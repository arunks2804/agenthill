"""Trace capture for agent executions.

Spans follow OpenTelemetry naming (trace_id, span_id, parent_span_id, name, start/end, attributes)
so a trace can be exported as OTLP-style JSON. Only observable behaviour is recorded: inputs,
retrieved evidence, decisions the runtime exposes, tool calls, results, errors and state changes.
Hidden model reasoning is never assumed to be available.
"""
from __future__ import annotations

import time
import uuid
from typing import Any


class Tracer:
    def __init__(self, name: str):
        self.trace_id = uuid.uuid4().hex
        self.root_id = uuid.uuid4().hex[:16]
        self.t0 = time.perf_counter()
        self.modeled_clock_ms = 0.0
        self.spans: list[dict[str, Any]] = []
        self.root_name = name

    def _now(self) -> float:
        return round((time.perf_counter() - self.t0) * 1000, 3)

    def add(self, kind: str, name: str, data: dict | None = None, modeled_ms: float = 0.0, status: str = "ok") -> dict:
        span = {
            "seq": len(self.spans) + 1,
            "span_id": uuid.uuid4().hex[:16],
            "parent_span_id": self.root_id,
            "kind": kind,
            "name": name,
            "status": status,
            "wall_offset_ms": self._now(),
            "modeled_start_ms": round(self.modeled_clock_ms, 1),
            "modeled_duration_ms": round(modeled_ms, 1),
            "data": data or {},
        }
        self.modeled_clock_ms += modeled_ms
        self.spans.append(span)
        return span

    def export(self) -> dict:
        return {"trace_id": self.trace_id, "root_span_id": self.root_id, "name": self.root_name,
                "wall_ms": self._now(), "modeled_ms": round(self.modeled_clock_ms, 1), "spans": self.spans}


def to_otlp(trace: dict) -> dict:
    """Convert a stored trace into an OTLP/JSON-like resourceSpans document."""
    def attrs(d: dict) -> list:
        out = []
        for k, v in d.items():
            out.append({"key": k, "value": {"stringValue": v if isinstance(v, str) else repr(v)}})
        return out

    spans = [{
        "traceId": trace["trace_id"], "spanId": trace["root_span_id"], "name": trace["name"],
        "startTimeUnixNano": 0, "endTimeUnixNano": int(trace.get("modeled_ms", 0) * 1e6), "attributes": [],
    }]
    for s in trace.get("spans", []):
        spans.append({
            "traceId": trace["trace_id"], "spanId": s["span_id"], "parentSpanId": s["parent_span_id"],
            "name": f"{s['kind']}:{s['name']}",
            "startTimeUnixNano": int(s["modeled_start_ms"] * 1e6),
            "endTimeUnixNano": int((s["modeled_start_ms"] + s["modeled_duration_ms"]) * 1e6),
            "status": {"code": "STATUS_CODE_ERROR" if s["status"] in ("error", "blocked") else "STATUS_CODE_OK"},
            "attributes": attrs({"agenthill.kind": s["kind"], **{f"agenthill.{k}": v for k, v in s["data"].items()}}),
        })
    return {"resourceSpans": [{"resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "agenthill-agent-runtime"}}]},
                               "scopeSpans": [{"scope": {"name": "agenthill"}, "spans": spans}]}]}
