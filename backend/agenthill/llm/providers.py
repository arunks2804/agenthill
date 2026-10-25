"""Provider implementations: Ollama (REST), Anthropic, OpenAI and Google Gemini (official SDKs)."""
from __future__ import annotations

import httpx

from .base import LLMError, LLMResponse, ModelSpec, Provider

# Conservative context windows used for pre-flight checks when the provider does not report one.
CONTEXT_WINDOWS = [("claude", 200_000), ("gpt-4.1", 1_000_000), ("gpt-4o", 128_000), ("gpt-5", 400_000),
                   ("o3", 200_000), ("o4", 200_000), ("gemini", 1_000_000)]


def _window(model: str, default: int = 32_000) -> int:
    for prefix, size in CONTEXT_WINDOWS:
        if model.lower().startswith(prefix):
            return size
    return default


class OllamaProvider(Provider):
    name, label = "ollama", "Ollama (local)"
    requires_api_key = False
    local = True
    DEFAULT_URL = "http://localhost:11434"
    NUM_CTX = 8192

    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        super().__init__(None, (base_url or self.DEFAULT_URL).rstrip("/"))

    def _request(self, method: str, path: str, timeout: float, **kw) -> dict:
        try:
            r = httpx.request(method, f"{self.base_url}{path}", timeout=timeout, **kw)
        except httpx.TimeoutException:
            raise LLMError("timeout", f"Ollama at {self.base_url} timed out after {timeout:.0f}s", self.name)
        except httpx.HTTPError as e:
            raise LLMError("unavailable", f"Cannot reach Ollama at {self.base_url}: {e.__class__.__name__}", self.name)
        if r.status_code == 404:
            raise LLMError("model_not_found", r.json().get("error", "model not found") if r.headers.get("content-type", "").startswith("application/json") else "not found", self.name)
        if r.status_code >= 400:
            raise LLMError("bad_request", f"Ollama HTTP {r.status_code}: {r.text[:200]}", self.name)
        return r.json()

    def list_models(self) -> list[str]:
        return [m["name"] for m in self._request("GET", "/api/tags", 10).get("models", [])]

    def context_window(self, model: str) -> int:
        return self.NUM_CTX

    def native_json(self, model: str) -> bool:
        return True

    def _chat(self, spec: ModelSpec, system: str, messages: list[dict], json_mode: bool) -> LLMResponse:
        body = {"model": spec.model, "stream": False, "messages": [{"role": "system", "content": system}, *messages],
                "options": {"temperature": spec.temperature, "num_predict": spec.max_tokens, "num_ctx": self.NUM_CTX}}
        if json_mode:
            body["format"] = "json"
        if spec.model.startswith(("qwen3", "deepseek-r1")):
            body["think"] = False
        try:
            data = self._request("POST", "/api/chat", spec.timeout_s, json=body)
        except LLMError as e:
            e.model = spec.model
            raise
        text = (data.get("message") or {}).get("content", "")
        if data.get("done_reason") == "length" and json_mode:
            raise LLMError("invalid_response", "Response truncated at max_tokens", self.name, spec.model)
        return LLMResponse(text, int(data.get("prompt_eval_count") or 0), int(data.get("eval_count") or 0), 0, self.name, spec.model, "none")


class AnthropicProvider(Provider):
    name, label = "anthropic", "Anthropic Claude"

    def _client(self, timeout: float):
        import anthropic
        kw = {"api_key": self.api_key, "timeout": timeout, "max_retries": 1}
        if self.base_url:
            kw["base_url"] = self.base_url
        return anthropic.Anthropic(**kw)

    def _map(self, e: Exception, model: str = "") -> LLMError:
        import anthropic as a
        table = [(a.AuthenticationError, "auth"), (a.PermissionDeniedError, "access_denied"), (a.RateLimitError, "rate_limit"),
                 (a.NotFoundError, "model_not_found"), (a.APITimeoutError, "timeout"), (a.APIConnectionError, "unavailable"),
                 (a.BadRequestError, "bad_request")]
        for cls, code in table:
            if isinstance(e, cls):
                msg = str(e)
                if code == "bad_request" and ("context" in msg.lower() or "too long" in msg.lower()):
                    code = "context_window"
                return LLMError(code, msg[:300], self.name, model)
        return LLMError("bad_request", str(e)[:300], self.name, model)

    def list_models(self) -> list[str]:
        try:
            return [m.id for m in self._client(20).models.list(limit=100)]
        except Exception as e:  # noqa: BLE001 - normalised below
            raise self._map(e)

    def context_window(self, model: str) -> int:
        return _window(model, 200_000)

    def _chat(self, spec: ModelSpec, system: str, messages: list[dict], json_mode: bool) -> LLMResponse:
        try:
            r = self._client(spec.timeout_s).messages.create(model=spec.model, max_tokens=spec.max_tokens, system=system,
                                                             messages=messages, temperature=spec.temperature)
        except Exception as e:  # noqa: BLE001
            raise self._map(e, spec.model)
        text = "".join(getattr(b, "text", "") for b in r.content)
        if r.stop_reason == "max_tokens" and json_mode:
            raise LLMError("invalid_response", "Response truncated at max_tokens", self.name, spec.model)
        return LLMResponse(text, r.usage.input_tokens, r.usage.output_tokens, 0, self.name, spec.model, "none")


class OpenAIProvider(Provider):
    name, label = "openai", "OpenAI"

    def _client(self, timeout: float):
        import openai
        kw = {"api_key": self.api_key, "timeout": timeout, "max_retries": 1}
        if self.base_url:
            kw["base_url"] = self.base_url
        return openai.OpenAI(**kw)

    def _map(self, e: Exception, model: str = "") -> LLMError:
        import openai as o
        table = [(o.AuthenticationError, "auth"), (o.PermissionDeniedError, "access_denied"), (o.RateLimitError, "rate_limit"),
                 (o.NotFoundError, "model_not_found"), (o.APITimeoutError, "timeout"), (o.APIConnectionError, "unavailable"),
                 (o.BadRequestError, "bad_request")]
        for cls, code in table:
            if isinstance(e, cls):
                msg = str(e)
                if code == "bad_request" and "context_length" in msg:
                    code = "context_window"
                elif code == "bad_request" and "response_format" in msg:
                    code = "unsupported_structured_output"
                return LLMError(code, msg[:300], self.name, model)
        return LLMError("bad_request", str(e)[:300], self.name, model)

    def list_models(self) -> list[str]:
        try:
            return sorted(m.id for m in self._client(20).models.list())
        except Exception as e:  # noqa: BLE001
            raise self._map(e)

    def context_window(self, model: str) -> int:
        return _window(model, 128_000)

    def native_json(self, model: str) -> bool:
        return True

    def _chat(self, spec: ModelSpec, system: str, messages: list[dict], json_mode: bool) -> LLMResponse:
        kw = {"model": spec.model, "messages": [{"role": "system", "content": system}, *messages], "max_completion_tokens": spec.max_tokens}
        if not spec.model.startswith(("o1", "o3", "o4", "gpt-5")):
            kw["temperature"] = spec.temperature
        if json_mode:
            kw["response_format"] = {"type": "json_object"}
        try:
            r = self._client(spec.timeout_s).chat.completions.create(**kw)
        except Exception as e:  # noqa: BLE001
            raise self._map(e, spec.model)
        choice = r.choices[0]
        if choice.finish_reason == "length" and json_mode:
            raise LLMError("invalid_response", "Response truncated at max_tokens", self.name, spec.model)
        u = r.usage
        return LLMResponse(choice.message.content or "", getattr(u, "prompt_tokens", 0), getattr(u, "completion_tokens", 0), 0,
                           self.name, spec.model, "none")


class GeminiProvider(Provider):
    name, label = "gemini", "Google Gemini"

    def _client(self, timeout: float):
        from google import genai
        from google.genai import types
        return genai.Client(api_key=self.api_key, http_options=types.HttpOptions(timeout=int(timeout * 1000)))

    def _map(self, e: Exception, model: str = "") -> LLMError:
        from google.genai import errors
        msg = str(e)[:300]
        if isinstance(e, errors.APIError):
            code = getattr(e, "code", 0)
            mapped = {400: "auth" if "API key" in msg else "bad_request", 401: "auth", 403: "access_denied",
                      404: "model_not_found", 429: "rate_limit", 504: "timeout"}.get(code, "bad_request")
            return LLMError(mapped, msg, self.name, model)
        if "timed out" in msg.lower() or "timeout" in e.__class__.__name__.lower():
            return LLMError("timeout", msg, self.name, model)
        if "connect" in msg.lower():
            return LLMError("unavailable", msg, self.name, model)
        return LLMError("bad_request", msg, self.name, model)

    def list_models(self) -> list[str]:
        try:
            out = []
            client = self._client(20)  # keep a reference: the SDK closes the transport when the client is collected
            for m in client.models.list():
                actions = getattr(m, "supported_actions", None) or []
                if not actions or "generateContent" in actions:
                    out.append(m.name.removeprefix("models/"))
            return sorted(out)
        except Exception as e:  # noqa: BLE001
            raise self._map(e)

    def context_window(self, model: str) -> int:
        return _window(model, 1_000_000)

    def native_json(self, model: str) -> bool:
        return True

    def _chat(self, spec: ModelSpec, system: str, messages: list[dict], json_mode: bool) -> LLMResponse:
        from google.genai import types
        contents = [types.Content(role="model" if m["role"] == "assistant" else "user", parts=[types.Part(text=m["content"])])
                    for m in messages]
        cfg = types.GenerateContentConfig(system_instruction=system, temperature=spec.temperature, max_output_tokens=spec.max_tokens,
                                          response_mime_type="application/json" if json_mode else None)
        try:
            client = self._client(spec.timeout_s)
            r = client.models.generate_content(model=spec.model, contents=contents, config=cfg)
        except Exception as e:  # noqa: BLE001
            raise self._map(e, spec.model)
        u = getattr(r, "usage_metadata", None)
        return LLMResponse(r.text or "", getattr(u, "prompt_token_count", 0) or 0, getattr(u, "candidates_token_count", 0) or 0, 0,
                           self.name, spec.model, "none")
