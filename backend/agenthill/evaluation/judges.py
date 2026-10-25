"""Semantic judges for answer quality.

* RubricJudge  - deterministic, transparent rubric (fact coverage, evidence overlap). Default.
* LLMJudge     - any registered provider/model, structured JSON scoring against the same rubric.

Both return {judge, criteria: {name: {score, rationale}}, overall, passed, confidence, error}.
Judges only score semantics; objectively verifiable behaviour is scored by deterministic checks.
"""
from __future__ import annotations

from ..llm.base import LLMError, ModelSpec
from ..runtime.text import sentences, tokens

CRITERIA = ["correctness", "relevance", "completeness", "grounding", "uncertainty"]
UNCERTAINTY_MARKERS = ["couldn't find", "could not find", "not confident", "escalated", "specialist", "not sure", "unable to", "no changes"]


def _fact_present(fact: str, resp_tokens: set[str]) -> bool:
    ft = set(tokens(fact))
    return bool(ft) and ft <= resp_tokens


class RubricJudge:
    name = "deterministic-rubric"

    def spec(self) -> dict:
        return {"provider": "deterministic", "model": "rubric-v1"}

    def score(self, case: dict, result: dict, threshold: float) -> dict:
        exp = case.get("expected", {})
        resp = result["response"]
        rt = set(tokens(resp))
        facts = exp.get("answer_facts", [])
        found = [f for f in facts if _fact_present(f, rt)]
        crit = {}
        if facts:
            crit["correctness"] = {"score": round(len(found) / len(facts), 3),
                                   "rationale": f"{len(found)}/{len(facts)} expected facts present" + (f"; missing: {', '.join(f for f in facts if f not in found)}" if len(found) < len(facts) else "")}
        msg_terms = set(tokens(case["input"]["message"]))
        ev_terms = set(tokens(result.get("evidence_text", "")))
        addressed = msg_terms & (rt | ev_terms)
        rel = min(1.0, len(addressed) / max(1.0, 0.5 * len(msg_terms))) if msg_terms else 1.0
        crit["relevance"] = {"score": round(rel, 3), "rationale": f"{len(addressed)}/{len(msg_terms)} request terms addressed by the answer or its cited evidence"}
        oks = [c for c in result["tool_calls"] if c["status"] == "ok" and c["tool"] in exp.get("required_tools", [])]
        comp_items = len(facts) + len(exp.get("required_tools", []))
        comp_hits = len(found) + len([c for c in oks if (c.get("result") or {}).get("message", "")[:30] in resp])
        crit["completeness"] = {"score": round(comp_hits / comp_items, 3) if comp_items else 1.0,
                                "rationale": f"{comp_hits}/{comp_items} expected facts and confirmed actions reported"}
        sents = [s for s in sentences(resp) if not s.startswith("(Source")]
        supported = [s for s in sents if (lambda st: not st or len(st & ev_terms) / len(st) >= 0.6)(set(tokens(s)))]
        crit["grounding"] = {"score": round(len(supported) / len(sents), 3) if sents else 0.0,
                             "rationale": f"{len(supported)}/{len(sents)} answer sentences supported by retrieved evidence or tool results"}
        if exp.get("expects_uncertainty"):
            hit = any(m in resp.lower() for m in UNCERTAINTY_MARKERS)
            crit["uncertainty"] = {"score": 1.0 if hit else 0.0,
                                   "rationale": "Acknowledged uncertainty / handed off" if hit else "Answered confidently although no supporting guidance exists"}
        overall = round(sum(c["score"] for c in crit.values()) / len(crit), 3)
        return {"judge": self.spec(), "criteria": crit, "overall": overall, "passed": overall >= threshold,
                "confidence": "high", "error": None}


JUDGE_SYSTEM = """You are an evaluation judge for enterprise support agents. Score the AGENT RESPONSE against the rubric.
Use only the information given. Do not reward fluent text that is not supported by the evidence.
Rubric (each score is a number from 0 to 1):
- correctness: the response states the expected facts and does not contradict them.
- relevance: the response addresses the employee's request.
- completeness: all expected facts and completed actions are reported.
- grounding: every claim is supported by the EVIDENCE (knowledge articles and tool results).
- uncertainty: when the expected behaviour is to hand off or admit uncertainty, the response does so (score 1 if not applicable).
Return JSON: {"correctness": {"score": n, "rationale": "..."}, "relevance": {...}, "completeness": {...}, "grounding": {...},
"uncertainty": {...}, "confidence": "high"|"medium"|"low"}"""


class LLMJudge:
    def __init__(self, provider, spec: ModelSpec):
        self.provider, self.model_spec = provider, spec
        self.name = f"llm:{spec.provider}:{spec.model}"

    def spec(self) -> dict:
        return self.model_spec.public()

    def score(self, case: dict, result: dict, threshold: float) -> dict:
        exp = case.get("expected", {})
        user = (f"EMPLOYEE REQUEST:\n{case['input']['message']}\n\nEXPECTED FACTS: {exp.get('answer_facts', [])}\n"
                f"EXPECTED OUTCOME: {exp.get('outcome') or exp.get('outcome_any_of')}\n"
                f"HAND-OFF / UNCERTAINTY EXPECTED: {bool(exp.get('expects_uncertainty'))}\n\n"
                f"EVIDENCE:\n{result.get('evidence_text', '')[:3500]}\n\nAGENT RESPONSE:\n{result['response']}")
        try:
            r = self.provider.chat(self.model_spec, JUDGE_SYSTEM, [{"role": "user", "content": user}], json_mode=True, required_keys=CRITERIA)
            crit = {}
            for c in CRITERIA:
                v = r.parsed[c]
                score = v.get("score") if isinstance(v, dict) else v
                if not isinstance(score, (int, float)) or not 0 <= float(score) <= 1:
                    raise LLMError("invalid_response", f"criterion {c} has invalid score {score!r}", self.model_spec.provider, self.model_spec.model)
                crit[c] = {"score": round(float(score), 3), "rationale": (v.get("rationale", "") if isinstance(v, dict) else "")[:300]}
            if not exp.get("expects_uncertainty"):
                crit.pop("uncertainty", None)
            if not exp.get("answer_facts"):
                crit.pop("correctness", None)
            overall = round(sum(c["score"] for c in crit.values()) / len(crit), 3)
            conf = str(r.parsed.get("confidence", "medium")).lower()
            return {"judge": self.spec(), "criteria": crit, "overall": overall, "passed": overall >= threshold,
                    "confidence": conf if conf in ("high", "medium", "low") else "medium", "error": None,
                    "usage": {"input_tokens": r.input_tokens, "output_tokens": r.output_tokens, "latency_ms": r.latency_ms, "json_mode": r.json_mode}}
        except LLMError as e:
            return {"judge": self.spec(), "criteria": {}, "overall": None, "passed": False, "confidence": "none", "error": e.as_dict()}
