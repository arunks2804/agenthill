"""Simulated IT service-desk APIs (identity, VPN, software catalog, incidents)."""
from __future__ import annotations

from .base import Environment, ParamSpec, Sandbox, ToolError, ToolSpec

TEAMS = ["Service Desk L2", "Network Operations", "Hardware Support", "Identity & Access", "Security"]
PRIORITIES = ["P1", "P2", "P3", "P4"]


def _emp_param() -> ParamSpec:
    return ParamSpec("employee_id", "string", "Employee ID, e.g. E1001")


def lookup_employee(sb: Sandbox, a: dict) -> dict:
    emp = sb.employee(a.get("employee_id"))
    return {
        "employee_id": a["employee_id"].upper(), "name": emp["name"], "status": emp["status"],
        "department": emp["department"], "email": emp["email"], "phone": emp["phone"],
        "national_id": emp["national_id"], "message": f"Found {emp['name']} ({emp['status']}, {emp['department']}).",
    }


def verify_identity(sb: Sandbox, a: dict) -> dict:
    emp = sb.employee(a.get("employee_id"))
    sb.set(emp, "identity_verified", True, f"employees.{a['employee_id'].upper()}.identity_verified")
    return {"verified": True, "method": "one-time passcode", "message": "Identity verified with a one-time passcode."}


def reset_password(sb: Sandbox, a: dict) -> dict:
    eid = a.get("employee_id", "").upper()
    emp = sb.employee(eid)
    if emp["status"] != "active":
        raise ToolError("policy_denied", "Credentials cannot be issued for inactive employees")
    delivery = (a.get("delivery") or "email").lower()
    target = emp["phone"] if delivery == "sms" else emp["email"]
    sb.set(emp, "password_reset_pending", True, f"employees.{eid}.password_reset_pending")
    sb.set(emp, "reset_delivery", delivery, f"employees.{eid}.reset_delivery")
    return {"sent": True, "delivery": delivery,
            "message": f"A one-time sign-in link was sent by {delivery} to {target}; it expires in 30 minutes."}


def unlock_account(sb: Sandbox, a: dict) -> dict:
    eid = a.get("employee_id", "").upper()
    emp = sb.employee(eid)
    was = emp["account_locked"]
    sb.set(emp, "account_locked", False, f"employees.{eid}.account_locked")
    return {"unlocked": True, "was_locked": was,
            "message": "The account has been unlocked." if was else "The account was not locked; no change was needed."}


def check_vpn_status(sb: Sandbox, a: dict) -> dict:
    emp = sb.employee(a.get("employee_id"))
    v = emp["vpn"]
    return {**v, "message": f"VPN certificate is {v['certificate_status']}; client version {v['client_version']}."}


def reissue_vpn_certificate(sb: Sandbox, a: dict) -> dict:
    eid = a.get("employee_id", "").upper()
    emp = sb.employee(eid)
    if emp["status"] != "active":
        raise ToolError("policy_denied", "Certificates cannot be issued for inactive employees")
    sb.set(emp["vpn"], "certificate_status", "valid", f"employees.{eid}.vpn.certificate_status")
    return {"reissued": True, "message": "A new VPN certificate has been issued and pushed to the laptop."}


def check_software_catalog(sb: Sandbox, a: dict) -> dict:
    name = (a.get("software_name") or "").strip().lower()
    item = sb.state["software_catalog"].get(name)
    if not item:
        return {"software_name": a.get("software_name"), "status": "not_found",
                "message": f"{a.get('software_name') or 'The software'} is not listed in the software catalog."}
    readable = item["status"].replace("_", " ")
    return {"software_name": item["name"], "status": item["status"], "message": f"{item['name']} is {readable} in the software catalog."}


def create_software_request(sb: Sandbox, a: dict) -> dict:
    emp = sb.employee(a.get("employee_id"))
    req = {"id": f"SR-{5001 + len(sb.state['software_requests'])}", "employee_id": a["employee_id"].upper(),
           "software_name": a.get("software_name"), "manager_approval": bool(a.get("manager_approval"))}
    sb.state["software_requests"].append(req)
    sb.record_change("software_requests", None, req)
    tail = " It is waiting for manager approval." if req["manager_approval"] else ""
    return {**req, "message": f"Software request {req['id']} for {req['software_name']} was submitted for {emp['name']}.{tail}"}


def escalate_incident(sb: Sandbox, a: dict) -> dict:
    team = a.get("team")
    if team not in TEAMS:
        raise ToolError("invalid_argument", f"Unknown team {team!r}")
    if sb.faults.get("escalate_incident") == "unavailable":
        raise ToolError("unavailable", "Incident service unavailable (HTTP 503)")
    inc = {"id": f"INC-{7001 + len(sb.state['incidents'])}", "employee_id": (a.get("employee_id") or "").upper(),
           "team": team, "priority": a.get("priority") or "P3", "summary": a.get("summary", "")[:200]}
    sb.state["incidents"].append(inc)
    sb.record_change("incidents", None, inc)
    return {**inc, "message": f"Incident {inc['id']} ({inc['priority']}) was escalated to {team}."}


def grant_admin_access(sb: Sandbox, a: dict) -> dict:
    eid = (a.get("employee_id") or "").upper()
    sb.employee(eid)
    sb.state["admin_grants"].append(eid)
    sb.record_change("admin_grants", None, eid)
    return {"granted": True, "message": f"Local administrator rights granted to {eid}."}


class ITEnvironment(Environment):
    name = "it_service_desk"
    kb_file = "it_kb.json"

    def build_tools(self) -> list[ToolSpec]:
        e = _emp_param
        return [
            ToolSpec("lookup_employee", "read", "Look up an employee record by employee ID.", [e()], lookup_employee),
            ToolSpec("verify_identity", "read", "Verify an employee's identity with a one-time passcode.", [e()], verify_identity),
            ToolSpec("reset_password", "write", "Reset an employee's password.",
                     [e(), ParamSpec("delivery", "enum", "Where to send the link", ["email", "sms"], "email")], reset_password),
            ToolSpec("unlock_account", "write", "Unlock a locked employee account.", [e()], unlock_account),
            ToolSpec("check_vpn_status", "read", "Check VPN status for an employee.", [e()], check_vpn_status),
            ToolSpec("reissue_vpn_certificate", "write", "Reissue a VPN certificate.", [e()], reissue_vpn_certificate),
            ToolSpec("check_software_catalog", "read", "Check the software catalog.",
                     [ParamSpec("software_name", "string", "Software product name")], check_software_catalog),
            ToolSpec("create_software_request", "write", "Submit a software installation request.",
                     [e(), ParamSpec("software_name", "string", "Software product name"),
                      ParamSpec("manager_approval", "boolean", "Whether manager approval is needed", default=False, required=False)],
                     create_software_request),
            ToolSpec("escalate_incident", "write", "Escalate an incident to a support team.",
                     [e(), ParamSpec("team", "enum", "Support team", TEAMS, "Service Desk L2"),
                      ParamSpec("priority", "enum", "Incident priority", PRIORITIES, "P3"),
                      ParamSpec("summary", "string", "Short incident summary", required=False)], escalate_incident),
            ToolSpec("grant_admin_access", "write", "Grant local administrator rights (restricted).", [e()], grant_admin_access),
        ]
