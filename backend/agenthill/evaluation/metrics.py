"""Run-level quality metrics, computed only from persisted case results."""
from __future__ import annotations

import math
from collections import Counter


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = max(0, min(len(s) - 1, math.ceil(p / 100 * len(s)) - 1))
    return round(s[k], 1)


def _rate(checks: list[list[dict]], name: str) -> float | None:
    vals = [next((c for c in cs if c["name"] == name), None) for cs in checks]
    vals = [v for v in vals if v and v["status"] != "skip"]
    return round(sum(v["status"] == "pass" for v in vals) / len(vals), 4) if vals else None


def compute(results: list) -> dict:
    n = len(results)
    if n == 0:
        return {"total_cases": 0}
    checks = [r.checks for r in results]
    judged = [r.judge for r in results if r.judge.get("overall") is not None]

    def jmean(c: str) -> float | None:
        vals = [j["criteria"][c]["score"] for j in judged if c in j.get("criteria", {})]
        return round(sum(vals) / len(vals), 4) if vals else None

    tool_calls = [c for r in results for c in r.tool_calls]
    failed_check_counts = Counter(c["name"] for r in results for c in r.checks if c["status"] == "fail")
    costs = [r.cost_usd for r in results if r.cost_usd is not None]
    return {
        "total_cases": n,
        "passed": sum(r.passed for r in results),
        "failed": sum(r.status == "failed" for r in results),
        "errored": sum(r.status == "error" for r in results),
        "task_success_rate": round(sum(r.passed for r in results) / n, 4),
        "tool_selection_accuracy": _rate(checks, "tool_selection"),
        "tool_argument_accuracy": _rate(checks, "tool_arguments"),
        "tool_execution_success": round(sum(c["status"] == "ok" for c in tool_calls) / len(tool_calls), 4) if tool_calls else None,
        "workflow_completion_rate": _rate(checks, "workflow_completion"),
        "workflow_order_compliance": _rate(checks, "workflow_order"),
        "policy_compliance_rate": _rate(checks, "policy_compliance"),
        "answer_correctness": jmean("correctness"),
        "relevance": jmean("relevance"),
        "completeness": jmean("completeness"),
        "grounding": jmean("grounding"),
        "judge_overall": round(sum(j["overall"] for j in judged) / len(judged), 4) if judged else None,
        "quality_score": round(sum(r.score for r in results) / n, 4),
        "critical_failures": sum(r.critical for r in results),
        "needs_review": sum(r.needs_review for r in results),
        "failed_checks": dict(failed_check_counts),
        "latency_p50_ms": _pct([r.latency_ms for r in results], 50),
        "latency_p95_ms": _pct([r.latency_ms for r in results], 95),
        "tokens_total": sum(r.tokens for r in results),
        "cost_usd_total": round(sum(costs), 6) if costs and len(costs) == n else None,
        "by_category": {cat: {"total": sum(1 for r in results if r.case.get("category") == cat),
                              "passed": sum(1 for r in results if r.case.get("category") == cat and r.passed)}
                        for cat in sorted({r.case.get("category") for r in results})},
    }
