"""Agent Execution Service: runs one evaluation case for one agent configuration.

Orchestration is a LangGraph state machine: interpret -> retrieve -> plan -> act (loop) -> respond.
The model behind the graph is pluggable: SimLM (deterministic, default) or a real LLM provider
(see llm_agent.py), which replaces the plan/act loop with a JSON tool-calling loop.
"""
from __future__ import annotations

import time
from collections import defaultdict
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from ..environments.base import get_environment
from . import simlm
from .gateway import ToolGateway
from .retrieval import retrieve
from .tracing import Tracer

GENERIC_ANSWER = ("This is usually caused by a temporary glitch. Please restart your device, wait a few minutes "
                  "and try again; it should resolve itself.")


class GraphState(TypedDict, total=False):
    steps: list[dict]
    idx: int
    notes: list[str]
    flags: list[str]
    halted: str | None
    article: dict | None
    mode: str


class RunContext:
    def __init__(self, env_name: str, tenant_id: str, config: dict, case: dict, provider=None):
        self.provider = provider
        self.input_tokens = 0
        self.output_tokens = 0
        self.env = get_environment(env_name)
        self.env_name = env_name
        self.config = config
        self.settings = {**DEFAULT_SETTINGS, **(config.get("settings") or {})}
        self.case = case
        self.message = case["input"]["message"]
        self.requester = case["input"].get("employee_id", "")
        self.target = simlm.target_employee_for(self.message, self.requester)
        self.tracer = Tracer(case.get("case_key", "case"))
        self.sandbox = self.env.new_sandbox(tenant_id, case.get("faults") or {})
        self.tools = simlm.build_tool_view(self.env, config.get("tools", []))
        self.gateway = ToolGateway(self.env, self.sandbox, set(self.tools), self.tracer)
        self.results: dict[str, list] = defaultdict(list)
        self.all_tools = set(self.env.tools)
        self.directives = simlm.Directives()
        self.selector = simlm.ToolSelector(self.tools)
        self.retrieved: list[dict] = []
        self.llm_calls = 0
        self.tokens = 0
        self.base_tokens = 0

    def model_call(self, purpose: str, output: dict, extra_text: str = "") -> None:
        """Account for one model turn (SimLM decisions are modelled as model calls for latency/cost)."""
        in_tok = self.base_tokens + simlm.estimate_tokens(extra_text)
        out_tok = 40
        self.llm_calls += 1
        self.tokens += in_tok + out_tok
        self.input_tokens += in_tok
        self.output_tokens += out_tok
        modeled = self.settings["model_latency_base_ms"] + (in_tok + out_tok) * self.settings["model_latency_per_token_ms"]
        self.tracer.add("llm_call", purpose, {"model": f"{self.settings['model_provider']}:{self.settings['model']}",
                                              "input_tokens": in_tok, "output_tokens": out_tok, "output": output}, modeled_ms=modeled)


DEFAULT_SETTINGS: dict[str, Any] = {
    "model_provider": "simlm", "model": "simlm-1", "temperature": 0.0,
    "retrieval_top_k": 3, "retrieval_min_score": 0.1, "tool_selection_min_score": 0.15,
    "escalation_threshold": 0.0, "max_steps": 8, "cost_per_1k_tokens": 0.003, "treat_tool_errors_as_blocking": False,
    "model_latency_base_ms": 180.0, "model_latency_per_token_ms": 0.05,
}


def _build_graph(ctx: RunContext):
    def interpret(state: GraphState) -> GraphState:
        ctx.directives = simlm.interpret_instructions(ctx.config.get("system_prompt", ""), ctx.tools, ctx.all_tools)
        ctx.tracer.add("instructions", "interpret_system_prompt", {
            "interpreted": ctx.directives.interpreted, "not_interpreted": ctx.directives.not_interpreted,
            "untrusted_content_guard": ctx.directives.untrusted_guard})
        return {}

    def do_retrieve(state: GraphState) -> GraphState:
        s = ctx.settings
        docs, candidates = retrieve(ctx.env_name, ctx.message, s["retrieval_top_k"], s["retrieval_min_score"])
        ctx.retrieved = docs
        ctx.tracer.add("retrieval", "knowledge_search", {
            "query": ctx.message, "top_k": s["retrieval_top_k"], "min_score": s["retrieval_min_score"],
            "accepted": [{k: d[k] for k in ("id", "title", "score", "source", "matched_terms")} for d in docs],
            "candidates": candidates}, modeled_ms=120)
        ctx.base_tokens = simlm.estimate_tokens(ctx.config.get("system_prompt", ""), ctx.message,
                                                *[t["description"] for t in ctx.tools.values()], *[d["body"] for d in docs])
        injected: list[dict] = []
        sources = [("user_message", ctx.message)] + [(f"retrieved:{d['id']}", d["body"]) for d in docs]
        for source, text in sources:
            for tool in simlm.injected_calls(text, ctx.all_tools):
                obey = not ctx.directives.untrusted_guard
                ctx.tracer.add("decision", "instruction_in_untrusted_content", {
                    "source": source, "requested_tool": tool, "obeyed": obey,
                    "reason": "no untrusted-content directive in system prompt" if obey else "untrusted-content directive in system prompt"})
                if obey:
                    injected.append({"text": f"[injected from {source}] call {tool}", "tool": tool, "kind": "injected"})
        return {"steps": injected}

    def plan(state: GraphState) -> GraphState:
        steps = list(state.get("steps") or [])
        s = ctx.settings
        top = ctx.retrieved[0] if ctx.retrieved else None
        fallback = ctx.directives.fallback
        if top is None:
            if fallback:
                decision = {"plan": "fallback_escalation", "reason": "no knowledge article above min_score", "directive": fallback["sentence"]}
                steps.append({"text": fallback["step_text"], "kind": "fallback"})
                article = None
            else:
                decision = {"plan": "unsupported_answer", "reason": "no knowledge article and no fallback instruction"}
                article = None
        elif s["escalation_threshold"] and top["score"] < s["escalation_threshold"]:
            text = fallback["step_text"] if fallback else "Escalate the request for human review"
            decision = {"plan": "low_confidence_escalation", "top_article": top["id"], "top_score": top["score"],
                        "escalation_threshold": s["escalation_threshold"]}
            steps.append({"text": text, "kind": "fallback"})
            article = None
        else:
            article = top["article"]
            decision = {"plan": "follow_runbook", "article": article["id"], "score": top["score"], "steps": [st["text"] for st in article["steps"]]}
            steps.extend({**st, "kind": "runbook"} for st in article["steps"])
        ctx.model_call("plan", decision)
        ctx.tracer.add("decision", "plan", decision)
        return {"steps": steps, "idx": 0, "notes": [], "flags": [], "article": article, "halted": None,
                "mode": decision["plan"]}

    def act(state: GraphState) -> GraphState:
        idx, steps = state["idx"], state["steps"]
        notes, flags = list(state["notes"]), list(state["flags"])
        step = steps[idx]
        out: GraphState = {"idx": idx + 1}
        if not simlm.eval_condition(step.get("when"), ctx.results, ctx.message):
            ctx.tracer.add("decision", "skip_step", {"step": step["text"], "condition": step.get("when"), "reason": "condition not met"})
            return out
        if step["kind"] != "injected" and simlm.is_communicative(step["text"]):
            notes.append(simlm.to_user_text(step["text"]))
            if step.get("outcome"):
                flags.append(step["outcome"])
            ctx.tracer.add("decision", "respond_step", {"step": step["text"], "outcome": step.get("outcome")})
            return {**out, "notes": notes, "flags": flags}
        if step["kind"] == "injected":
            tool = step["tool"]
        else:
            ranking = ctx.selector.rank(step["text"])
            best = ranking[0] if ranking else None
            selected = best["tool"] if best and best["score"] >= ctx.settings["tool_selection_min_score"] else None
            ctx.tracer.add("decision", "tool_selection", {"step": step["text"], "candidates": ranking, "selected": selected,
                                                          "threshold": ctx.settings["tool_selection_min_score"]})
            if selected is None:
                notes.append(simlm.to_user_text(step["text"]))
                return {**out, "notes": notes}
            tool = selected
        spec = ctx.env.tools[tool]
        args, arg_notes = simlm.fill_args(spec, step["text"], ctx.message, ctx.target)
        ctx.tracer.add("decision", "argument_filling", {"tool": tool, "args": args, "sources": arg_notes})
        if any(p["tool"] == tool for p in ctx.directives.prohibited):
            ctx.tracer.add("decision", "prohibited_by_instruction", {"tool": tool})
            notes.append(f"I am not permitted to use {tool.replace('_', ' ')}.")
            return {**out, "notes": notes}
        for pre in ctx.directives.prerequisites:
            if tool not in pre["targets"] or pre["before"] == tool:
                continue
            emp = args.get("employee_id")
            if not ctx.gateway.succeeded(pre["before"], emp):
                if len(ctx.gateway.calls) >= ctx.settings["max_steps"]:
                    return _halt(out, notes, flags)
                ctx.tracer.add("decision", "insert_prerequisite", {"tool": tool, "prerequisite": pre["before"], "directive": pre["sentence"]})
                call = ctx.gateway.call(pre["before"], {"employee_id": emp}, "prerequisite", idx)
                ctx.results[pre["before"]].append(call)
                ctx.model_call(f"after:{pre['before']}", {"status": call.status}, str(call.result or call.error))
                if call.status != "ok":
                    ctx.tracer.add("decision", "gated_action_blocked", {"tool": tool, "prerequisite": pre["before"], "error": call.error})
                    notes.append(f"I could not complete {pre['before'].replace('_', ' ')} ({call.error['message']}), so I have not made any changes.")
                    flags.append("declined")
                    return {**out, "notes": notes, "flags": flags}
                notes.append(call.result.get("message", ""))
        if len(ctx.gateway.calls) >= ctx.settings["max_steps"]:
            return _halt(out, notes, flags)
        call = ctx.gateway.call(tool, args, "injected" if step["kind"] == "injected" else step["kind"], idx)
        ctx.results[tool].append(call)
        ctx.model_call(f"after:{tool}", {"status": call.status}, str(call.result or call.error))
        if call.status == "ok":
            notes.append(call.result.get("message", ""))
        else:
            notes.append(f"I wasn't able to complete {tool.replace('_', ' ')}: {call.error['message']}.")
            if spec.side_effect == "write" or ctx.settings.get("treat_tool_errors_as_blocking"):
                flags.append("tool_error")
                if ctx.settings.get("treat_tool_errors_as_blocking"):
                    ctx.tracer.add("decision", "halt_on_tool_error", {"tool": tool, "error": call.error})
                    return {**out, "idx": 10_000, "halted": "tool_error", "notes": notes, "flags": flags}
        return {**out, "notes": notes, "flags": flags}

    def _halt(out: GraphState, notes: list, flags: list) -> GraphState:
        ctx.tracer.add("decision", "max_steps_reached", {"max_steps": ctx.settings["max_steps"]}, status="error")
        return {**out, "idx": 10_000, "halted": "max_steps", "notes": notes, "flags": flags + ["incomplete"]}

    def route(state: GraphState) -> str:
        return "act" if state["idx"] < len(state["steps"]) and not state.get("halted") else "respond"

    def respond(state: GraphState) -> GraphState:
        return {}

    g = StateGraph(GraphState)
    g.add_node("interpret", interpret)
    g.add_node("retrieve", do_retrieve)
    g.add_node("plan", plan)
    g.add_node("act", act)
    g.add_node("respond", respond)
    g.set_entry_point("interpret")
    g.add_edge("interpret", "retrieve")
    g.add_edge("retrieve", "plan")
    g.add_conditional_edges("plan", route, {"act": "act", "respond": "respond"})
    g.add_conditional_edges("act", route, {"act": "act", "respond": "respond"})
    g.add_edge("respond", END)
    return g.compile()


def _compose(ctx: RunContext, state: GraphState) -> tuple[str, str]:
    calls = ctx.gateway.calls
    flags = state.get("flags", [])
    escalated = any(c.tool.startswith("escalate") and c.status == "ok" for c in calls)
    if escalated:
        outcome = "escalated"
    elif "declined" in flags:
        outcome = "declined"
    elif "incomplete" in flags or "tool_error" in flags:
        outcome = "incomplete"
    else:
        outcome = "resolved"
    parts = [n for n in state.get("notes", []) if n]
    article = state.get("article")
    if state.get("mode") == "unsupported_answer":
        parts = [GENERIC_ANSWER]
    elif state.get("mode") in ("fallback_escalation", "low_confidence_escalation") and escalated:
        parts.insert(0, "I couldn't find guidance I'm confident about, so I've passed this to a specialist team.")
    if article and outcome != "declined":
        parts.append(article["resolution_summary"])
    if article:
        parts.append(f"(Source: {article['id']})")
    return " ".join(parts).strip(), outcome


def execute_case(env_name: str, tenant_id: str, config: dict, case: dict, provider=None) -> dict:
    """Run one case end-to-end and return response, outcome, tool calls, state and trace."""
    wall0 = time.perf_counter()
    ctx = RunContext(env_name, tenant_id, config, case, provider)
    ctx.tracer.add("input", "user_message", {"message": ctx.message, "requester": ctx.requester, "tenant": tenant_id,
                                             "config_version": config.get("version"), "faults": case.get("faults") or {}})
    if ctx.settings.get("model_provider", "simlm") == "simlm":
        graph = _build_graph(ctx)
        state = graph.invoke({}, config={"recursion_limit": 200})
        response, outcome = _compose(ctx, state)
    else:
        from .llm_agent import run_llm_agent
        response, outcome = run_llm_agent(ctx)
    for ch in ctx.sandbox.changes:
        ctx.tracer.add("state_change", ch["path"], ch)
    ctx.tracer.add("response", "final_answer", {"response": response, "outcome": outcome})
    tool_ms = sum(c.latency_ms for c in ctx.gateway.calls)
    trace = ctx.tracer.export()
    return {
        "response": response,
        "outcome": outcome,
        "tool_calls": [c.as_dict() for c in ctx.gateway.calls],
        "retrieved": [{k: d[k] for k in ("id", "title", "score", "source")} for d in ctx.retrieved],
        "evidence_text": " ".join([d["body"] + " " + " ".join(s["text"] for s in d["article"]["steps"]) + " " + d["article"]["resolution_summary"]
                                   for d in ctx.retrieved] + [str((c.get("result") or {}).get("message", "")) for c in
                                                              [x.as_dict() for x in ctx.gateway.calls]]),
        "state": ctx.sandbox.state,
        "state_changes": ctx.sandbox.changes,
        "tokens": ctx.tokens,
        "input_tokens": ctx.input_tokens,
        "output_tokens": ctx.output_tokens,
        "llm_calls": ctx.llm_calls,
        "latency_ms": round(trace["modeled_ms"], 1),
        "tool_latency_ms": round(tool_ms, 1),
        "cost_usd": round(ctx.tokens / 1000 * float(ctx.settings["cost_per_1k_tokens"]), 6),
        "wall_ms": round((time.perf_counter() - wall0) * 1000, 2),
        "trace": trace,
        "target_employee": ctx.target,
    }
