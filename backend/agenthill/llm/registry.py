"""Provider registry and model-role resolution.

Model roles:
  * agent      - stored per agent configuration version (settings.model_provider / settings.model)
  * judge      - platform setting; "deterministic" = built-in rubric judge
  * analyzer   - platform setting; "deterministic" = evidence rules only (LLM adds a narrative layer)

To add a provider: implement `Provider` and call `register_provider(MyProvider)`. Nothing in the
evaluation engine changes.
"""
from __future__ import annotations

from .base import LLMError, Provider
from .providers import AnthropicProvider, GeminiProvider, OllamaProvider, OpenAIProvider

PROVIDERS: dict[str, type[Provider]] = {}
DETERMINISTIC = "deterministic"
SIMLM = "simlm"


def register_provider(cls: type[Provider]) -> None:
    PROVIDERS[cls.name] = cls


for _cls in (OllamaProvider, AnthropicProvider, OpenAIProvider, GeminiProvider):
    register_provider(_cls)


def provider_catalog() -> list[dict]:
    return [{"name": c.name, "label": c.label, "requires_api_key": c.requires_api_key, "local": c.local,
             "supports_model_discovery": c.supports_model_discovery} for c in PROVIDERS.values()]


def build_provider(name: str, provider_settings: dict, credentials) -> Provider:
    cls = PROVIDERS.get(name)
    if cls is None:
        raise LLMError("unknown_provider", f"Provider {name!r} is not registered", name)
    cfg = (provider_settings or {}).get(name, {})
    return cls(api_key=credentials.get(name) if cls.requires_api_key else None, base_url=cfg.get("base_url") or None)


def cost_for(provider: str, model: str, in_tok: int, out_tok: int, pricing: dict) -> dict:
    """Return {usd, basis}. Cost is only computed from prices the user configured in Settings."""
    if provider == SIMLM:
        return {"usd": None, "basis": "modeled (SimLM reference model)"}
    if provider == "ollama":
        return {"usd": None, "basis": "local inference - resource cost not measured"}
    p = (pricing or {}).get(f"{provider}:{model}")
    if not p:
        return {"usd": None, "basis": "no price configured for this model"}
    usd = in_tok / 1e6 * float(p.get("input_per_mtok", 0)) + out_tok / 1e6 * float(p.get("output_per_mtok", 0))
    return {"usd": round(usd, 6), "basis": "configured price per million tokens"}
