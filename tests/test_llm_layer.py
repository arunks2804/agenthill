"""Model-agnostic evaluation: a scripted fake provider exercises the same LLM agent, judge and analyzer
code paths that Ollama / Claude / OpenAI / Gemini use - without network access."""
import json

import pytest

from agenthill import db as D
from agenthill import services as S
from agenthill.llm.base import LLMError, LLMResponse, ModelSpec, Provider, parse_json_object
from agenthill.llm.registry import PROVIDERS, register_provider


class FakeProvider(Provider):
    name, label = "fake", "Fake (test)"
    requires_api_key = False
    fail_with: str | None = None

    def list_models(self):
        return ["fake-agent", "fake-judge"]

    def native_json(self, model):
        return True

    def _chat(self, spec, system, messages, json_mode):
        if FakeProvider.fail_with:
            raise LLMError(FakeProvider.fail_with, "scripted failure", self.name, spec.model)
        last = messages[-1]["content"]
        if "Score the AGENT RESPONSE" in system:
            score = 0.9 if "VPN" in last or "sign-in" in last else 0.4
            body = {c: {"score": score, "rationale": "scripted"} for c in ("correctness", "relevance", "completeness", "grounding", "uncertainty")}
            body["confidence"] = "high"
        elif "failure-analysis assistant" in system:
            body = {"hypothesis": "Scripted review agrees.", "confidence": "high", "supporting_evidence_ids": ["E1"]}
        elif "Request:" in last and "VPN" in last:
            body = {"action": "call_tool", "tool": "check_vpn_status", "args": {"employee_id": "E1001"}, "reason": "diagnose"}
        elif "tool:check_vpn_status" in last:
            body = {"action": "call_tool", "tool": "reissue_vpn_certificate", "args": {"employee_id": "E1001"}, "reason": "expired"}
        elif "tool:reissue_vpn_certificate" in last:
            body = {"action": "final", "response": "A new VPN certificate has been issued. Please reconnect.", "outcome": "resolved"}
        else:
            body = {"action": "final", "response": "I have escalated this.", "outcome": "resolved"}
        return LLMResponse(json.dumps(body), 100, 20, 0, self.name, spec.model, "none")


@pytest.fixture(autouse=True)
def _fake():
    register_provider(FakeProvider)
    FakeProvider.fail_with = None
    yield
    PROVIDERS.pop("fake", None)


def _config(db, agent, provider="fake", model="fake-agent"):
    base = db.get(D.AgentConfig, agent.current_config_id)
    new = {"system_prompt": base.system_prompt, "tools": base.tools, "settings": {**base.settings, "model_provider": provider, "model": model}}
    c = S.new_config_version(db, agent, base, new, "leo", f"{provider}:{model}", "experimental", ["model change"])
    db.commit()
    return c


def test_parse_json_variants():
    assert parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_object('<think>hmm</think> sure: {"a": 2} done') == {"a": 2}
    with pytest.raises(LLMError) as e:
        parse_json_object("no json here")
    assert e.value.code == "unsupported_structured_output"
    with pytest.raises(LLMError) as e:
        parse_json_object('{"a": 1}', required=["b"])
    assert e.value.code == "invalid_response"


def test_context_window_preflight():
    class Tiny(FakeProvider):
        def context_window(self, model):
            return 50
    with pytest.raises(LLMError) as e:
        Tiny().chat(ModelSpec("fake", "m", max_tokens=40), "x" * 400, [{"role": "user", "content": "hi"}])
    assert e.value.code == "context_window"


def test_llm_agent_and_llm_judge_use_same_pipeline(db, it_agent):
    cfg = _config(db, it_agent)
    from agenthill.platform_settings import put_setting
    put_setting(db, "acme", "model_roles", {"judge": {"provider": "fake", "model": "fake-judge"},
                                            "analyzer": {"provider": "fake", "model": "fake-judge"}}, "ana")
    dv = db.query(D.DatasetVersion).first()
    run = S.create_run(db, "acme", "ana", it_agent.id, cfg.id, dv.id, background=False)
    db.expire_all()
    run = db.get(D.EvalRun, run.id)
    assert run.status == "completed"
    assert run.models["agent"]["provider"] == "fake" and run.models["judge"]["provider"] == "fake"
    assert run.models["same_model_agent_and_judge"] is False
    r1 = db.query(D.CaseResult).filter_by(run_id=run.id, case_key="IT-001").one()
    assert [c["tool"] for c in r1.tool_calls] == ["check_vpn_status", "reissue_vpn_certificate"]
    assert r1.passed and r1.judge["judge"]["model"] == "fake-judge"
    assert any(s["kind"] == "llm_call" for s in r1.trace["spans"])
    assert r1.cost_usd is None  # no price configured: cost is not invented
    fa = S.analyze_run(db, "acme", "ana", run.id)
    assert fa.analyzer["narrative_model"]["provider"] == "fake" and fa.analyzer["error"] is None
    assert any(c.get("llm_review", {}).get("status") == "accepted" for c in fa.clusters)


def test_same_model_bias_is_flagged(db, it_agent):
    cfg = _config(db, it_agent, model="fake-judge")
    from agenthill.platform_settings import put_setting
    put_setting(db, "acme", "model_roles", {"judge": {"provider": "fake", "model": "fake-judge"}}, "ana")
    dv = db.query(D.DatasetVersion).first()
    run = S.create_run(db, "acme", "ana", it_agent.id, cfg.id, dv.id, background=False)
    db.expire_all()
    assert db.get(D.EvalRun, run.id).models["same_model_agent_and_judge"] is True


def test_provider_failure_fails_run_without_substitution(db, it_agent):
    cfg = _config(db, it_agent)
    FakeProvider.fail_with = "auth"
    dv = db.query(D.DatasetVersion).first()
    run = S.create_run(db, "acme", "ana", it_agent.id, cfg.id, dv.id, background=False)
    db.expire_all()
    run = db.get(D.EvalRun, run.id)
    assert run.status == "failed" and json.loads(run.error)["code"] == "auth"
    assert db.query(D.CaseResult).filter_by(run_id=run.id).count() == 0  # nothing was silently produced by another model


def test_model_comparison_experiment(db, it_agent):
    base = db.get(D.AgentConfig, it_agent.current_config_id)
    patches = [{"type": "setting", "target": "model_provider", "before": "simlm", "after": "fake"},
               {"type": "setting", "target": "model", "before": "simlm-1", "after": "fake-agent"}]
    e = S.create_experiment(db, "acme", "leo", it_agent.id, "simlm vs fake", patches=patches, kind="model_comparison", baseline_config_id=base.id)
    S.approve_experiment(db, "acme", "ana", e.id)
    e = S.run_experiment(db, "acme", "leo", e.id, background=False)
    assert e.status == "completed"
    assert e.comparison["models"]["baseline"]["agent"]["provider"] == "simlm"
    assert e.comparison["models"]["candidate"]["agent"]["provider"] == "fake"
    assert any("Agent model differs" in n for n in e.comparison["fairness"]["notes"])
    assert "tokens_total" in e.comparison["metrics"]
