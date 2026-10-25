"""Evaluation Engine: runs an agent configuration against a dataset version and persists results.

Pipeline per case: Agent Execution Service -> deterministic checks -> semantic judge -> scoring ->
redaction -> persistence. Provider-specific code is confined to the model layer; this module only
sees `Provider` objects and normalised `LLMError`s.
"""
from __future__ import annotations

import json
import logging
import threading
import time

from ..db import Agent, AgentConfig, AuditEvent, CaseResult, DatasetVersion, EvalRun, SessionLocal, utcnow
from ..gates.engine import evaluate_gates
from ..llm.base import LLMError, ModelSpec
from ..llm.registry import DETERMINISTIC, SIMLM, cost_for
from ..platform_settings import CREDENTIALS, get_setting, provider_for, role_spec
from ..runtime.agent import execute_case
from ..runtime.redaction import redact
from . import deterministic, metrics
from .judges import LLMJudge, RubricJudge

log = logging.getLogger("agenthill.eval")
FATAL_CODES = {"unavailable", "auth", "missing_key", "model_not_found", "access_denied", "unknown_provider", "context_window"}


def config_dict(cfg: AgentConfig) -> dict:
    return {"id": cfg.id, "version": cfg.version, "label": cfg.label, "system_prompt": cfg.system_prompt,
            "tools": cfg.tools, "settings": cfg.settings}


def scrub(obj, secrets: list[str]):
    """Redact PII patterns and any configured credential value."""
    text = json.dumps(redact(obj))
    for s in secrets:
        if s and len(s) > 6:
            text = text.replace(s, "[REDACTED-SECRET]")
    return json.loads(text)


def build_judge(db, tenant_id: str):
    spec = role_spec(db, tenant_id, "judge")
    if spec.provider in (DETERMINISTIC, ""):
        return RubricJudge()
    return LLMJudge(provider_for(db, tenant_id, spec.provider), spec)


def agent_model_spec(cfg: AgentConfig) -> ModelSpec:
    s = cfg.settings or {}
    return ModelSpec(provider=s.get("model_provider", SIMLM), model=s.get("model", "simlm-1"), temperature=float(s.get("temperature", 0.0)),
                     max_tokens=int(s.get("max_tokens", 500)), timeout_s=float(s.get("timeout_s", 90)))


def model_metadata(cfg: AgentConfig, judge) -> dict:
    agent = agent_model_spec(cfg).public()
    j = judge.spec()
    same = agent["provider"] == j.get("provider") and agent["model"] == j.get("model")
    return {"agent": {**agent, "settings": {k: v for k, v in (cfg.settings or {}).items() if k not in ("model_provider", "model")}},
            "judge": j, "same_model_agent_and_judge": same,
            "bias_note": ("The same model executes and judges this run; self-preference bias is possible. "
                          "Deterministic checks are unaffected." if same else None)}


def evaluate_case(case: dict, env_name: str, tenant_id: str, cfg: dict, judge, criteria: dict, provider, pricing: dict) -> dict:
    spec = ModelSpec.from_dict({"provider": cfg["settings"].get("model_provider", SIMLM), "model": cfg["settings"].get("model", "")})
    exec_ = execute_case(env_name, tenant_id, cfg, case, provider=provider)
    checks = deterministic.evaluate(case, env_name, exec_)
    threshold = float(criteria.get("judge_pass_threshold", 0.6))
    j = judge.score(case, exec_, threshold)
    applicable = [c for c in checks if c["status"] != "skip"]
    det_score = sum(c["status"] == "pass" for c in applicable) / len(applicable) if applicable else 1.0
    w = criteria.get("weights", {"deterministic": 0.6, "judge": 0.4})
    det_pass = all(c["status"] != "fail" for c in checks)
    if j.get("error"):
        status, passed, score = "error", False, round(det_score * w["deterministic"], 4)
    else:
        passed = det_pass and j["passed"]
        status = "passed" if passed else "failed"
        score = round(w["deterministic"] * det_score + w["judge"] * j["overall"], 4)
    critical = any(c["status"] == "fail" and c["severity"] == "critical" for c in checks)
    # Disputed or uncertain semantic verdicts are routed to human review.
    needs_review = bool(j.get("error")) or j.get("confidence") == "low" or (
        j.get("overall") is not None and abs(j["overall"] - threshold) < 0.08) or (det_pass and j.get("overall") is not None and not j["passed"])
    if spec.provider == SIMLM:
        cost = exec_["cost_usd"]
    else:
        cost = cost_for(spec.provider, spec.model, exec_.get("input_tokens", 0), exec_.get("output_tokens", 0), pricing)["usd"]
    return {"exec": exec_, "checks": checks, "judge": j, "passed": passed, "status": status, "score": score,
            "critical": critical, "needs_review": needs_review, "cost": cost}


def execute_run(run_id: int) -> None:
    db = SessionLocal()
    run = db.get(EvalRun, run_id)
    try:
        agent = db.get(Agent, run.agent_id)
        cfg = db.get(AgentConfig, run.config_id)
        dv = db.get(DatasetVersion, run.dataset_version_id)
        run.status, run.started_at = "running", utcnow()
        run.progress = {"done": 0, "total": len(dv.cases)}
        db.commit()
        t0 = time.perf_counter()
        judge = build_judge(db, run.tenant_id)
        run.models = model_metadata(cfg, judge)
        db.commit()
        aspec = agent_model_spec(cfg)
        provider = None if aspec.provider == SIMLM else provider_for(db, run.tenant_id, aspec.provider)
        pricing = get_setting(db, run.tenant_id, "pricing")
        secrets = CREDENTIALS.all_secrets()
        cdict = config_dict(cfg)
        for i, case in enumerate(dv.cases):
            try:
                r = evaluate_case(case, agent.environment, run.tenant_id, cdict, judge, agent.evaluation_criteria, provider, pricing)
                if r["judge"].get("error") and r["judge"]["error"]["code"] in FATAL_CODES:
                    raise LLMError(r["judge"]["error"]["code"], "Judge model failed: " + r["judge"]["error"]["message"],
                                   r["judge"]["error"]["provider"], r["judge"]["error"]["model"])
                e = r["exec"]
                db.add(CaseResult(
                    run_id=run.id, case_key=case["case_key"], case=case, passed=r["passed"], status=r["status"], outcome=e["outcome"],
                    response=scrub(e["response"], secrets), checks=r["checks"], judge=scrub(r["judge"], secrets), score=r["score"],
                    critical=r["critical"], tool_calls=scrub(e["tool_calls"], secrets), retrieved=e["retrieved"],
                    state_changes=scrub(e["state_changes"], secrets), latency_ms=e["latency_ms"], tokens=e["tokens"],
                    cost_usd=r["cost"], trace=scrub(e["trace"], secrets), needs_review=r["needs_review"]))
            except LLMError as err:
                if err.code in FATAL_CODES:
                    raise
                db.add(CaseResult(run_id=run.id, case_key=case["case_key"], case=case, passed=False, status="error", outcome="error",
                                  response="", checks=[], judge={"error": err.as_dict()}, score=0.0, critical=False, tool_calls=[],
                                  retrieved=[], state_changes=[], latency_ms=0.0, tokens=0, cost_usd=None,
                                  trace={"spans": [], "error": err.as_dict()}, needs_review=True))
            run.progress = {"done": i + 1, "total": len(dv.cases)}
            db.commit()
        results = db.query(CaseResult).filter_by(run_id=run.id).all()
        run.metrics = metrics.compute(results)
        run.metrics["evaluation_duration_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        run.gate_verdict = evaluate_gates(run.metrics, agent.quality_gates or {})
        run.status = "completed"
    except LLMError as err:
        log.warning("run %s failed: %s", run_id, err.code)
        run.status, run.error = "failed", json.dumps(err.as_dict())
    except Exception as err:  # noqa: BLE001 - recorded on the run, surfaced in UI
        log.exception("run %s crashed", run_id)
        run.status, run.error = "failed", json.dumps({"code": "internal", "message": str(err)[:400]})
    run.finished_at = utcnow()
    if run.started_at:
        run.duration_ms = round((run.finished_at - run.started_at).total_seconds() * 1000, 1)
    db.add(AuditEvent(tenant_id=run.tenant_id, actor=run.created_by or "system", action=f"run.{run.status}", entity_type="eval_run",
                      entity_id=str(run.id), details={"metrics": {k: run.metrics.get(k) for k in ("task_success_rate", "critical_failures")}
                                                      if run.metrics else {}, "error": run.error}))
    db.commit()
    db.close()


def start_run(run_id: int, background: bool = True) -> None:
    if background:
        threading.Thread(target=execute_run, args=(run_id,), daemon=True).start()
    else:
        execute_run(run_id)
