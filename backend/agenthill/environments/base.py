"""Simulated enterprise environments (sandboxes) that demonstration agents act on.

Every evaluation case gets a fresh, tenant-scoped copy of the synthetic data, so state
changes made by one case can never leak into another case, another tenant, or a real
system. There is no production backend: the gateway refuses anything but `sandbox`.
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

DATA_DIR = Path(__file__).resolve().parents[3] / "data"


class ToolError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class ParamSpec:
    name: str
    type: str  # string | enum | boolean | date
    description: str = ""
    options: list[str] | None = None
    default: Any = None
    required: bool = True


@dataclass
class ToolSpec:
    name: str
    side_effect: str  # read | write
    default_description: str
    params: list[ParamSpec]
    fn: Callable[["Sandbox", dict], dict]

    def schema(self) -> dict:
        return {
            "name": self.name,
            "side_effect": self.side_effect,
            "default_description": self.default_description,
            "parameters": [p.__dict__ for p in self.params],
        }


@dataclass
class Sandbox:
    environment: "Environment"
    tenant_id: str
    state: dict
    faults: dict = field(default_factory=dict)
    changes: list = field(default_factory=list)

    def employee(self, employee_id: str | None) -> dict:
        emp = self.state["employees"].get((employee_id or "").upper())
        if emp is None:
            # Deliberately identical for "exists in another tenant" and "does not exist".
            raise ToolError("not_found", f"No employee {employee_id} in this organisation")
        return emp

    def record_change(self, path: str, before: Any, after: Any) -> None:
        self.changes.append({"path": path, "before": before, "after": after})

    def set(self, obj: dict, key: str, value: Any, path: str) -> None:
        before = obj.get(key)
        obj[key] = value
        if before != value:
            self.record_change(path, before, value)

    def get_path(self, path: str) -> Any:
        cur: Any = self.state
        for part in path.split("."):
            if isinstance(cur, dict):
                cur = cur.get(part) if part in cur else cur.get(part.upper())
            elif isinstance(cur, list) and part == "count":
                return len(cur)
            else:
                return None
        return cur


class Environment:
    name: str = ""
    tools: dict[str, ToolSpec]

    def __init__(self) -> None:
        self.data = json.loads((DATA_DIR / "environments" / f"{self.name}.json").read_text())
        self.knowledge = json.loads((DATA_DIR / "knowledge" / self.kb_file).read_text())
        self.tools = {t.name: t for t in self.build_tools()}

    kb_file: str = ""

    def build_tools(self) -> list[ToolSpec]:  # pragma: no cover - abstract
        raise NotImplementedError

    def tenants(self) -> list[str]:
        return list(self.data["tenants"].keys())

    def new_sandbox(self, tenant_id: str, faults: dict | None = None) -> Sandbox:
        if tenant_id not in self.data["tenants"]:
            raise ToolError("unknown_tenant", f"Tenant {tenant_id} has no sandbox data")
        return Sandbox(self, tenant_id, copy.deepcopy(self.data["tenants"][tenant_id]), faults or {})

    def latency_ms(self, tool: str) -> int:
        return int(self.data.get("tool_latency_ms", {}).get(tool, 150))

    def catalog(self) -> list[dict]:
        return [t.schema() for t in self.tools.values()]


_REGISTRY: dict[str, Environment] = {}


def get_environment(name: str) -> Environment:
    from .hr_services import HREnvironment
    from .it_service_desk import ITEnvironment

    classes = {"it_service_desk": ITEnvironment, "hr_services": HREnvironment}
    if name not in classes:
        raise KeyError(f"Unknown environment {name}")
    if name not in _REGISTRY:
        _REGISTRY[name] = classes[name]()
    return _REGISTRY[name]


def list_environments() -> list[dict]:
    out = []
    for name in ("it_service_desk", "hr_services"):
        env = get_environment(name)
        out.append({"name": name, "description": env.data.get("description", ""), "tools": env.catalog(),
                    "knowledge_articles": len(env.knowledge)})
    return out
