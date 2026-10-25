"""Judge calibration against human-reviewed labels (agreement rate and Cohen's kappa)."""
from __future__ import annotations

import json
import time

from ..config import DATA_DIR
from ..db import utcnow


def _kappa(pairs: list[tuple[bool, bool]]) -> float | None:
    n = len(pairs)
    if not n:
        return None
    po = sum(a == b for a, b in pairs) / n
    pa = sum(a for a, _ in pairs) / n
    pb = sum(b for _, b in pairs) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return round((po - pe) / (1 - pe), 4) if pe < 1 else 1.0


def calibrate(judge, threshold: float = 0.6) -> dict:
    data = json.loads((DATA_DIR / "calibration" / "judge_calibration.json").read_text())
    rows, t0 = [], time.perf_counter()
    for ex in data["examples"]:
        case = {"input": {"message": ex["message"]}, "expected": {"answer_facts": ex["answer_facts"],
                                                                   "expects_uncertainty": ex.get("expects_uncertainty", False)}}
        result = {"response": ex["response"], "evidence_text": ex["evidence"], "tool_calls": []}
        j = judge.score(case, result, threshold)
        rows.append({"id": ex["id"], "human_pass": ex["human_pass"], "judge_pass": j["passed"] if not j.get("error") else None,
                     "judge_overall": j.get("overall"), "error": j.get("error")})
    scored = [(r["human_pass"], r["judge_pass"]) for r in rows if r["judge_pass"] is not None]
    agree = sum(a == b for a, b in scored)
    return {"judge": judge.spec(), "examples": len(rows), "scored": len(scored), "errors": len(rows) - len(scored),
            "agreement_rate": round(agree / len(scored), 4) if scored else None, "cohens_kappa": _kappa(scored),
            "false_pass": sum(1 for h, j in scored if j and not h), "false_fail": sum(1 for h, j in scored if h and not j),
            "duration_ms": round((time.perf_counter() - t0) * 1000, 1), "computed_at": utcnow().isoformat() + "Z", "rows": rows}
