"""Regression detection and quality gates.

A gate verdict states whether a configuration met the thresholds the team defined on this dataset.
It is explicitly NOT a production-readiness certificate.
"""
from __future__ import annotations

DISCLAIMER = ("Quality gates show this configuration met the team's thresholds on this dataset version. "
              "They do not prove production readiness: coverage is limited to the scenarios in the dataset, "
              "tools are simulated, and live traffic may differ.")

GATE_DEFS = [
    ("min_task_success_rate", "task_success_rate", ">=", "Task success rate"),
    ("min_tool_selection_accuracy", "tool_selection_accuracy", ">=", "Tool selection accuracy"),
    ("max_critical_failures", "critical_failures", "<=", "Critical failures"),
    ("max_regressions", "regressions", "<=", "Regressions vs baseline"),
    ("max_p95_latency_ms", "latency_p95_ms", "<=", "p95 latency (ms)"),
    ("max_cost_per_run_usd", "cost_usd_total", "<=", "Cost per run (USD)"),
]


def detect_regressions(baseline_results: list, candidate_results: list) -> dict:
    base = {r.case_key: r for r in baseline_results}
    cand = {r.case_key: r for r in candidate_results}
    rows, fixed, regressed, still_failing, still_passing = [], [], [], [], []
    for key in sorted(set(base) | set(cand)):
        b, c = base.get(key), cand.get(key)
        if b is None or c is None:
            continue
        if b.passed and not c.passed:
            change = "regressed"
            regressed.append(key)
        elif not b.passed and c.passed:
            change = "fixed"
            fixed.append(key)
        elif not b.passed and not c.passed:
            change = "still_failing"
            still_failing.append(key)
        else:
            change = "still_passing"
            still_passing.append(key)
        rows.append({"case_key": key, "category": c.case.get("category"), "severity": c.case.get("severity"), "change": change,
                     "baseline": {"passed": b.passed, "outcome": b.outcome, "score": b.score, "result_id": b.id,
                                  "failed_checks": [x["name"] for x in b.checks if x["status"] == "fail"]},
                     "candidate": {"passed": c.passed, "outcome": c.outcome, "score": c.score, "result_id": c.id,
                                   "failed_checks": [x["name"] for x in c.checks if x["status"] == "fail"]}})
    return {"rows": rows, "fixed": fixed, "regressed": regressed, "still_failing": still_failing, "still_passing": still_passing}


def evaluate_gates(metrics: dict, gates: dict, regressions: int | None = None) -> dict:
    observed = {**metrics, "regressions": regressions}
    results = []
    for key, metric, op, label in GATE_DEFS:
        if key not in gates or gates[key] is None:
            continue
        threshold = gates[key]
        value = observed.get(metric)
        if value is None:
            status = "not_measured"
            reason = ("No baseline comparison in this run" if metric == "regressions"
                      else "Cost not measurable for this provider (local or unpriced model)" if metric == "cost_usd_total"
                      else "Metric not available")
        else:
            ok = value >= threshold if op == ">=" else value <= threshold
            status = "pass" if ok else "fail"
            reason = f"{label} {value} {'meets' if ok else 'violates'} {op} {threshold}"
        results.append({"gate": key, "label": label, "metric": metric, "operator": op, "threshold": threshold,
                        "value": value, "status": status, "reason": reason})
    failed = [g for g in results if g["status"] == "fail"]
    unmeasured = [g for g in results if g["status"] == "not_measured"]
    verdict = "FAIL" if failed else ("PASS" if not unmeasured else "PASS_WITH_UNMEASURED")
    summary = (f"{len(failed)} of {len(results)} gates failed: " + "; ".join(g["reason"] for g in failed)) if failed else \
              f"All {len(results) - len(unmeasured)} measured gates passed" + (f"; {len(unmeasured)} not measured" if unmeasured else "")
    return {"verdict": verdict, "summary": summary, "gates": results, "disclaimer": DISCLAIMER}
