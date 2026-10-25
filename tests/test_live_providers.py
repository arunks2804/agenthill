"""Live provider tests. Skipped unless explicitly enabled, because they need a running Ollama server or
real API keys. Cloud tests without keys are reported as SKIPPED (unverified), never as passed.

    AGENTHILL_LIVE_OLLAMA=1 OLLAMA_MODEL=llama3.1:8b pytest tests/test_live_providers.py
    ANTHROPIC_API_KEY=... AGENTHILL_LIVE_ANTHROPIC_MODEL=<model> pytest tests/test_live_providers.py
"""
import json
import os

import pytest

from agenthill import db as D
from agenthill import services as S
from agenthill.llm.base import ModelSpec
from agenthill.llm.providers import AnthropicProvider, GeminiProvider, OllamaProvider, OpenAIProvider
from agenthill.platform_settings import put_setting

OLLAMA = os.environ.get("AGENTHILL_LIVE_OLLAMA") == "1"
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.1:8b")


@pytest.mark.skipif(not OLLAMA, reason="set AGENTHILL_LIVE_OLLAMA=1 to run against a local Ollama server")
def test_ollama_agent_with_ollama_judge(db, it_agent):
    base = db.get(D.AgentConfig, it_agent.current_config_id)
    cfg = S.new_config_version(db, it_agent, base, {"system_prompt": base.system_prompt, "tools": base.tools,
                               "settings": {**base.settings, "model_provider": "ollama", "model": OLLAMA_MODEL, "max_tokens": 400}},
                               "test", "ollama", "experimental", ["ollama"])
    db.commit()
    put_setting(db, "acme", "model_roles", {"judge": {"provider": "ollama", "model": OLLAMA_MODEL, "max_tokens": 500}}, "ana")
    ds = db.query(D.Dataset).filter_by(agent_id=it_agent.id).one()
    small = D.DatasetVersion(dataset_id=ds.id, version=99, cases=[c for c in db.query(D.DatasetVersion).first().cases
                                                                   if c["case_key"] in ("IT-001", "IT-014")])
    db.add(small)
    db.commit()
    run = S.create_run(db, "acme", "ana", it_agent.id, cfg.id, small.id, background=False)
    db.expire_all()
    run = db.get(D.EvalRun, run.id)
    assert run.status == "completed", run.error
    assert run.models["agent"]["provider"] == "ollama" and run.models["same_model_agent_and_judge"] is True
    for r in db.query(D.CaseResult).filter_by(run_id=run.id):
        assert any(s["kind"] == "llm_call" for s in r.trace["spans"])
        assert r.cost_usd is None


def _cloud(cls, env_key, model_env):
    key, model = os.environ.get(env_key), os.environ.get(model_env)
    if not key or not model:
        pytest.skip(f"UNVERIFIED: {env_key} and {model_env} not set")
    r = cls(api_key=key).test_connection(model)
    assert r["ok"], json.dumps(r.get("error"))


def test_live_anthropic():
    _cloud(AnthropicProvider, "ANTHROPIC_API_KEY", "AGENTHILL_LIVE_ANTHROPIC_MODEL")


def test_live_openai():
    _cloud(OpenAIProvider, "OPENAI_API_KEY", "AGENTHILL_LIVE_OPENAI_MODEL")


def test_live_gemini():
    _cloud(GeminiProvider, "GEMINI_API_KEY", "AGENTHILL_LIVE_GEMINI_MODEL")


@pytest.mark.skipif(not os.environ.get("AGENTHILL_LIVE_NETWORK"), reason="set AGENTHILL_LIVE_NETWORK=1 to call real provider endpoints with invalid keys")
def test_invalid_keys_are_rejected_by_real_endpoints():
    for cls, key in ((OpenAIProvider, "sk-invalid-0000000000"), (AnthropicProvider, "sk-ant-invalid-000000"), (GeminiProvider, "AIzaInvalid000000000")):
        r = cls(api_key=key).test_connection()
        assert r["ok"] is False and r["error"]["code"] == "auth", (cls.name, r)
