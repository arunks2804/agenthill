"""Common model integration layer.

Every model-backed component (agent execution, LLM judge, failure-analysis narrative) talks to
models only through `Provider.chat()`. Providers register themselves in `registry.py`; adding a new
provider means adding one class there - the evaluation engine never imports a provider SDK.

Errors are normalised to `LLMError` with a stable `code` so the UI can explain them. A failing
provider is never silently replaced with another provider.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

ERROR_HINTS = {
    "unavailable": "The provider could not be reached. For Ollama, check the server URL and that `ollama serve` is running.",
    "model_not_found": "The model is not available. For Ollama run `ollama pull <model>`; for cloud providers check the model ID.",
    "auth": "The API key was rejected. Update it in Settings -> LLM Providers.",
    "missing_key": "No API key is configured for this provider. Add one in Settings -> LLM Providers or via environment variable.",
    "rate_limit": "The provider rate-limited the request. Wait and retry, or lower evaluation concurrency.",
    "access_denied": "The key is valid but has no access to this model.",
    "timeout": "The provider did not respond within the timeout.",
    "unsupported_structured_output": "The model did not return valid JSON for a structured request.",
    "context_window": "The request is larger than the model's configured context window.",
    "invalid_response": "The model response was empty, truncated or did not match the expected schema.",
    "bad_request": "The provider rejected the request.",
    "unknown_provider": "This provider is not registered.",
}


class LLMError(Exception):
    def __init__(self, code: str, message: str, provider: str = "", model: str = ""):
        super().__init__(message)
        self.code, self.message, self.provider, self.model = code, message, provider, model

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "provider": self.provider, "model": self.model,
                "hint": ERROR_HINTS.get(self.code, "")}


@dataclass
class ModelSpec:
    """Provider + model + parameters. Persisted with runs; never contains credentials."""
    provider: str
    model: str
    temperature: float = 0.0
    max_tokens: int = 800
    timeout_s: float = 60.0
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "ModelSpec":
        return cls(provider=d.get("provider") or d.get("model_provider") or "", model=d.get("model", ""),
                   temperature=float(d.get("temperature", 0.0)), max_tokens=int(d.get("max_tokens", 800)),
                   timeout_s=float(d.get("timeout_s", 60.0)), extra=dict(d.get("extra") or {}))

    def public(self) -> dict:
        return {"provider": self.provider, "model": self.model, "temperature": self.temperature,
                "max_tokens": self.max_tokens, "timeout_s": self.timeout_s}


@dataclass
class LLMResponse:
    text: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    provider: str
    model: str
    json_mode: str  # native | prompted | none
    parsed: dict | None = None


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def parse_json_object(text: str, required: list[str] | None = None, provider: str = "", model: str = "") -> dict:
    """Extract and validate the first JSON object in a model response."""
    if not text or not text.strip():
        raise LLMError("invalid_response", "Empty model response", provider, model)
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    cleaned = re.sub(r"^```(?:json)?|```$", "", cleaned.strip(), flags=re.M).strip()
    try:
        obj = json.loads(cleaned)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", cleaned, re.S)
        if not m:
            raise LLMError("unsupported_structured_output", f"No JSON object in response: {cleaned[:160]!r}", provider, model)
        try:
            obj = json.loads(m.group(0))
        except json.JSONDecodeError as e:
            raise LLMError("unsupported_structured_output", f"Malformed JSON ({e.msg}): {cleaned[:160]!r}", provider, model)
    if not isinstance(obj, dict):
        raise LLMError("invalid_response", "Expected a JSON object", provider, model)
    missing = [k for k in (required or []) if k not in obj]
    if missing:
        raise LLMError("invalid_response", f"JSON response missing keys: {missing}", provider, model)
    return obj


class Provider:
    name = ""
    label = ""
    requires_api_key = True
    supports_model_discovery = True
    local = False

    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        self.api_key = api_key
        self.base_url = base_url

    # --- to implement -------------------------------------------------------------------------------
    def _chat(self, spec: ModelSpec, system: str, messages: list[dict], json_mode: bool) -> LLMResponse:
        raise NotImplementedError

    def list_models(self) -> list[str]:
        raise NotImplementedError

    def context_window(self, model: str) -> int:
        return 32_000

    def native_json(self, model: str) -> bool:
        return False

    # --- shared ---------------------------------------------------------------------------------------
    def check_key(self) -> None:
        if self.requires_api_key and not self.api_key:
            raise LLMError("missing_key", f"No API key configured for {self.label or self.name}", self.name)

    def chat(self, spec: ModelSpec, system: str, messages: list[dict], json_mode: bool = False,
             required_keys: list[str] | None = None) -> LLMResponse:
        self.check_key()
        if not spec.model:
            raise LLMError("bad_request", "No model selected", self.name)
        prompt_tokens = estimate_tokens(system + "".join(m["content"] for m in messages))
        window = self.context_window(spec.model)
        if prompt_tokens + spec.max_tokens > window:
            raise LLMError("context_window", f"Request needs ~{prompt_tokens + spec.max_tokens} tokens; {spec.model} window is {window}",
                           self.name, spec.model)
        sys_prompt = system
        if json_mode and not self.native_json(spec.model):
            sys_prompt = system + "\n\nRespond with a single JSON object only. No prose, no markdown fences."
        t0 = time.perf_counter()
        resp = self._chat(spec, sys_prompt, messages, json_mode)
        resp.latency_ms = round((time.perf_counter() - t0) * 1000, 1)
        resp.json_mode = ("native" if self.native_json(spec.model) else "prompted") if json_mode else "none"
        if json_mode:
            resp.parsed = parse_json_object(resp.text, required_keys, self.name, spec.model)
        return resp

    def test_connection(self, model: str | None = None) -> dict:
        """Real round-trip: list models (if supported) and, if a model is given, a tiny JSON chat."""
        out: dict = {"provider": self.name, "ok": False, "models": [], "checks": []}
        try:
            self.check_key()
            if self.supports_model_discovery:
                out["models"] = self.list_models()
                out["checks"].append({"check": "model_discovery", "ok": True, "detail": f"{len(out['models'])} models"})
                if model and out["models"] and model not in out["models"] and not any(m.startswith(model) for m in out["models"]):
                    raise LLMError("model_not_found", f"Model {model!r} is not available from {self.label}", self.name, model)
            if model:
                r = self.chat(ModelSpec(self.name, model, max_tokens=60, timeout_s=60), "You are a connectivity test.",
                              [{"role": "user", "content": 'Reply with {"status": "ok"}'}], json_mode=True, required_keys=["status"])
                out["checks"].append({"check": "structured_output", "ok": True, "detail": f"{r.json_mode} JSON in {r.latency_ms} ms"})
                out["json_mode"] = r.json_mode
                out["latency_ms"] = r.latency_ms
            out["ok"] = True
        except LLMError as e:
            out["error"] = e.as_dict()
        return out
