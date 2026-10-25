from types import SimpleNamespace

import pytest

from agenthill import db as D
from agenthill import services as S
from agenthill.gates.engine import detect_regressions, evaluate_gates


def _approve_top(db, run_id, n=3):
    fa = S.analyze_run(db, "acme", "ana", run_id)
    recs = db.query(D.Recommendation).filter_by(analysis_id=fa.id).order_by(D.Recommendation.priority_score.desc()).all()
    chosen = [r for r in recs if r.proposed_change["patches"]][:n]
    for r in chosen:
        S.review_recommendation(db, "acme", "maya", r.id, "approve", "ok")
    return fa, chosen


def test_baseline_metrics_come_from_results(db, baseline_run):
    res = db.query(D.CaseResult).filter_by(run_id=baseline_run.id).all()
    m = baseline_run.metrics
    assert baseline_run.status == "completed" and m["total_cases"] == len(res) == 19
    assert m["passed"] == sum(r.passed for r in res)
    assert m["critical_failures"] == sum(r.critical for r in res) > 0
    assert baseline_run.gate_verdict["verdict"] == "FAIL"
    assert baseline_run.models["agent"]["provider"] == "simlm" and baseline_run.models["judge"]["provider"] == "deterministic"


def test_failure_classification_matches_human_labels(db, baseline_run):
    fa = S.analyze_run(db, "acme", "ana", baseline_run.id)
    acc = fa.summary["labeled_accuracy"]
    assert acc["labeled_cases"] == 13 and acc["primary_accuracy"] >= 0.9
    for c in fa.clusters:
        assert c["evidence"] and c["hypothesis"] and c["confidence"] in ("high", "medium", "low")
        assert all("result_id" in e for e in c["evidence"])
    cats = {c["category"] for c in fa.clusters}
    assert {"prompt_injection", "incorrect_tool_selection", "workflow_sequencing", "tool_execution_failure", "retrieval_failure"} <= cats


def test_recommendations_are_prioritized_with_patches(db, baseline_run):
    fa = S.analyze_run(db, "acme", "ana", baseline_run.id)
    recs = db.query(D.Recommendation).filter_by(analysis_id=fa.id).order_by(D.Recommendation.priority_score.desc()).all()
    assert recs[0].category == "prompt_injection" and recs[0].priority == "P1"
    assert all(r.status == "proposed" for r in recs)
    tool_rec = next(r for r in recs if r.change_type == "tool_description")
    assert tool_rec.proposed_change["patches"][0]["target"] == "reset_password"
    for r in recs:
        for k in ("failure_description", "evidence", "root_cause", "expected_impact", "confidence", "priority", "affected_cases"):
            assert getattr(r, k)


def test_experiment_requires_approvals(db, it_agent, baseline_run):
    fa = S.analyze_run(db, "acme", "ana", baseline_run.id)
    rec = db.query(D.Recommendation).filter_by(analysis_id=fa.id).first()
    with pytest.raises(S.ServiceError):
        S.create_experiment(db, "acme", "leo", it_agent.id, "x", [rec.id])  # recommendation not approved
    S.review_recommendation(db, "acme", "maya", rec.id, "approve")
    e = S.create_experiment(db, "acme", "leo", it_agent.id, "x", [rec.id])
    with pytest.raises(S.ServiceError):
        S.run_experiment(db, "acme", "leo", e.id, background=False)  # not approved for execution
    with pytest.raises(S.ServiceError) as err:
        S.approve_experiment(db, "acme", "leo", e.id)  # ML engineer cannot approve
    assert err.value.status == 403


def test_full_loop_fixes_failures_without_regressions(db, it_agent, baseline_run):
    base_cfg = it_agent.current_config_id
    _, chosen = _approve_top(db, baseline_run.id)
    e = S.create_experiment(db, "acme", "leo", it_agent.id, "fix", [r.id for r in chosen])
    assert db.get(D.AgentConfig, e.candidate_config_id).parent_id == base_cfg  # baseline preserved
    S.approve_experiment(db, "acme", "maya", e.id)
    e = S.run_experiment(db, "acme", "leo", e.id, background=False)
    assert e.status == "completed"
    cmp = e.comparison
    assert len(cmp["fixed"]) >= 10 and cmp["regressed"] == []
    assert cmp["metrics"]["task_success_rate"]["candidate"] > cmp["metrics"]["task_success_rate"]["baseline"]
    assert e.gate_verdict["verdict"] == "PASS" and "not prove production readiness" in e.gate_verdict["disclaimer"]
    with pytest.raises(S.ServiceError):
        S.decide_experiment(db, "acme", "ana", e.id, "accept")  # only the PM decides
    S.decide_experiment(db, "acme", "maya", e.id, "accept", "ship it")
    db.expire_all()
    agent = db.get(D.Agent, it_agent.id)
    assert agent.current_config_id == e.candidate_config_id
    assert all(db.get(D.Recommendation, r.id).status == "validated" for r in chosen)
    h = S.history(db, "acme", "fix")[0]
    assert h["decision"] == "accept" and h["approvals"]["decision"]["by"] == "maya" and h["original_failures"]


def test_regression_detection_and_gates():
    mk = lambda key, passed: SimpleNamespace(case_key=key, passed=passed, outcome="x", score=1.0 if passed else 0.0, id=1,
                                             checks=[], case={"category": "normal", "severity": "high"})
    reg = detect_regressions([mk("A", True), mk("B", False), mk("C", True)], [mk("A", False), mk("B", True), mk("C", True)])
    assert reg["regressed"] == ["A"] and reg["fixed"] == ["B"] and reg["still_passing"] == ["C"]
    gates = {"min_task_success_rate": 0.8, "max_critical_failures": 0, "max_regressions": 0, "max_cost_per_run_usd": 1}
    v = evaluate_gates({"task_success_rate": 0.9, "critical_failures": 0, "cost_usd_total": None}, gates, regressions=1)
    assert v["verdict"] == "FAIL" and any(g["gate"] == "max_regressions" and g["status"] == "fail" for g in v["gates"])
    assert any(g["status"] == "not_measured" for g in v["gates"])
    v2 = evaluate_gates({"task_success_rate": 0.9, "critical_failures": 0, "cost_usd_total": 0.1}, gates, regressions=0)
    assert v2["verdict"] == "PASS"


def test_confirmed_failure_becomes_regression_test(db, it_agent, baseline_run):
    S.analyze_run(db, "acme", "ana", baseline_run.id)
    failed = db.query(D.CaseResult).filter_by(run_id=baseline_run.id, case_key="IT-003").one()
    out = S.convert_to_regression(db, "acme", "ana", failed.id, "confirmed")
    assert out["case_key"] == "REG-IT-003" and out["origin"]["diagnosis"] == "incorrect_tool_selection"
    with pytest.raises(S.ServiceError):
        S.convert_to_regression(db, "acme", "ana", failed.id)
    passed = db.query(D.CaseResult).filter_by(run_id=baseline_run.id, passed=True).first()
    with pytest.raises(S.ServiceError):
        S.convert_to_regression(db, "acme", "ana", passed.id)
    ds = db.query(D.Dataset).filter_by(agent_id=it_agent.id, kind="regression").one()
    v = db.query(D.DatasetVersion).filter_by(dataset_id=ds.id).one()
    run = S.create_run(db, "acme", "ana", it_agent.id, None, v.id, "regression", background=False)
    db.expire_all()
    assert db.get(D.EvalRun, run.id).metrics["passed"] == 0  # still failing on the unchanged v1 config


def test_persistence_and_audit(db, it_agent, baseline_run):
    S.analyze_run(db, "acme", "ana", baseline_run.id)
    actions = {a.action for a in db.query(D.AuditEvent).filter_by(tenant_id="acme")}
    assert {"agent.registered", "dataset.version_published", "run.created", "run.completed", "analysis.completed"} <= actions
    r = db.query(D.CaseResult).filter_by(run_id=baseline_run.id, case_key="IT-003").one()
    assert r.trace["spans"] and "567-89-0123" not in str(r.trace)
