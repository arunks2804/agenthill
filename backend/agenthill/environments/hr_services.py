"""Simulated HR services APIs (profiles, leave, benefits, onboarding, HR cases)."""
from __future__ import annotations

from datetime import date

from .base import Environment, ParamSpec, Sandbox, ToolError, ToolSpec

PLANS = ["medical", "dental", "vision", "401k"]
CATEGORIES = ["payroll", "personal_details", "leave", "benefits", "general"]


def _emp_param() -> ParamSpec:
    return ParamSpec("employee_id", "string", "Employee ID, e.g. E1001")


def get_employee_profile(sb: Sandbox, a: dict) -> dict:
    emp = sb.employee(a.get("employee_id"))
    return {**{k: emp[k] for k in ("name", "status", "employment_type", "start_date", "location", "salary_band", "base_salary", "email", "national_id")},
            "message": f"{emp['name']} is a {emp['employment_type'].replace('_', '-')} employee in {emp['location']}."}


def check_leave_balance(sb: Sandbox, a: dict) -> dict:
    emp = sb.employee(a.get("employee_id"))
    days = emp["leave_balance_days"]
    return {"employee_id": a["employee_id"].upper(), "balance_days": days, "employment_type": emp["employment_type"],
            "message": f"Your current annual leave balance is {days:g} days."}


def check_benefits_eligibility(sb: Sandbox, a: dict) -> dict:
    emp = sb.employee(a.get("employee_id"))
    plan = (a.get("plan") or "").lower()
    rules = sb.state["benefit_rules"]
    if plan not in rules:
        raise ToolError("invalid_argument", f"Unknown plan {plan!r}")
    rule = rules[plan]
    service_days = (date.fromisoformat(sb.environment.data["today"]) - date.fromisoformat(emp["start_date"])).days
    if emp["employment_type"] not in rule["eligible_types"]:
        eligible, reason = False, f"{emp['employment_type'].replace('_', '-')} workers are not eligible for the {plan} plan"
    elif service_days < rule["waiting_days"]:
        eligible, reason = False, f"the {plan} plan has a {rule['waiting_days']}-day waiting period"
    else:
        eligible, reason = True, f"{emp['employment_type'].replace('_', '-')} employees are eligible for the {plan} plan"
    verdict = "You are eligible" if eligible else "You are not eligible"
    return {"plan": plan, "eligible": eligible, "reason": reason, "message": f"{verdict} for the {plan} plan: {reason}."}


def create_hr_ticket(sb: Sandbox, a: dict) -> dict:
    emp = sb.employee(a.get("employee_id"))
    cat = a.get("category") or "general"
    if cat not in CATEGORIES:
        raise ToolError("invalid_argument", f"Unknown category {cat!r}")
    t = {"id": f"HR-{3001 + len(sb.state['hr_tickets'])}", "employee_id": a["employee_id"].upper(), "category": cat,
         "summary": (a.get("summary") or "")[:200]}
    sb.state["hr_tickets"].append(t)
    sb.record_change("hr_tickets", None, t)
    return {**t, "message": f"HR ticket {t['id']} ({cat.replace('_', ' ')}) was opened for {emp['name']}."}


def start_onboarding(sb: Sandbox, a: dict) -> dict:
    sb.employee(a.get("employee_id"))  # the requesting hiring manager must exist in this tenant
    if not a.get("start_date"):
        raise ToolError("invalid_argument", "start_date is required (YYYY-MM-DD)")
    rec = {"id": f"ONB-{4001 + len(sb.state['onboarding'])}", "manager_id": a["employee_id"].upper(),
           "new_hire_name": a.get("new_hire_name") or "New hire", "start_date": a["start_date"]}
    sb.state["onboarding"].append(rec)
    sb.record_change("onboarding", None, rec)
    return {**rec, "message": f"Onboarding checklist {rec['id']} was started for {rec['new_hire_name']} starting {rec['start_date']}."}


def escalate_to_hr_partner(sb: Sandbox, a: dict) -> dict:
    sb.employee(a.get("employee_id"))
    c = {"id": f"HRBP-{6001 + len(sb.state['hr_partner_cases'])}", "employee_id": a["employee_id"].upper(),
         "confidential": bool(a.get("confidential")), "reason": (a.get("reason") or "")[:200]}
    sb.state["hr_partner_cases"].append(c)
    sb.record_change("hr_partner_cases", None, c)
    return {**c, "message": f"Case {c['id']} was escalated to an HR Business Partner, who will contact you within one business day."}


class HREnvironment(Environment):
    name = "hr_services"
    kb_file = "hr_kb.json"

    def build_tools(self) -> list[ToolSpec]:
        e = _emp_param
        return [
            ToolSpec("get_employee_profile", "read", "Get an employee's HR profile.", [e()], get_employee_profile),
            ToolSpec("check_leave_balance", "read", "Check an employee's leave balance.", [e()], check_leave_balance),
            ToolSpec("check_benefits_eligibility", "read", "Check benefits eligibility.",
                     [e(), ParamSpec("plan", "enum", "Benefit plan", PLANS, "medical")], check_benefits_eligibility),
            ToolSpec("create_hr_ticket", "write", "Open an HR ticket.",
                     [e(), ParamSpec("category", "enum", "Ticket category", CATEGORIES, "general"),
                      ParamSpec("summary", "string", "Ticket summary", required=False)], create_hr_ticket),
            ToolSpec("start_onboarding", "write", "Start a new-hire onboarding checklist.",
                     [e(), ParamSpec("new_hire_name", "string", "New hire's full name", required=False),
                      ParamSpec("start_date", "date", "Start date YYYY-MM-DD")], start_onboarding),
            ToolSpec("escalate_to_hr_partner", "write", "Escalate a case to an HR Business Partner.",
                     [e(), ParamSpec("reason", "string", "Reason", required=False),
                      ParamSpec("confidential", "boolean", "Handle confidentially", default=False, required=False)],
                     escalate_to_hr_partner),
        ]
