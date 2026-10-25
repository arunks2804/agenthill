"""Tenant-scoped platform settings: provider endpoints, model roles and model pricing. Never credentials."""
from __future__ import annotations

from .config import DEFAULT_OLLAMA_URL, VAR_DIR
from .db import AuditEvent, PlatformSetting, utcnow
from .llm.base import ModelSpec
from .llm.credentials import CredentialStore
from .llm.registry import DETERMINISTIC, build_provider

CREDENTIALS = CredentialStore(VAR_DIR)

DEFAULTS = {
    "providers": {"ollama": {"base_url": DEFAULT_OLLAMA_URL}, "anthropic": {}, "openai": {}, "gemini": {}},
    "model_roles": {
        "judge": {"provider": DETERMINISTIC, "model": "rubric-v1", "temperature": 0.0, "max_tokens": 600},
        "analyzer": {"provider": DETERMINISTIC, "model": "evidence-rules-v1", "temperature": 0.0, "max_tokens": 900},
    },
    "pricing": {},
}


def get_setting(db, tenant_id: str, key: str) -> dict:
    row = db.get(PlatformSetting, (tenant_id, key))
    base = DEFAULTS[key]
    if row is None:
        return {k: (dict(v) if isinstance(v, dict) else v) for k, v in base.items()}
    merged = {k: (dict(v) if isinstance(v, dict) else v) for k, v in base.items()}
    merged.update(row.value)
    return merged


def put_setting(db, tenant_id: str, key: str, value: dict, actor: str) -> dict:
    row = db.get(PlatformSetting, (tenant_id, key))
    if row is None:
        row = PlatformSetting(tenant_id=tenant_id, key=key, value=value, updated_by=actor)
        db.add(row)
    else:
        row.value, row.updated_by, row.updated_at = value, actor, utcnow()
    db.add(AuditEvent(tenant_id=tenant_id, actor=actor, action=f"settings.{key}.updated", entity_type="settings", entity_id=key,
                      details={"value": value}))
    db.commit()
    return get_setting(db, tenant_id, key)


def provider_for(db, tenant_id: str, name: str):
    return build_provider(name, get_setting(db, tenant_id, "providers"), CREDENTIALS)


def role_spec(db, tenant_id: str, role: str) -> ModelSpec:
    return ModelSpec.from_dict(get_setting(db, tenant_id, "model_roles")[role])
