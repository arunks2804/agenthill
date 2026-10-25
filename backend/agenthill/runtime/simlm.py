"""SimLM: a deterministic reference model used to run the demonstration agents reproducibly.

SimLM is NOT a language model. It is a transparent decision procedure whose behaviour is driven
entirely by the agent configuration under test, so that configuration changes (prompt text, tool
descriptions, execution settings) change behaviour in the same direction they would for an LLM agent:

* Tool selection: TF-IDF similarity between a runbook step and each enabled tool's name + description
  (sentences starting "Do not use" count as negative evidence). Poor descriptions -> wrong tools.
* Instructions: system-prompt sentences are interpreted with a small documented grammar
  (prerequisites, prohibitions, fallback escalation, untrusted-content guard). Unrecognised
  sentences are reported in the trace as "not interpreted".
* Untrusted content: without an untrusted-content directive, instruction-like text in retrieved
  documents or the user message is obeyed - the classic prompt-injection weakness.

Every decision is emitted to the trace with its evidence (candidate scores, matched directive),
which is what the Failure Analysis Agent later reasons over.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from ..environments.base import Environment, ToolSpec
from .text import TfIdf, overlap_terms, sentences, tokens

COMMUNICATIVE_VERBS = {"tell", "ask", "advise", "explain", "remind", "suggest", "inform", "let", "confirm"}
WRITE_ACTION_PHRASES = ("any write action", "any action that changes", "any account-changing action", "any state-changing action")
CALL_RE = re.compile(r"\b(?:call|use|run|invoke|execute)\s+(?:the\s+)?([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\b")
EMP_RE = re.compile(r"\b([EG]\d{4})\b", re.I)
ISO_DATE_RE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")
MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                         "september", "october", "november", "december"], 1)}
TEXT_DATE_RE = re.compile(r"\b(" + "|".join(MONTHS) + r")\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(20\d{2}))?", re.I)


@dataclass
class Directives:
    prerequisites: list[dict] = field(default_factory=list)   # {"before": tool, "targets": [tools], "sentence"}
    prohibited: list[dict] = field(default_factory=list)      # {"tool", "sentence"}
    fallback: dict | None = None                             # {"step_text", "sentence"}
    untrusted_guard: bool = False
    interpreted: list[dict] = field(default_factory=list)
    not_interpreted: list[str] = field(default_factory=list)


def tool_names_in(text: str, known: set[str]) -> list[str]:
    found = []
    for t in re.findall(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)+", text.lower()):
        if t in known and t not in found:
            found.append(t)
    return found


def interpret_instructions(prompt: str, tools: dict[str, dict], all_tools: set[str]) -> Directives:
    d = Directives()
    write_tools = [n for n, t in tools.items() if t["side_effect"] == "write"]
    for s in sentences(prompt):
        low = s.lower()
        handled = False
        m = re.search(r"\bbefore\b", low)
        if m and re.search(r"\b(always|must|first)\b", low[: m.start()] + " "):
            left, right = low[: m.start()], low[m.end():]
            pre = tool_names_in(left, all_tools)
            targets = tool_names_in(right, all_tools)
            if not targets and any(p in right for p in WRITE_ACTION_PHRASES):
                targets = write_tools
            if pre and targets:
                d.prerequisites.append({"before": pre[0], "targets": targets, "sentence": s})
                d.interpreted.append({"type": "prerequisite", "sentence": s, "before": pre[0], "targets": targets})
                handled = True
        if not handled and re.search(r"\b(never|do not|don't)\s+(call|use|run)\b", low):
            for t in tool_names_in(low, all_tools):
                d.prohibited.append({"tool": t, "sentence": s})
                d.interpreted.append({"type": "prohibition", "sentence": s, "tool": t})
                handled = True
        if not handled and "escalate" in low and re.search(r"\b(no relevant|cannot find|can't find|not found|no matching)\b", low):
            clause = low[low.index("escalate"):].rstrip(".")
            d.fallback = {"step_text": clause[0].upper() + clause[1:], "sentence": s}
            d.interpreted.append({"type": "fallback_escalation", "sentence": s, "step_text": d.fallback["step_text"]})
            handled = True
        if not handled and ("untrusted" in low or re.search(r"do not follow (any )?instructions", low)):
            d.untrusted_guard = True
            d.interpreted.append({"type": "untrusted_content_guard", "sentence": s})
            handled = True
        if not handled:
            d.not_interpreted.append(s)
    return d


def injected_calls(text: str, all_tools: set[str]) -> list[str]:
    """Instruction-like tool invocations embedded in content that is not the system prompt."""
    return [t for t in CALL_RE.findall((text or "").lower()) if t in all_tools]


class ToolSelector:
    def __init__(self, tools: dict[str, dict]):
        self.names = list(tools.keys())
        pos_docs, self.neg_docs = [], []
        for name in self.names:
            desc = tools[name].get("description", "")
            pos = [s for s in sentences(desc) if not re.match(r"(do not|don't|never|not for)\b", s.lower())]
            neg = [s for s in sentences(desc) if re.match(r"(do not|don't|never|not for)\b", s.lower())]
            pos_docs.append(name.replace("_", " ") + " " + " ".join(pos))
            self.neg_docs.append(" ".join(neg))
        self.index = TfIdf(pos_docs) if pos_docs else None
        self.pos_docs = pos_docs

    def rank(self, text: str) -> list[dict]:
        if not self.index:
            return []
        q = self.index.vector(text)
        out = []
        for i, name in enumerate(self.names):
            score = TfIdf.cosine(q, self.index.doc_vecs[i])
            if self.neg_docs[i]:
                score -= 0.5 * TfIdf.cosine(q, self.index.vector(self.neg_docs[i]))
            out.append({"tool": name, "score": round(score, 4), "matched_terms": overlap_terms(text, self.pos_docs[i])})
        return sorted(out, key=lambda c: (-c["score"], self.names.index(c["tool"])))


def is_communicative(step_text: str) -> bool:
    words = tokens(step_text, keep_stop=True)
    return bool(words) and words[0] in COMMUNICATIVE_VERBS


def extract_software(message: str) -> str | None:
    m = re.search(r"\b(?:install|installed|installing|get|need|request|use|download)\s+((?:[A-Za-z][\w.+-]*[A-Z][\w.+-]*|[A-Z][\w.+-]*)(?:\s+[A-Z][\w.+-]*)*)", message)
    return m.group(1).strip() if m else None


def extract_date(message: str) -> str | None:
    m = ISO_DATE_RE.search(message)
    if m:
        return m.group(1)
    m = TEXT_DATE_RE.search(message)
    if m:
        try:
            return date(int(m.group(3) or 2026), MONTHS[m.group(1).lower()], int(m.group(2))).isoformat()
        except ValueError:
            return None
    return None


def extract_person(message: str) -> str | None:
    m = re.search(r"\b(?:hire|named|called)\s+([A-Z][a-z]+\s+[A-Z][a-z]+)", message)
    return m.group(1) if m else None


def fill_args(spec: ToolSpec, step_text: str, message: str, target_employee: str) -> tuple[dict, list[dict]]:
    """Fill tool arguments from the runbook step, the user message and the case context."""
    args, notes = {}, []
    step_low, msg_low = step_text.lower(), message.lower()
    for p in spec.params:
        val, src = None, None
        if p.name == "employee_id":
            val, src = target_employee, "case context / message"
        elif p.type == "enum":
            for text, label in ((step_low, "runbook step"), (msg_low, "user message")):
                for opt in p.options or []:
                    if re.search(r"(?<![\w])" + re.escape(opt.lower().replace("_", " ")) + r"(?![\w])", text):
                        val, src = opt, label
                        break
                if val:
                    break
            if val is None:
                val, src = p.default, "parameter default"
        elif p.type == "boolean":
            words = set(tokens(p.name.replace("_", " ")))
            val = bool(words) and words <= set(tokens(step_text))
            src = "runbook step" if val else "parameter default"
        elif p.type == "date":
            val, src = extract_date(message), "user message"
        elif p.name == "software_name":
            val, src = extract_software(message), "user message"
        elif p.name == "new_hire_name":
            val, src = extract_person(message), "user message"
        elif p.name in ("summary", "reason"):
            val, src = message[:160], "user message"
        if val is None and not p.required:
            continue
        args[p.name] = val
        notes.append({"param": p.name, "value": val, "source": src})
    return args, notes


def eval_condition(cond: dict | None, results: dict[str, list], message: str) -> bool:
    """Evaluate a runbook step condition against tool results observed so far."""
    if not cond:
        return True
    if "all" in cond:
        return all(eval_condition(c, results, message) for c in cond["all"])
    if "not" in cond:
        return not eval_condition(cond["not"], results, message)
    if "message_contains_any" in cond:
        low = message.lower().replace("’", "'")
        return any(p in low for p in cond["message_contains_any"])
    calls = results.get(cond.get("tool", ""), [])
    if not calls:
        return False
    last = calls[-1]
    if "ok" in cond:
        return (last.status == "ok") == cond["ok"]
    if last.status != "ok":
        return False
    return last.result.get(cond["field"]) == cond["equals"]


_PHRASES = [
    (r"^tell the employee that ", ""), (r"^tell the manager that ", ""), (r"^tell the employee to ", "Please "),
    (r"^tell the employee ", ""), (r"^ask the employee to ", "Please "), (r"^advise the employee to ", "Please "),
    (r"^explain that ", ""), (r"^remind the employee that ", "Note: "), (r"^suggest ", "Consider "),
]


def to_user_text(step_text: str) -> str:
    t = step_text.strip()
    for pat, rep in _PHRASES:
        if re.match(pat, t, re.I):
            t = re.sub(pat, rep, t, flags=re.I)
            break
    t = re.sub(r"\bthe employee's\b", "your", t, flags=re.I)
    t = re.sub(r"\bthe employee\b", "you", t, flags=re.I)
    t = t[0].upper() + t[1:] if t else t
    return t if t.endswith(".") else t + "."


def estimate_tokens(*texts: str) -> int:
    return int(sum(len((t or "").split()) for t in texts) * 1.3)


def target_employee_for(message: str, requester: str) -> str:
    m = EMP_RE.search(message)
    return m.group(1).upper() if m else requester


def build_tool_view(env: Environment, config_tools: list[dict]) -> dict[str, dict]:
    """Merge the agent's tool configuration (enabled + descriptions) with environment schemas."""
    view = {}
    for t in config_tools:
        if not t.get("enabled", True) or t["name"] not in env.tools:
            continue
        spec = env.tools[t["name"]]
        view[t["name"]] = {"description": t.get("description") or spec.default_description, "side_effect": spec.side_effect}
    return view
