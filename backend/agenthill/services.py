"""Application services: Agent Registry, Dataset Management, Runs, Analysis, Recommendations,
Experimentation, Regression & Learning, History. API routes and scripts call these functions; they
are the structured contracts between components."""
from __future__ import annotations

import json
import random
import threading
import time
from datetime import datetime

from sqlalchemy import or_

from .analysis import failure_analyzer
from .config import DATA_DIR
from .db import (Agent, AgentConfig, AuditEvent, CaseResult, Dataset, DatasetVersion, EvalRun, Experiment, FailureAnalysis,
                 HumanReview, Recommendation, SessionLocal, Tenant, TestCase, utcnow)
from .environments.base import get_environment
from .evaluation import engine
from .gates.engine import detect_regressions, evaluate_gates
from .llm.base import LLMError
from .llm.registry import DETERMINISTIC
from .platform_settings import get_setting, provider_for, role_spec
from .recommendations import engine as rec_engine

CATEGORIES = {"normal", "edge", "failure", "adversarial", "regression"}
EXPECTED_KEYS = {"outcome", "outcome_any_of", "required_tools", "allowed_tools", "prohibited_tools", "order", "args", "state",
                 "answer_facts", "must_not_contain", "expects_uncertainty"}
USERS = {
    "maya": {"name": "Maya Chen", "role": "ai_product_manager", "title": "AI Product Manager"},
    "leo": {"name": "Leo Martins", "role": "ml_engineer", "title": "AI/ML Engineer"},
    "ana": {"name": "Ana Ruiz", "role": "evaluation_engineer", "title": "Evaluation Engineer"},
    "sam": {"name": "Sam Okoye", "role": "domain_expert", "title": "Business / Domain Expert"},
}
APPROVER_ROLES = {"ai_product_manager", "evaluation_engineer"}


class ServiceError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message, self.status = message, status


def audit(db, tenant: str, actor: str, action: str, etype: str, eid, details: dict | None = None) -> None:
    db.add(AuditEvent(tenant_id=tenant, actor=actor, action=action, entity_type=etype, entity_id=str(eid), details=details or {}))


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat() + "Z" if dt else None


def require_role(actor: str, roles: set[str], what: str) -> None:
    if USERS.get(actor, {}).get("role") not in roles:
        raise ServiceError(f"{USERS.get(actor, {}).get('title', actor)} cannot {what}; requires {' or '.join(sorted(roles))}", 403)


def get_owned(db, model, oid, tenant: str):
    obj = db.get(model, oid)
    owner = getattr(obj, "tenant_id", None) if obj is not None else None
    if obj is not None and owner is None and hasattr(obj, "agent_id"):
        owner = db.get(Agent, obj.agent_id).tenant_id
    if obj is None or owner != tenant:
        raise ServiceError(f"{model.__name__} {oid} not found", 404)  # same answer for other tenants: no existence leak
    return obj


# ----------------------------------------------------------------------------- serialisers
def agent_out(db, a: Agent) -> dict:
    cfgs = db.query(AgentConfig).filter_by(agent_id=a.id).order_by(AgentConfig.version).all()
    cur = next((c for c in cfgs if c.id == a.current_config_id), None)
    return {"id": a.id, "tenant_id": a.tenant_id, "name": a.name, "domain": a.domain, "description": a.description,
            "environment": a.environment, "template_id": a.template_id, "intended_tasks": a.intended_tasks,
            "expected_outcomes": a.expected_outcomes, "evaluation_criteria": a.evaluation_criteria, "quality_gates": a.quality_gates,
            "current_config_id": a.current_config_id, "current_config_version": cur.version if cur else None,
            "configs": [config_out(c) for c in cfgs], "created_by": a.created_by, "created_at": iso(a.created_at)}


def config_out(c: AgentConfig) -> dict:
    return {"id": c.id, "agent_id": c.agent_id, "version": c.version, "label": c.label, "system_prompt": c.system_prompt, "tools": c.tools,
            "settings": c.settings, "parent_id": c.parent_id, "status": c.status, "change_summary": c.change_summary,
            "created_by": c.created_by, "created_at": iso(c.created_at)}


def run_out(r: EvalRun, db=None) -> dict:
    d = {"id": r.id, "agent_id": r.agent_id, "config_id": r.config_id, "dataset_version_id": r.dataset_version_id, "purpose": r.purpose,
         "experiment_id": r.experiment_id, "status": r.status, "progress": r.progress, "models": r.models, "metrics": r.metrics,
         "gate_verdict": r.gate_verdict, "error": json.loads(r.error) if r.error and r.error.startswith("{") else r.error,
         "created_by": r.created_by, "created_at": iso(r.created_at), "finished_at": iso(r.finished_at), "duration_ms": r.duration_ms}
    if db is not None:
        cfg = db.get(AgentConfig, r.config_id)
        dv = db.get(DatasetVersion, r.dataset_version_id)
        ds = db.get(Dataset, dv.dataset_id) if dv else None
        d.update({"config_version": cfg.version if cfg else None, "config_label": cfg.label if cfg else "",
                  "dataset_version": dv.version if dv else None, "dataset_name": ds.name if ds else ""})
    return d


def result_out(r: CaseResult, full: bool = False) -> dict:
    d = {"id": r.id, "run_id": r.run_id, "case_key": r.case_key, "category": r.case.get("category"), "severity": r.case.get("severity"),
         "message": r.case["input"]["message"], "passed": r.passed, "status": r.status, "outcome": r.outcome, "score": r.score,
         "critical": r.critical, "needs_review": r.needs_review, "latency_ms": r.latency_ms, "tokens": r.tokens, "cost_usd": r.cost_usd,
         "failed_checks": [c["name"] for c in r.checks if c["status"] == "fail"], "judge_overall": (r.judge or {}).get("overall"),
         "tools": [c["tool"] for c in r.tool_calls]}
    if full:
        d.update({"case": r.case, "response": r.response, "checks": r.checks, "judge": r.judge, "tool_calls": r.tool_calls,
                  "retrieved": r.retrieved, "state_changes": r.state_changes, "trace": r.trace})
    return d


def rec_out(r: Recommendation) -> dict:
    return {c.name: (iso(getattr(r, c.name)) if isinstance(getattr(r, c.name), datetime) else getattr(r, c.name)) for c in r.__table__.columns}


def exp_out(e: Experiment) -> dict:
    return {c.name: (iso(getattr(e, c.name)) if isinstance(getattr(e, c.name), datetime) else getattr(e, c.name)) for c in e.__table__.columns}


# ----------------------------------------------------------------------------- tenants & templates
def ensure_tenants(db) -> None:
    for tid, name in (("acme", "Acme Corp"), ("globex", "Globex Inc")):
        if db.get(Tenant, tid) is None:
            db.add(Tenant(id=tid, name=name))
    db.commit()


def load_template(template_id: str) -> dict:
    path = DATA_DIR / "templates" / f"{template_id}.json"
    if not path.exists():
        raise ServiceError(f"Unknown template {template_id}", 404)
    return json.loads(path.read_text())


def list_templates() -> list[dict]:
    out = []
    for p in sorted((DATA_DIR / "templates").glob("*.json")):
        t = json.loads(p.read_text())
        out.append({"template_id": t["template_id"], "name": t["name"], "domain": t["domain"], "environment": t["environment"],
                    "description": t["description"], "template": t})
    return out


# ----------------------------------------------------------------------------- Agent Registry
def validate_agent_payload(p: dict) -> None:
    for k in ("name", "domain", "environment", "system_prompt", "tools", "settings"):
        if not p.get(k) and p.get(k) != {}:
            raise ServiceError(f"Missing field: {k}")
    env = get_environment(p["environment"])
    unknown = [t["name"] for t in p["tools"] if t["name"] not in env.tools]
    if unknown:
        raise ServiceError(f"Tools not offered by environment {p['environment']}: {unknown}")


def register_agent(db, tenant: str, actor: str, p: dict) -> Agent:
    validate_agent_payload(p)
    name = p["name"].strip()
    if any(x.name.strip().casefold() == name.casefold() for x in db.query(Agent).filter_by(tenant_id=tenant)):
        raise ServiceError(f"An agent named “{name}” is already registered for this customer. "
                           "Select it in the Agent menu; improvements are added to it as new versions.", 409)
    p = {**p, "name": name}
    a = Agent(tenant_id=tenant, name=p["name"], domain=p["domain"], description=p.get("description", ""), environment=p["environment"],
              template_id=p.get("template_id"), intended_tasks=p.get("intended_tasks", []), expected_outcomes=p.get("expected_outcomes", []),
              evaluation_criteria=p.get("evaluation_criteria") or {"judge_pass_threshold": 0.6, "weights": {"deterministic": 0.6, "judge": 0.4}},
              quality_gates=p.get("quality_gates") or {}, created_by=actor)
    db.add(a)
    db.flush()
    c = AgentConfig(agent_id=a.id, version=1, label=p.get("config_label") or "v1 baseline", system_prompt=p["system_prompt"],
                    tools=p["tools"], settings=p["settings"], status="baseline", created_by=actor, change_summary=["Initial configuration"])
    db.add(c)
    db.flush()
    a.current_config_id = c.id
    audit(db, tenant, actor, "agent.registered", "agent", a.id, {"name": a.name, "environment": a.environment})
    db.commit()
    return a


def register_from_template(db, tenant: str, actor: str, template_id: str, load_dataset: bool = True) -> Agent:
    t = load_template(template_id)
    bc = t["baseline_config"]
    a = register_agent(db, tenant, actor, {**t, "template_id": template_id, "system_prompt": bc["system_prompt"], "tools": bc["tools"],
                                           "settings": bc["settings"], "config_label": bc.get("label")})
    if load_dataset:
        ds_file = json.loads((DATA_DIR / "datasets" / t["dataset_file"]).read_text())
        ds = create_dataset(db, tenant, actor, a.id, ds_file["name"], ds_file.get("description", ""))
        import_cases(db, tenant, actor, ds.id, ds_file["cases"], source="curated")
        publish_version(db, tenant, actor, ds.id, "Initial curated golden set")
    return a


def new_config_version(db, agent: Agent, base: AgentConfig, new: dict, actor: str, label: str, status: str, summary: list[str]) -> AgentConfig:
    ver = (db.query(AgentConfig).filter_by(agent_id=agent.id).count()) + 1
    c = AgentConfig(agent_id=agent.id, version=ver, label=label, system_prompt=new["system_prompt"], tools=new["tools"],
                    settings=new["settings"], parent_id=base.id, status=status, created_by=actor, change_summary=summary)
    db.add(c)
    db.flush()
    audit(db, agent.tenant_id, actor, "config.created", "agent_config", c.id, {"version": ver, "status": status, "changes": summary})
    return c


# ----------------------------------------------------------------------------- Datasets
def create_dataset(db, tenant: str, actor: str, agent_id: int, name: str, description: str = "", kind: str = "golden") -> Dataset:
    get_owned(db, Agent, agent_id, tenant)
    ds = Dataset(tenant_id=tenant, agent_id=agent_id, name=name, description=description, kind=kind)
    db.add(ds)
    db.flush()
    audit(db, tenant, actor, "dataset.created", "dataset", ds.id, {"name": name, "kind": kind})
    db.commit()
    return ds


def validate_case(c: dict, env_name: str) -> list[str]:
    errs = []
    for k in ("case_key", "category", "input", "expected"):
        if k not in c:
            errs.append(f"missing {k}")
    if errs:
        return errs
    if c["category"] not in CATEGORIES:
        errs.append(f"invalid category {c['category']!r}")
    if not isinstance(c["input"], dict) or not c["input"].get("message") or not c["input"].get("employee_id"):
        errs.append("input.message and input.employee_id are required")
    unknown = set(c["expected"]) - EXPECTED_KEYS
    if unknown:
        errs.append(f"unknown expected keys {sorted(unknown)}")
    env = get_environment(env_name)
    tools = set(c["expected"].get("required_tools", [])) | set(c["expected"].get("prohibited_tools", [])) | set(c["expected"].get("args", {}))
    tools |= {t for pair in c["expected"].get("order", []) for t in pair}
    bad = [t for t in tools if t not in env.tools]
    if bad:
        errs.append(f"unknown tools {bad}")
    if c["expected"].get("outcome") and c["expected"]["outcome"] not in ("resolved", "escalated", "declined"):
        errs.append("outcome must be resolved, escalated or declined")
    return errs


def import_cases(db, tenant: str, actor: str, dataset_id: int, cases: list[dict], source: str = "imported", status: str = "approved") -> dict:
    ds = get_owned(db, Dataset, dataset_id, tenant)
    agent = db.get(Agent, ds.agent_id)
    existing = {c.case_key for c in db.query(TestCase).filter_by(dataset_id=ds.id)}
    added, errors = [], []
    for c in cases:
        errs = validate_case(c, agent.environment)
        if c.get("case_key") in existing:
            errs.append("duplicate case_key")
        if errs:
            errors.append({"case_key": c.get("case_key"), "errors": errs})
            continue
        db.add(TestCase(dataset_id=ds.id, case_key=c["case_key"], category=c["category"], severity=c.get("severity", "medium"),
                        tags=c.get("tags", []), input=c["input"], expected=c["expected"], faults=c.get("faults", {}), source=source,
                        status=status, origin=c.get("origin", {}), reviewed_by=actor if status == "approved" else None))
        existing.add(c["case_key"])
        added.append(c["case_key"])
    audit(db, tenant, actor, "dataset.cases_imported", "dataset", ds.id, {"added": len(added), "rejected": len(errors), "source": source})
    db.commit()
    return {"added": added, "errors": errors}


def case_out(c: TestCase) -> dict:
    return {"id": c.id, "dataset_id": c.dataset_id, "case_key": c.case_key, "category": c.category, "severity": c.severity, "tags": c.tags,
            "input": c.input, "expected": c.expected, "faults": c.faults, "source": c.source, "status": c.status, "origin": c.origin,
            "reviewed_by": c.reviewed_by, "created_at": iso(c.created_at)}


def snapshot(c: TestCase) -> dict:
    return {"case_key": c.case_key, "category": c.category, "severity": c.severity, "tags": c.tags, "input": c.input,
            "expected": c.expected, "faults": c.faults, "source": c.source, "origin": c.origin}


def publish_version(db, tenant: str, actor: str, dataset_id: int, note: str = "") -> DatasetVersion:
    ds = get_owned(db, Dataset, dataset_id, tenant)
    cases = db.query(TestCase).filter_by(dataset_id=ds.id, status="approved").order_by(TestCase.case_key).all()
    if not cases:
        raise ServiceError("Dataset has no approved cases to publish")
    ver = db.query(DatasetVersion).filter_by(dataset_id=ds.id).count() + 1
    dv = DatasetVersion(dataset_id=ds.id, version=ver, cases=[snapshot(c) for c in cases], note=note, created_by=actor)
    db.add(dv)
    db.flush()
    audit(db, tenant, actor, "dataset.version_published", "dataset_version", dv.id, {"dataset": ds.name, "version": ver, "cases": len(cases)})
    db.commit()
    return dv


def review_case(db, tenant: str, actor: str, case_id: int, decision: str, edits: dict | None = None) -> TestCase:
    c = db.get(TestCase, case_id)
    if c is None or db.get(Dataset, c.dataset_id).tenant_id != tenant:
        raise ServiceError("Test case not found", 404)
    if decision not in ("approve", "reject"):
        raise ServiceError("decision must be approve or reject")
    if edits:
        for k in ("input", "expected", "category", "severity"):
            if k in edits:
                setattr(c, k, edits[k])
        errs = validate_case(snapshot(c), db.get(Agent, db.get(Dataset, c.dataset_id).agent_id).environment)
        if errs:
            raise ServiceError("; ".join(errs))
    c.status = "approved" if decision == "approve" else "rejected"
    c.reviewed_by = actor
    audit(db, tenant, actor, f"case.{c.status}", "test_case", c.id, {"case_key": c.case_key, "source": c.source})
    db.commit()
    return c


PARAPHRASES = [("forgot", "can't remember"), ("can't", "cannot"), ("laptop", "computer"), ("please", "kindly"), ("need", "would like"),
               ("won't", "does not"), ("How many", "What number of")]


def generate_cases(db, tenant: str, actor: str, dataset_id: int, count: int = 3, mode: str = "auto") -> dict:
    """Generate candidate scenarios. They enter as pending_review and are never treated as ground truth until approved."""
    ds = get_owned(db, Dataset, dataset_id, tenant)
    agent = db.get(Agent, ds.agent_id)
    approved = db.query(TestCase).filter_by(dataset_id=ds.id, status="approved").all()
    if not approved:
        raise ServiceError("Need approved cases to seed generation")
    spec = role_spec(db, tenant, "analyzer")
    generator, created, errors = "template-paraphrase", [], []
    n_existing = db.query(TestCase).filter_by(dataset_id=ds.id).count()
    if mode in ("auto", "llm") and spec.provider != DETERMINISTIC:
        generator = f"llm:{spec.provider}:{spec.model}"
        env = get_environment(agent.environment)
        examples = [snapshot(c) for c in random.Random(n_existing).sample(approved, min(2, len(approved)))]
        system = ("You write evaluation scenarios for an enterprise support agent. Return JSON {\"cases\": [...]} where each case has "
                  "category (normal|edge|failure|adversarial), severity, input {employee_id, message}, expected {outcome, required_tools, "
                  "prohibited_tools, answer_facts}. Use only these tools: " + ", ".join(env.tools) +
                  ". Use only these employee IDs: " + ", ".join(sorted({c.input['employee_id'] for c in approved})) +
                  ". Knowledge articles: " + "; ".join(a["title"] for a in env.knowledge))
        try:
            r = provider_for(db, tenant, spec.provider).chat(spec, system, [{"role": "user", "content": f"Examples: {json.dumps(examples)}\nWrite {count} new, different cases."}],
                                                             json_mode=True, required_keys=["cases"])
            raw = r.parsed["cases"][:count] if isinstance(r.parsed["cases"], list) else []
        except LLMError as e:
            if mode == "llm":
                raise ServiceError(f"Generation model failed: {e.message} ({e.code})", 502)
            errors.append({"generator": generator, "error": e.as_dict()})
            raw, generator = [], "template-paraphrase"
        for i, c in enumerate(raw):
            c = {**c, "case_key": f"GEN-{n_existing + i + 1:03d}", "origin": {"generator": generator}}
            c.setdefault("expected", {})
            created.append(c)
    if not created:
        rng = random.Random(n_existing + 7)
        for i, src in enumerate(rng.sample(approved, min(count, len(approved)))):
            msg = src.input["message"]
            for a, b in PARAPHRASES:
                if a in msg:
                    msg = msg.replace(a, b, 1)
                    break
            msg = rng.choice(["Hi, ", "Quick one: ", "Urgent - ", ""]) + msg[0].lower() + msg[1:]
            created.append({"case_key": f"GEN-{n_existing + i + 1:03d}", "category": src.category, "severity": src.severity,
                            "tags": src.tags + ["generated"], "input": {**src.input, "message": msg}, "expected": src.expected,
                            "faults": src.faults, "origin": {"generator": generator, "paraphrase_of": src.case_key,
                                                             "note": "Expected behaviour copied from source case; reviewer must confirm."}})
    res = import_cases(db, tenant, actor, ds.id, created, source="generated", status="pending_review")
    res.update({"generator": generator, "generation_errors": errors})
    return res


# ----------------------------------------------------------------------------- Runs
def create_run(db, tenant: str, actor: str, agent_id: int, config_id: int | None, dataset_version_id: int, purpose: str = "adhoc",
               experiment_id: int | None = None, background: bool = True) -> EvalRun:
    agent = get_owned(db, Agent, agent_id, tenant)
    cfg = db.get(AgentConfig, config_id or agent.current_config_id)
    dv = db.get(DatasetVersion, dataset_version_id)
    if cfg is None or cfg.agent_id != agent.id:
        raise ServiceError("Config version does not belong to this agent")
    if dv is None or db.get(Dataset, dv.dataset_id).agent_id != agent.id:
        raise ServiceError("Dataset version does not belong to this agent")
    run = EvalRun(tenant_id=tenant, agent_id=agent.id, config_id=cfg.id, dataset_version_id=dv.id, purpose=purpose,
                  experiment_id=experiment_id, created_by=actor, progress={"done": 0, "total": len(dv.cases)})
    db.add(run)
    db.flush()
    audit(db, tenant, actor, "run.created", "eval_run", run.id, {"config_version": cfg.version, "dataset_version": dv.version, "purpose": purpose})
    db.commit()
    engine.start_run(run.id, background=background)
    return run


def wait_for_runs(run_ids: list[int], timeout_s: float = 1800) -> None:
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        db = SessionLocal()
        statuses = [db.get(EvalRun, r).status for r in run_ids]
        db.close()
        if all(s in ("completed", "failed") for s in statuses):
            return
        time.sleep(0.25)
    raise TimeoutError("runs did not finish")


# ----------------------------------------------------------------------------- Analysis & recommendations
def analyze_run(db, tenant: str, actor: str, run_id: int) -> FailureAnalysis:
    run = get_owned(db, EvalRun, run_id, tenant)
    if run.status != "completed":
        raise ServiceError("Run is not completed")
    agent = db.get(Agent, run.agent_id)
    cfg = engine.config_dict(db.get(AgentConfig, run.config_id))
    results = db.query(CaseResult).filter_by(run_id=run.id).all()
    analysis = failure_analyzer.analyze(results, cfg, agent.environment)
    spec = role_spec(db, tenant, "analyzer")
    analyzer_meta = {"evidence_layer": "deterministic evidence rules v1", "narrative_model": None, "error": None}
    if spec.provider != DETERMINISTIC and analysis["clusters"]:
        analyzer_meta["narrative_model"] = spec.public()
        try:
            prov = provider_for(db, tenant, spec.provider)
            analysis["clusters"], err = failure_analyzer.llm_narratives(analysis["clusters"], prov, spec)
        except LLMError as e:
            err = e.as_dict()
        analyzer_meta["error"] = err
    labels = json.loads((DATA_DIR / "calibration" / "failure_labels.json").read_text())
    acc = failure_analyzer.labeled_accuracy(analysis, agent.template_id, db.get(AgentConfig, run.config_id).version, labels)
    analysis["summary"]["labeled_accuracy"] = acc
    fa = FailureAnalysis(run_id=run.id, tenant_id=tenant, analyzer=analyzer_meta, clusters=analysis["clusters"],
                         case_diagnoses=analysis["case_diagnoses"], summary=analysis["summary"])
    db.add(fa)
    db.flush()
    recs = rec_engine.build(analysis["clusters"], cfg, {r.case_key: r.case for r in results}, results)
    for r in recs:
        db.add(Recommendation(tenant_id=tenant, agent_id=agent.id, analysis_id=fa.id, run_id=run.id, cluster_key=r["cluster_key"],
                              title=r["title"], category=r["category"], change_type=r["change_type"], failure_description=r["failure_description"],
                              evidence=r["evidence"], root_cause=r["root_cause"], proposed_change={**r["proposed_change"], "cluster_keys": r["cluster_keys"]},
                              expected_impact=r["expected_impact"], confidence=r["confidence"], priority=r["priority"],
                              priority_score=r["priority_score"], affected_cases=r["affected_cases"]))
    audit(db, tenant, actor, "analysis.completed", "failure_analysis", fa.id, {"run_id": run.id, "clusters": len(analysis["clusters"]),
                                                                                "recommendations": len(recs)})
    db.commit()
    return fa


def review_recommendation(db, tenant: str, actor: str, rec_id: int, decision: str, note: str = "") -> Recommendation:
    r = get_owned(db, Recommendation, rec_id, tenant)
    if decision not in ("approve", "reject"):
        raise ServiceError("decision must be approve or reject")
    r.status = "approved" if decision == "approve" else "rejected"
    r.reviewed_by, r.review_note, r.reviewed_at = actor, note, utcnow()
    audit(db, tenant, actor, f"recommendation.{r.status}", "recommendation", r.id, {"title": r.title, "note": note})
    db.commit()
    return r


# ----------------------------------------------------------------------------- Experiments
def create_experiment(db, tenant: str, actor: str, agent_id: int, name: str, recommendation_ids: list[int] | None = None,
                      patches: list[dict] | None = None, dataset_version_id: int | None = None, kind: str = "improvement",
                      hypothesis: str = "", baseline_config_id: int | None = None) -> Experiment:
    agent = get_owned(db, Agent, agent_id, tenant)
    base = db.get(AgentConfig, baseline_config_id or agent.current_config_id)
    recs = [get_owned(db, Recommendation, rid, tenant) for rid in (recommendation_ids or [])]
    unapproved = [r.id for r in recs if r.status != "approved"]
    if unapproved:
        raise ServiceError(f"Recommendations must be approved before experimentation: {unapproved}")
    all_patches = list(patches or []) or [p for r in recs for p in r.proposed_change.get("patches", [])]
    if not all_patches:
        raise ServiceError("No changes to test")
    base_d = engine.config_dict(base)
    new, summary = rec_engine.apply_patches(base_d, all_patches)
    cand = new_config_version(db, agent, base, new, actor, f"v{db.query(AgentConfig).filter_by(agent_id=agent.id).count() + 1} experimental: {name}",
                              "experimental", summary)
    if dataset_version_id is None:
        ds = db.query(Dataset).filter_by(agent_id=agent.id, kind="golden").first()
        dataset_version_id = db.query(DatasetVersion).filter_by(dataset_id=ds.id).order_by(DatasetVersion.version.desc()).first().id
    source_run = recs[0].run_id if recs else None
    e = Experiment(tenant_id=tenant, agent_id=agent.id, name=name, kind=kind, hypothesis=hypothesis or "; ".join(r.root_cause for r in recs)[:2000],
                   recommendation_ids=[r.id for r in recs], patches=all_patches, baseline_config_id=base.id, candidate_config_id=cand.id,
                   dataset_version_id=dataset_version_id, source_run_id=source_run, created_by=actor, status="pending_approval")
    db.add(e)
    db.flush()
    audit(db, tenant, actor, "experiment.created", "experiment", e.id, {"name": name, "kind": kind, "changes": summary})
    db.commit()
    return e


def approve_experiment(db, tenant: str, actor: str, exp_id: int, note: str = "") -> Experiment:
    e = get_owned(db, Experiment, exp_id, tenant)
    require_role(actor, APPROVER_ROLES, "approve experiment execution")
    if e.status != "pending_approval":
        raise ServiceError(f"Experiment is {e.status}")
    e.status, e.approved_by, e.approved_at = "approved", actor, utcnow()
    audit(db, tenant, actor, "experiment.approved", "experiment", e.id, {"note": note})
    db.commit()
    return e


def run_experiment(db, tenant: str, actor: str, exp_id: int, background: bool = True) -> Experiment:
    e = get_owned(db, Experiment, exp_id, tenant)
    if e.status != "approved":
        raise ServiceError("Experiment must be approved before it runs")
    b = create_run(db, tenant, actor, e.agent_id, e.baseline_config_id, e.dataset_version_id, "experiment_baseline", e.id, background=False) \
        if not background else None
    e.status = "running"
    db.commit()

    def _go():
        s = SessionLocal()
        try:
            ex = s.get(Experiment, exp_id)
            br = b or create_run(s, tenant, actor, ex.agent_id, ex.baseline_config_id, ex.dataset_version_id, "experiment_baseline", ex.id, background=False)
            cr = create_run(s, tenant, actor, ex.agent_id, ex.candidate_config_id, ex.dataset_version_id, "experiment_candidate", ex.id, background=False)
            s.expire_all()
            finalize_experiment(s, ex.id, br.id, cr.id)
        except Exception as err:  # noqa: BLE001
            ex = s.get(Experiment, exp_id)
            ex.status = "failed"
            ex.comparison = {"error": str(err)[:500]}
            s.commit()
        finally:
            s.close()

    if background:
        threading.Thread(target=_go, daemon=True).start()
    else:
        _go()
        db.expire_all()
    return db.get(Experiment, exp_id)


def _delta(a, b):
    return round(b - a, 4) if isinstance(a, (int, float)) and isinstance(b, (int, float)) else None


def finalize_experiment(db, exp_id: int, base_run_id: int, cand_run_id: int) -> None:
    e = db.get(Experiment, exp_id)
    br, cr = db.get(EvalRun, base_run_id), db.get(EvalRun, cand_run_id)
    e.baseline_run_id, e.candidate_run_id = br.id, cr.id
    if br.status != "completed" or cr.status != "completed":
        e.status = "failed"
        e.comparison = {"error": "A run failed", "baseline_error": br.error, "candidate_error": cr.error}
        db.commit()
        return
    agent = db.get(Agent, e.agent_id)
    bres = db.query(CaseResult).filter_by(run_id=br.id).all()
    cres = db.query(CaseResult).filter_by(run_id=cr.id).all()
    reg = detect_regressions(bres, cres)
    keys = ["task_success_rate", "tool_selection_accuracy", "tool_argument_accuracy", "workflow_completion_rate", "policy_compliance_rate",
            "answer_correctness", "grounding", "judge_overall", "quality_score", "critical_failures", "latency_p50_ms", "latency_p95_ms",
            "tokens_total", "cost_usd_total", "evaluation_duration_ms"]
    metrics = {k: {"baseline": br.metrics.get(k), "candidate": cr.metrics.get(k), "delta": _delta(br.metrics.get(k), cr.metrics.get(k))} for k in keys}
    bc = engine.config_dict(db.get(AgentConfig, br.config_id))
    cc = engine.config_dict(db.get(AgentConfig, cr.config_id))
    bdist = failure_analyzer.analyze(bres, bc, agent.environment)["summary"]["primary_distribution"]
    cdist = failure_analyzer.analyze(cres, cc, agent.environment)["summary"]["primary_distribution"]
    cats = sorted(set(bdist) | set(cdist))
    gates = evaluate_gates(cr.metrics, agent.quality_gates or {}, regressions=len(reg["regressed"]))
    cr.gate_verdict = gates
    rec_cases = sorted({k for rid in e.recommendation_ids for k in (db.get(Recommendation, rid).affected_cases or [])})
    e.comparison = {"metrics": metrics, "cases": reg["rows"], "fixed": reg["fixed"], "regressed": reg["regressed"],
                    "still_failing": reg["still_failing"], "still_passing": len(reg["still_passing"]),
                    "failure_categories": [{"category": c, "baseline": bdist.get(c, 0), "candidate": cdist.get(c, 0)} for c in cats],
                    "targeted_cases": rec_cases, "targeted_fixed": [k for k in rec_cases if k in reg["fixed"]],
                    "models": {"baseline": br.models, "candidate": cr.models},
                    "config_diff": db.get(AgentConfig, e.candidate_config_id).change_summary,
                    "fairness": {"same_dataset_version": True, "same_judge": br.models.get("judge") == cr.models.get("judge"),
                                 "same_criteria": True, "notes": _fairness_notes(br, cr)}}
    e.gate_verdict = gates
    e.status = "completed"
    e.time_to_verdict_ms = round((utcnow() - e.created_at).total_seconds() * 1000, 1)
    audit(db, e.tenant_id, "system", "experiment.completed", "experiment", e.id,
          {"verdict": gates["verdict"], "fixed": len(reg["fixed"]), "regressed": len(reg["regressed"])})
    db.commit()


def _fairness_notes(br: EvalRun, cr: EvalRun) -> list[str]:
    notes = []
    a, b = br.models.get("agent", {}), cr.models.get("agent", {})
    if (a.get("provider"), a.get("model")) != (b.get("provider"), b.get("model")):
        notes.append(f"Agent model differs: {a.get('provider')}:{a.get('model')} vs {b.get('provider')}:{b.get('model')}")
    if a.get("provider") == "simlm" or b.get("provider") == "simlm":
        notes.append("SimLM latency and cost are modeled, not measured.")
    if "ollama" in (a.get("provider"), b.get("provider")):
        notes.append("Ollama inference cost is not measured (local hardware).")
    for side, r in (("baseline", br), ("candidate", cr)):
        if r.models.get("same_model_agent_and_judge"):
            notes.append(f"{side}: the same model acts as agent and judge (possible self-preference bias).")
    return notes


def decide_experiment(db, tenant: str, actor: str, exp_id: int, decision: str, note: str = "") -> Experiment:
    e = get_owned(db, Experiment, exp_id, tenant)
    require_role(actor, {"ai_product_manager"}, "accept or reject an experiment")
    if e.status != "completed":
        raise ServiceError("Experiment has not completed")
    if decision not in ("accept", "reject"):
        raise ServiceError("decision must be accept or reject")
    e.decision, e.decision_by, e.decision_note, e.decided_at = decision, actor, note, utcnow()
    cand = db.get(AgentConfig, e.candidate_config_id)
    agent = db.get(Agent, e.agent_id)
    if decision == "accept":
        cand.status = "approved"
        agent.current_config_id = cand.id
    else:
        cand.status = "rejected"
    for rid in e.recommendation_ids:
        r = db.get(Recommendation, rid)
        fixed = set(e.comparison.get("fixed", []))
        r.status = "validated" if decision == "accept" and set(r.affected_cases) & fixed else ("invalidated" if decision == "reject" else r.status)
    audit(db, tenant, actor, f"experiment.{decision}ed", "experiment", e.id,
          {"note": note, "verdict": e.gate_verdict.get("verdict"), "candidate_config": cand.version,
           "deployment": "Not deployed. AgentHill records the approved configuration; production rollout is outside the platform."})
    db.commit()
    return e


# ----------------------------------------------------------------------------- Regression & learning
def regression_dataset(db, tenant: str, agent_id: int, actor: str = "system") -> Dataset:
    ds = db.query(Dataset).filter_by(agent_id=agent_id, kind="regression").first()
    if ds is None:
        ds = create_dataset(db, tenant, actor, agent_id, f"{db.get(Agent, agent_id).name} - Regression Suite",
                            "Confirmed failures converted into permanent regression tests.", kind="regression")
    return ds


def convert_to_regression(db, tenant: str, actor: str, result_id: int, note: str = "") -> dict:
    r = db.get(CaseResult, result_id)
    if r is None:
        raise ServiceError("Result not found", 404)
    run = get_owned(db, EvalRun, r.run_id, tenant)
    if r.passed:
        raise ServiceError("Only confirmed failures can be converted into regression tests")
    ds = regression_dataset(db, tenant, run.agent_id, actor)
    key = f"REG-{r.case_key}"
    if db.query(TestCase).filter_by(dataset_id=ds.id, case_key=key).first():
        raise ServiceError(f"{key} already exists in the regression suite")
    fa = db.query(FailureAnalysis).filter_by(run_id=run.id).order_by(FailureAnalysis.id.desc()).first()
    diag = next((d for d in (fa.case_diagnoses if fa else []) if d["result_id"] == r.id), None)
    cfg = db.get(AgentConfig, run.config_id)
    exp = db.query(Experiment).filter(Experiment.agent_id == run.agent_id, Experiment.status == "completed").order_by(Experiment.id.desc()).first()
    fixed_by = exp.id if exp and r.case_key in (exp.comparison or {}).get("fixed", []) else None
    origin = {"result_id": r.id, "run_id": run.id, "config_version": cfg.version, "original_case": r.case_key,
              "failed_checks": [c["name"] for c in r.checks if c["status"] == "fail"],
              "diagnosis": diag["primary"] if diag else None, "fixed_by_experiment": fixed_by, "confirmed_by": actor, "note": note}
    case = {**{k: r.case[k] for k in ("category", "severity", "tags", "input", "expected") if k in r.case}, "faults": r.case.get("faults", {}),
            "case_key": key, "origin": origin}
    case["tags"] = list(case.get("tags", [])) + ["regression"]
    res = import_cases(db, tenant, actor, ds.id, [case], source="regression", status="approved")
    if res["errors"]:
        raise ServiceError(str(res["errors"]))
    dv = publish_version(db, tenant, actor, ds.id, f"Added {key} from run {run.id}")
    audit(db, tenant, actor, "regression.case_added", "test_case", key, origin)
    db.commit()
    return {"case_key": key, "dataset_id": ds.id, "dataset_version": dv.version, "origin": origin}


# ----------------------------------------------------------------------------- Reviews & metrics
def add_review(db, tenant: str, actor: str, target_type: str, target_id: str, verdict: str, labels: dict | None = None, note: str = "") -> HumanReview:
    if target_type == "case_result":
        r = db.get(CaseResult, int(target_id))
        if r is None:
            raise ServiceError("Result not found", 404)
        get_owned(db, EvalRun, r.run_id, tenant)
        if verdict not in ("agree", "disagree"):
            raise ServiceError("verdict must be agree or disagree")
        r.needs_review = False
    elif target_type == "cluster":
        if verdict not in ("confirm", "reject"):
            raise ServiceError("verdict must be confirm or reject")
    else:
        raise ServiceError("unknown target_type")
    hr = HumanReview(tenant_id=tenant, target_type=target_type, target_id=str(target_id), reviewer=actor, verdict=verdict,
                     labels=labels or {}, note=note)
    db.add(hr)
    audit(db, tenant, actor, f"review.{target_type}.{verdict}", target_type, target_id, {"note": note})
    db.commit()
    return hr


def history(db, tenant: str, q: str = "", agent_id: int | None = None) -> list[dict]:
    qry = db.query(Experiment).filter(Experiment.tenant_id == tenant)
    if agent_id:
        qry = qry.filter(Experiment.agent_id == agent_id)
    if q:
        like = f"%{q}%"
        qry = qry.filter(or_(Experiment.name.ilike(like), Experiment.hypothesis.ilike(like), Experiment.decision_note.ilike(like)))
    out = []
    for e in qry.order_by(Experiment.id.desc()).all():
        agent = db.get(Agent, e.agent_id)
        bc, cc = db.get(AgentConfig, e.baseline_config_id), db.get(AgentConfig, e.candidate_config_id)
        dv = db.get(DatasetVersion, e.dataset_version_id)
        recs = [db.get(Recommendation, rid) for rid in e.recommendation_ids]
        m = (e.comparison or {}).get("metrics", {})
        out.append({**exp_out(e), "agent_name": agent.name, "baseline_version": bc.version, "candidate_version": cc.version,
                    "dataset_version": dv.version if dv else None,
                    "original_failures": sorted({k for r in recs for k in r.affected_cases}),
                    "root_cause_hypotheses": [r.root_cause for r in recs],
                    "recommended_changes": [r.title for r in recs], "applied_changes": cc.change_summary,
                    "baseline_task_success": (m.get("task_success_rate") or {}).get("baseline"),
                    "candidate_task_success": (m.get("task_success_rate") or {}).get("candidate"),
                    "regressions": (e.comparison or {}).get("regressed", []), "fixed": (e.comparison or {}).get("fixed", []),
                    "approvals": {"recommendations": [{"id": r.id, "by": r.reviewed_by, "at": iso(r.reviewed_at), "note": r.review_note} for r in recs],
                                  "execution": {"by": e.approved_by, "at": iso(e.approved_at)},
                                  "decision": {"by": e.decision_by, "at": iso(e.decided_at), "note": e.decision_note}}})
    return out
