"""Tool gateway: the only path from an agent to (simulated) enterprise APIs.

Enforces: sandbox-only execution, the agent's enabled-tool allow-list, tenant scoping (via the
tenant-specific sandbox), case-level fault injection, timing capture and untrusted-output labelling.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from ..environments.base import Environment, Sandbox, ToolError
from .tracing import Tracer


@dataclass
class ToolCall:
    tool: str
    args: dict
    status: str  # ok | error | blocked
    result: dict | None
    error: dict | None
    latency_ms: float
    origin: str  # plan | prerequisite | injected | fallback | llm
    step: int | None = None

    def as_dict(self) -> dict:
        return self.__dict__.copy()


class ToolGateway:
    def __init__(self, env: Environment, sandbox: Sandbox, enabled_tools: set[str], tracer: Tracer,
                 execution_mode: str = "sandbox"):
        if execution_mode != "sandbox":
            raise PermissionError("AgentHill only executes agents against sandbox environments")
        self.env, self.sandbox, self.enabled, self.tracer = env, sandbox, enabled_tools, tracer
        self.calls: list[ToolCall] = []

    def call(self, tool: str, args: dict, origin: str, step: int | None = None) -> ToolCall:
        t0 = time.perf_counter()
        spec = self.env.tools.get(tool)
        if spec is None or tool not in self.enabled:
            reason = "unknown tool" if spec is None else "tool is not enabled for this agent"
            call = ToolCall(tool, args, "blocked", None, {"code": "permission_denied", "message": reason}, 0.0, origin, step)
            self.calls.append(call)
            self.tracer.add("guardrail", f"blocked:{tool}", {"tool": tool, "args": args, "reason": reason, "origin": origin}, status="blocked")
            return call
        fault = self.sandbox.faults.get(tool)
        latency = float(self.env.latency_ms(tool))
        try:
            if fault == "timeout":
                latency = 5000.0
                raise ToolError("timeout", f"{tool} timed out after 5000 ms")
            result = spec.fn(self.sandbox, dict(args))
            call = ToolCall(tool, args, "ok", result, None, latency, origin, step)
        except ToolError as e:
            call = ToolCall(tool, args, "error", None, {"code": e.code, "message": e.message}, latency, origin, step)
        call.latency_ms = latency
        self.calls.append(call)
        self.tracer.add("tool_call", tool, {
            "tool": tool, "side_effect": spec.side_effect, "args": args, "origin": origin, "step": step,
            "result": call.result, "error": call.error, "untrusted_output": True,
            "gateway_overhead_ms": round((time.perf_counter() - t0) * 1000, 3),
        }, modeled_ms=latency, status=call.status)
        return call

    def succeeded(self, tool: str, employee_id: str | None = None) -> bool:
        return any(c.tool == tool and c.status == "ok" and (employee_id is None or c.args.get("employee_id") == employee_id)
                   for c in self.calls)
