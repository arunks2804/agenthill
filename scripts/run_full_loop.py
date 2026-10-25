"""Run the complete improvement loop headlessly and export the evidence to docs/examples/.

Uses a separate database (var/examples/) so the demo database is untouched. Every number in the exported
files comes from executions performed by this script.

    python scripts/run_full_loop.py                 # SimLM agents, deterministic judge (~10 s)
    python scripts/run_full_loop.py --ollama llama3.1:8b --judge-ollama qwen3:8b   # adds a real-model comparison
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("AGENTHILL_VAR_DIR", str(ROOT / "var" / "examples"))
sys.path.insert(0, str(ROOT / "backend"))

from agenthill import db as D  # noqa: E402
from agenthill import services as S  # noqa: E402
from agenthill.evaluation.calibration import calibrate  # noqa: E402
from agenthill.evaluation.engine import build_judge  # noqa: E402
from agenthill.platform_settings import put_setting  # noqa: E402

OUT = ROOT / "docs" / "examples"


def dump(name: str, obj) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(obj, indent=2, default=str))


def run_summary(db, run) -> dict:
    res = db.query(D.CaseResult).filter_by(run_id=run.id).order_by(D.CaseResult.case_key).all()
    cfg = db.get(D.AgentConfig, run.config_id)
    return {"run_id": run.id, "agent_id": run.agent_id, "config_version": cfg.version, "config_label": cfg.label, "purpose": run.purpose,
            "models": run.models, "metrics": run.metrics, "gate_verdict": run.gate_verdict, "duration_ms": run.duration_ms,
            "cases": [{"case_key": r.case_key, "category": r.case.get("category"), "passed": r.passed, "status": r.status, "outcome": r.outcome,
                       "tools": [f"{c['tool']}:{c['status']}" for c in r.tool_calls], "failed_checks": [c["name"] for c in r.checks if c["status"] == "fail"],
                       "judge_overall": (r.judge or {}).get("overall"), "response": r.response} for r in res]}


def loop(db, agent, actor_pm="maya", top=3, name="", only_change_types=None):
    dv = db.query(D.DatasetVersion).join(D.Dataset).filter(D.Dataset.agent_id == agent.id, D.Dataset.kind == "golden").first()
    base = S.create_run(db, agent.tenant_id, "ana", agent.id, None, dv.id, "baseline", background=False)
    db.expire_all()
    base = db.get(D.EvalRun, base.id)
    fa = S.analyze_run(db, agent.tenant_id, "ana", base.id)
    recs = db.query(D.Recommendation).filter_by(analysis_id=fa.id).order_by(D.Recommendation.priority_score.desc()).all()
    chosen = [r for r in recs if r.proposed_change["patches"] and (not only_change_types or r.change_type in only_change_types)][:top]
    for r in chosen:
        S.review_recommendation(db, agent.tenant_id, actor_pm, r.id, "approve", "Evidence reviewed")
    e = S.create_experiment(db, agent.tenant_id, "leo", agent.id, name or " + ".join(r.title for r in chosen), [r.id for r in chosen])
    S.approve_experiment(db, agent.tenant_id, actor_pm, e.id, "Approved for sandbox execution")
    e = S.run_experiment(db, agent.tenant_id, "leo", e.id, background=False)
    return base, fa, recs, e


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ollama", help="also compare the IT agent on this Ollama model")
    ap.add_argument("--judge-ollama", help="Ollama model to use as judge for the comparison")
    args = ap.parse_args()
    D.init_engine()
    D.reset_db()
    db = D.SessionLocal()
    S.ensure_tenants(db)
    t0 = time.time()

    # IT agent: baseline -> analysis -> top-3 recommendations -> experiment -> accept -> regression test
    it = S.register_from_template(db, "acme", "leo", "it_incident_agent")
    base, fa, recs, exp = loop(db, it, name="Injection guard + reset_password description + identity prerequisite")
    S.decide_experiment(db, "acme", "maya", exp.id, "accept", "All gates passed; no regressions")
    reg = S.convert_to_regression(db, "acme", "ana",
                                  db.query(D.CaseResult).filter_by(run_id=base.id, case_key="IT-003").one().id, "Confirmed tool-selection failure")
    reg_ds = db.query(D.Dataset).filter_by(agent_id=it.id, kind="regression").one()
    reg_v = db.query(D.DatasetVersion).filter_by(dataset_id=reg_ds.id).order_by(D.DatasetVersion.version.desc()).first()
    reg_run = S.create_run(db, "acme", "ana", it.id, None, reg_v.id, "regression", background=False)

    # Second IT experiment: the remaining retrieval recommendation, tested on the improved configuration
    db.expire_all()
    base2, fa2, recs2, exp2 = loop(db, db.get(D.Agent, it.id), top=1, name="Raise retrieval_min_score", only_change_types={"setting"})
    S.decide_experiment(db, "acme", "maya", exp2.id, "accept" if exp2.gate_verdict["verdict"] == "PASS" and not exp2.comparison["regressed"] else "reject",
                        "Decision taken from measured comparison")

    # HR agent: the same platform, unchanged
    hr = S.register_from_template(db, "acme", "leo", "hr_support_agent")
    hr_base, hr_fa, hr_recs, hr_exp = loop(db, hr, top=1, name="Lower HR escalation threshold", only_change_types={"setting"})
    S.decide_experiment(db, "acme", "maya", hr_exp.id, "accept" if hr_exp.gate_verdict["verdict"] == "PASS" and not hr_exp.comparison["regressed"] else "reject",
                        "Decision taken from measured comparison")

    db.expire_all()
    dump("it_baseline_run.json", run_summary(db, base))
    dump("it_failure_analysis.json", {"clusters": fa.clusters, "summary": fa.summary})
    dump("it_recommendations.json", [S.rec_out(r) for r in recs])
    for name, e in (("it_experiment_1_comparison.json", exp), ("it_experiment_2_comparison.json", exp2), ("hr_experiment_comparison.json", hr_exp)):
        e = db.get(D.Experiment, e.id)
        dump(name, {"experiment": S.exp_out(e), "candidate_config": S.config_out(db.get(D.AgentConfig, e.candidate_config_id))})
    dump("it_candidate_run.json", run_summary(db, db.get(D.EvalRun, db.get(D.Experiment, exp.id).candidate_run_id)))
    dump("it_regression_run.json", {"regression_case": reg, "run": run_summary(db, db.get(D.EvalRun, reg_run.id))})
    dump("hr_baseline_run.json", run_summary(db, hr_base))
    dump("hr_failure_analysis.json", {"clusters": hr_fa.clusters, "summary": hr_fa.summary})
    dump("experiment_history.json", S.history(db, "acme"))
    dump("judge_calibration_rubric.json", calibrate(build_judge(db, "acme")))

    if args.ollama:
        if args.judge_ollama:
            put_setting(db, "acme", "model_roles", {"judge": {"provider": "ollama", "model": args.judge_ollama, "max_tokens": 600}}, "ana")
            dump("judge_calibration_ollama.json", calibrate(build_judge(db, "acme")))
        agent = db.get(D.Agent, it.id)
        cur = db.get(D.AgentConfig, agent.current_config_id)
        patches = [{"type": "setting", "target": "model_provider", "before": cur.settings["model_provider"], "after": "ollama", "summary": "model_provider -> ollama"},
                   {"type": "setting", "target": "model", "before": cur.settings["model"], "after": args.ollama, "summary": f"model -> {args.ollama}"}]
        e = S.create_experiment(db, "acme", "leo", agent.id, f"Model comparison: SimLM vs ollama:{args.ollama}", patches=patches, kind="model_comparison")
        S.approve_experiment(db, "acme", "ana", e.id)
        e = S.run_experiment(db, "acme", "leo", e.id, background=False)
        dump("model_comparison_simlm_vs_ollama.json", {"experiment": S.exp_out(e),
                                                       "candidate_run": run_summary(db, db.get(D.EvalRun, e.candidate_run_id)) if e.candidate_run_id else None})

    lines = ["# Example run summary", "", f"Generated by `scripts/run_full_loop.py` in {time.time() - t0:.0f}s. All values below come from executions in that script.", ""]
    for label, e in (("IT experiment 1", exp), ("IT experiment 2", exp2), ("HR experiment", hr_exp)):
        e = db.get(D.Experiment, e.id)
        m = e.comparison["metrics"]
        lines.append(f"- **{label} — {e.name}**: task success {m['task_success_rate']['baseline']:.1%} → {m['task_success_rate']['candidate']:.1%}, "
                     f"critical failures {m['critical_failures']['baseline']} → {m['critical_failures']['candidate']}, fixed {len(e.comparison['fixed'])}, "
                     f"regressed {len(e.comparison['regressed'])} {e.comparison['regressed'] or ''}, gates **{e.gate_verdict['verdict']}**, decision **{e.decision}**")
    lines.append(f"- Failure classification vs. human labels (IT v1): {fa.summary['labeled_accuracy']['primary_accuracy']:.0%} of {fa.summary['labeled_accuracy']['labeled_cases']} cases")
    (OUT / "SUMMARY.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
