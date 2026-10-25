"""HTTP API. Every request is scoped to a tenant (X-Tenant-ID) and an acting user (X-User).
In this prototype identity is simulated by header; tenant scoping is enforced in every query."""
from __future__ import annotations

import json
from collections import Counter

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

from .. import services as S
from ..config import DATA_DIR
from ..db import (Agent, AgentConfig, AuditEvent, CaseResult, Dataset, DatasetVersion, EvalRun, Experiment, FailureAnalysis,
                  HumanReview, Recommendation, SessionLocal, Tenant, TestCase)
from ..evaluation.engine import build_judge, config_dict
from ..evaluation.judges import RubricJudge
from ..llm.base import LLMError, ModelSpec
from ..llm.registry import DETERMINISTIC, PROVIDERS, SIMLM, build_provider, provider_catalog
from ..platform_settings import CREDENTIALS, get_setting, put_setting
from ..runtime.tracing import to_otlp

router = APIRouter(prefix="/api")


class Ctx:
    def __init__(self, db, tenant: str, user: str):
        self.db, self.tenant, self.user = db, tenant, user


def ctx(x_tenant_id: str = Header("acme"), x_user: str = Header("maya")):
    db = SessionLocal()
    try:
        if db.get(Tenant, x_tenant_id) is None:
            raise HTTPException(403, "Unknown tenant")
        if x_user not in S.USERS:
            raise HTTPException(403, "Unknown user")
        yield Ctx(db, x_tenant_id, x_user)
    finally:
        db.close()


def call(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except S.ServiceError as e:
        raise HTTPException(e.status, e.message)


# ------------------------------------------------------------------ identity
@router.get("/me")
def me(c: Ctx = Depends(ctx)):
    return {"user": c.user, **S.USERS[c.user], "tenant": c.tenant,
            "tenants": [{"id": t.id, "name": t.name} for t in c.db.query(Tenant).all()],
            "users": [{"id": k, **v} for k, v in S.USERS.items()]}


# ------------------------------------------------------------------ registry
@router.get("/templates")
def templates():
    return S.list_templates()


@router.get("/environments")
def environments():
    from ..environments.base import list_environments
    return list_environments()


class AgentIn(BaseModel):
    name: str
    domain: str
    description: str = ""
    environment: str
    intended_tasks: list[str] = []
    expected_outcomes: list[str] = []
    system_prompt: str
    tools: list[dict]
    settings: dict
    evaluation_criteria: dict = {}
    quality_gates: dict = {}
    template_id: str | None = None
    load_template_dataset: bool = True


@router.get("/agents")
def agents(c: Ctx = Depends(ctx)):
    return [S.agent_out(c.db, a) for a in c.db.query(Agent).filter_by(tenant_id=c.tenant).order_by(Agent.id)]


@router.post("/agents")
def create_agent(body: AgentIn, c: Ctx = Depends(ctx)):
    a = call(S.register_agent, c.db, c.tenant, c.user, body.model_dump())
    if body.template_id and body.load_template_dataset:
        t = S.load_template(body.template_id)
        ds_file = json.loads((DATA_DIR / "datasets" / t["dataset_file"]).read_text())
        ds = S.create_dataset(c.db, c.tenant, c.user, a.id, ds_file["name"], ds_file.get("description", ""))
        S.import_cases(c.db, c.tenant, c.user, ds.id, ds_file["cases"], source="curated")
        S.publish_version(c.db, c.tenant, c.user, ds.id, "Initial curated golden set")
    return S.agent_out(c.db, a)


@router.get("/agents/{agent_id}")
def get_agent(agent_id: int, c: Ctx = Depends(ctx)):
    return S.agent_out(c.db, call(S.get_owned, c.db, Agent, agent_id, c.tenant))


class AgentPatch(BaseModel):
    quality_gates: dict | None = None
    evaluation_criteria: dict | None = None
    description: str | None = None


@router.patch("/agents/{agent_id}")
def patch_agent(agent_id: int, body: AgentPatch, c: Ctx = Depends(ctx)):
    a = call(S.get_owned, c.db, Agent, agent_id, c.tenant)
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(a, k, v)
    S.audit(c.db, c.tenant, c.user, "agent.updated", "agent", a.id, body.model_dump(exclude_none=True))
    c.db.commit()
    return S.agent_out(c.db, a)


class ConfigIn(BaseModel):
    base_config_id: int | None = None
    label: str = ""
    system_prompt: str | None = None
    tools: list[dict] | None = None
    settings: dict | None = None


@router.post("/agents/{agent_id}/configs")
def new_config(agent_id: int, body: ConfigIn, c: Ctx = Depends(ctx)):
    """Create a new configuration version (e.g. a different model). Never overwrites an existing version."""
    a = call(S.get_owned, c.db, Agent, agent_id, c.tenant)
    base = c.db.get(AgentConfig, body.base_config_id or a.current_config_id)
    if base is None or base.agent_id != a.id:
        raise HTTPException(400, "Base config not found")
    new = {"system_prompt": body.system_prompt or base.system_prompt, "tools": body.tools or base.tools,
           "settings": {**base.settings, **(body.settings or {})}}
    _validate_agent_model(new["settings"])
    changes = [f"{k}: {base.settings.get(k)!r} -> {v!r}" for k, v in (body.settings or {}).items() if base.settings.get(k) != v]
    if body.system_prompt and body.system_prompt != base.system_prompt:
        changes.append("system prompt edited")
    cfg = S.new_config_version(c.db, a, base, new, c.user, body.label or f"v? from v{base.version}", "experimental", changes or ["no changes"])
    cfg.label = body.label or f"v{cfg.version} (from v{base.version})"
    c.db.commit()
    return S.config_out(cfg)


def _validate_agent_model(settings: dict) -> None:
    p = settings.get("model_provider", SIMLM)
    if p != SIMLM and p not in PROVIDERS:
        raise HTTPException(400, f"Unknown provider {p}")
    if p != SIMLM and not settings.get("model"):
        raise HTTPException(400, "Select a model for the agent")


# ------------------------------------------------------------------ datasets
@router.get("/agents/{agent_id}/datasets")
def datasets(agent_id: int, c: Ctx = Depends(ctx)):
    call(S.get_owned, c.db, Agent, agent_id, c.tenant)
    out = []
    for d in c.db.query(Dataset).filter_by(agent_id=agent_id).order_by(Dataset.id):
        vers = c.db.query(DatasetVersion).filter_by(dataset_id=d.id).order_by(DatasetVersion.version).all()
        cases = c.db.query(TestCase).filter_by(dataset_id=d.id).all()
        out.append({"id": d.id, "name": d.name, "kind": d.kind, "description": d.description,
                    "versions": [{"id": v.id, "version": v.version, "cases": len(v.cases), "note": v.note, "created_by": v.created_by,
                                  "created_at": S.iso(v.created_at)} for v in vers],
                    "counts": dict(Counter(f"{x.status}" for x in cases)), "categories": dict(Counter(x.category for x in cases if x.status == "approved"))})
    return out


class DatasetIn(BaseModel):
    name: str
    description: str = ""


@router.post("/agents/{agent_id}/datasets")
def create_dataset(agent_id: int, body: DatasetIn, c: Ctx = Depends(ctx)):
    d = call(S.create_dataset, c.db, c.tenant, c.user, agent_id, body.name, body.description)
    return {"id": d.id}


@router.get("/datasets/{dataset_id}/cases")
def cases(dataset_id: int, status: str | None = None, c: Ctx = Depends(ctx)):
    call(S.get_owned, c.db, Dataset, dataset_id, c.tenant)
    q = c.db.query(TestCase).filter_by(dataset_id=dataset_id)
    if status:
        q = q.filter_by(status=status)
    return [S.case_out(x) for x in q.order_by(TestCase.case_key)]


class ImportIn(BaseModel):
    cases: list[dict]


@router.post("/datasets/{dataset_id}/import")
def import_cases(dataset_id: int, body: ImportIn, c: Ctx = Depends(ctx)):
    return call(S.import_cases, c.db, c.tenant, c.user, dataset_id, body.cases, "imported")


class GenerateIn(BaseModel):
    count: int = Field(3, ge=1, le=10)
    mode: str = "auto"


@router.post("/datasets/{dataset_id}/generate")
def generate(dataset_id: int, body: GenerateIn, c: Ctx = Depends(ctx)):
    return call(S.generate_cases, c.db, c.tenant, c.user, dataset_id, body.count, body.mode)


class ReviewCaseIn(BaseModel):
    decision: str
    edits: dict | None = None


@router.post("/cases/{case_id}/review")
def review_case(case_id: int, body: ReviewCaseIn, c: Ctx = Depends(ctx)):
    return S.case_out(call(S.review_case, c.db, c.tenant, c.user, case_id, body.decision, body.edits))


class PublishIn(BaseModel):
    note: str = ""


@router.post("/datasets/{dataset_id}/versions")
def publish(dataset_id: int, body: PublishIn, c: Ctx = Depends(ctx)):
    v = call(S.publish_version, c.db, c.tenant, c.user, dataset_id, body.note)
    return {"id": v.id, "version": v.version, "cases": len(v.cases)}


@router.get("/dataset-versions/{version_id}")
def dataset_version(version_id: int, c: Ctx = Depends(ctx)):
    v = c.db.get(DatasetVersion, version_id)
    if v is None:
        raise HTTPException(404, "Not found")
    call(S.get_owned, c.db, Dataset, v.dataset_id, c.tenant)
    return {"id": v.id, "version": v.version, "note": v.note, "cases": v.cases}


# ------------------------------------------------------------------ runs & traces
class RunIn(BaseModel):
    agent_id: int
    config_id: int | None = None
    dataset_version_id: int
    purpose: str = "adhoc"


@router.post("/runs")
def create_run(body: RunIn, c: Ctx = Depends(ctx)):
    r = call(S.create_run, c.db, c.tenant, c.user, body.agent_id, body.config_id, body.dataset_version_id, body.purpose)
    return S.run_out(r, c.db)


@router.get("/runs")
def runs(agent_id: int | None = None, c: Ctx = Depends(ctx)):
    q = c.db.query(EvalRun).filter_by(tenant_id=c.tenant)
    if agent_id:
        q = q.filter_by(agent_id=agent_id)
    return [S.run_out(r, c.db) for r in q.order_by(EvalRun.id.desc()).limit(200)]


@router.get("/runs/{run_id}")
def get_run(run_id: int, c: Ctx = Depends(ctx)):
    r = call(S.get_owned, c.db, EvalRun, run_id, c.tenant)
    out = S.run_out(r, c.db)
    out["results"] = [S.result_out(x) for x in c.db.query(CaseResult).filter_by(run_id=r.id).order_by(CaseResult.case_key)]
    fa = c.db.query(FailureAnalysis).filter_by(run_id=r.id).order_by(FailureAnalysis.id.desc()).first()
    out["analysis_id"] = fa.id if fa else None
    return out


@router.get("/results/{result_id}")
def get_result(result_id: int, c: Ctx = Depends(ctx)):
    r = c.db.get(CaseResult, result_id)
    if r is None:
        raise HTTPException(404, "Not found")
    call(S.get_owned, c.db, EvalRun, r.run_id, c.tenant)
    out = S.result_out(r, full=True)
    fa = c.db.query(FailureAnalysis).filter_by(run_id=r.run_id).order_by(FailureAnalysis.id.desc()).first()
    out["diagnosis"] = next((d for d in (fa.case_diagnoses if fa else []) if d["result_id"] == r.id), None)
    out["reviews"] = [{"reviewer": h.reviewer, "verdict": h.verdict, "note": h.note, "created_at": S.iso(h.created_at)}
                      for h in c.db.query(HumanReview).filter_by(target_type="case_result", target_id=str(r.id))]
    return out


@router.get("/results/{result_id}/otlp")
def get_result_otlp(result_id: int, c: Ctx = Depends(ctx)):
    r = c.db.get(CaseResult, result_id)
    if r is None:
        raise HTTPException(404, "Not found")
    call(S.get_owned, c.db, EvalRun, r.run_id, c.tenant)
    return to_otlp(r.trace)


# ------------------------------------------------------------------ analysis & recommendations
@router.post("/runs/{run_id}/analyze")
def analyze(run_id: int, c: Ctx = Depends(ctx)):
    fa = call(S.analyze_run, c.db, c.tenant, c.user, run_id)
    return analysis_out(c, fa)


def analysis_out(c: Ctx, fa: FailureAnalysis) -> dict:
    reviews = {h.target_id: {"verdict": h.verdict, "reviewer": h.reviewer, "note": h.note}
               for h in c.db.query(HumanReview).filter_by(target_type="cluster", tenant_id=c.tenant)}
    clusters = [{**cl, "review": reviews.get(f"{fa.id}:{cl['key']}")} for cl in fa.clusters]
    return {"id": fa.id, "run_id": fa.run_id, "analyzer": fa.analyzer, "clusters": clusters, "case_diagnoses": fa.case_diagnoses,
            "summary": fa.summary, "created_at": S.iso(fa.created_at),
            "recommendations": [S.rec_out(r) for r in c.db.query(Recommendation).filter_by(analysis_id=fa.id).order_by(Recommendation.priority_score.desc())]}


@router.get("/runs/{run_id}/analysis")
def get_analysis(run_id: int, c: Ctx = Depends(ctx)):
    call(S.get_owned, c.db, EvalRun, run_id, c.tenant)
    fa = c.db.query(FailureAnalysis).filter_by(run_id=run_id).order_by(FailureAnalysis.id.desc()).first()
    if fa is None:
        raise HTTPException(404, "No analysis yet")
    return analysis_out(c, fa)


@router.get("/recommendations")
def recommendations(agent_id: int | None = None, c: Ctx = Depends(ctx)):
    q = c.db.query(Recommendation).filter_by(tenant_id=c.tenant)
    if agent_id:
        q = q.filter_by(agent_id=agent_id)
    latest = {}
    for r in q.order_by(Recommendation.id.desc()):
        latest.setdefault(r.analysis_id, []).append(r)
    return [S.rec_out(r) for r in q.order_by(Recommendation.analysis_id.desc(), Recommendation.priority_score.desc())]


class DecisionIn(BaseModel):
    decision: str
    note: str = ""


@router.post("/recommendations/{rec_id}/review")
def review_rec(rec_id: int, body: DecisionIn, c: Ctx = Depends(ctx)):
    return S.rec_out(call(S.review_recommendation, c.db, c.tenant, c.user, rec_id, body.decision, body.note))


# ------------------------------------------------------------------ experiments
class ExperimentIn(BaseModel):
    agent_id: int
    name: str
    recommendation_ids: list[int] = []
    patches: list[dict] | None = None
    dataset_version_id: int | None = None
    kind: str = "improvement"
    hypothesis: str = ""
    baseline_config_id: int | None = None


@router.post("/experiments")
def create_experiment(body: ExperimentIn, c: Ctx = Depends(ctx)):
    for p in body.patches or []:
        if p.get("type") == "setting" and p.get("target") == "model_provider":
            _validate_agent_model({"model_provider": p["after"], "model": next((q["after"] for q in body.patches if q.get("target") == "model"), "")})
    e = call(S.create_experiment, c.db, c.tenant, c.user, body.agent_id, body.name, body.recommendation_ids, body.patches,
             body.dataset_version_id, body.kind, body.hypothesis, body.baseline_config_id)
    return S.exp_out(e)


@router.get("/experiments")
def experiments(agent_id: int | None = None, c: Ctx = Depends(ctx)):
    q = c.db.query(Experiment).filter_by(tenant_id=c.tenant)
    if agent_id:
        q = q.filter_by(agent_id=agent_id)
    return [S.exp_out(e) for e in q.order_by(Experiment.id.desc())]


@router.get("/experiments/{exp_id}")
def get_experiment(exp_id: int, c: Ctx = Depends(ctx)):
    e = call(S.get_owned, c.db, Experiment, exp_id, c.tenant)
    out = S.exp_out(e)
    out["baseline_config"] = S.config_out(c.db.get(AgentConfig, e.baseline_config_id))
    out["candidate_config"] = S.config_out(c.db.get(AgentConfig, e.candidate_config_id))
    out["runs"] = {k: (S.run_out(c.db.get(EvalRun, rid), c.db) if rid else None)
                   for k, rid in (("baseline", e.baseline_run_id), ("candidate", e.candidate_run_id))}
    out["live_runs"] = [S.run_out(r, c.db) for r in c.db.query(EvalRun).filter_by(experiment_id=e.id)]
    return out


@router.post("/experiments/{exp_id}/approve")
def approve_experiment(exp_id: int, body: DecisionIn | None = None, c: Ctx = Depends(ctx)):
    return S.exp_out(call(S.approve_experiment, c.db, c.tenant, c.user, exp_id, body.note if body else ""))


@router.post("/experiments/{exp_id}/run")
def run_experiment(exp_id: int, c: Ctx = Depends(ctx)):
    return S.exp_out(call(S.run_experiment, c.db, c.tenant, c.user, exp_id))


@router.post("/experiments/{exp_id}/decision")
def decide(exp_id: int, body: DecisionIn, c: Ctx = Depends(ctx)):
    return S.exp_out(call(S.decide_experiment, c.db, c.tenant, c.user, exp_id, body.decision, body.note))


# ------------------------------------------------------------------ regression & learning
class RegressionIn(BaseModel):
    note: str = ""


@router.post("/results/{result_id}/to-regression")
def to_regression(result_id: int, body: RegressionIn, c: Ctx = Depends(ctx)):
    return call(S.convert_to_regression, c.db, c.tenant, c.user, result_id, body.note)


@router.get("/agents/{agent_id}/regression")
def regression(agent_id: int, c: Ctx = Depends(ctx)):
    a = call(S.get_owned, c.db, Agent, agent_id, c.tenant)
    ds = c.db.query(Dataset).filter_by(agent_id=a.id, kind="regression").first()
    cases_ = [S.case_out(x) for x in c.db.query(TestCase).filter_by(dataset_id=ds.id)] if ds else []
    vers = c.db.query(DatasetVersion).filter_by(dataset_id=ds.id).order_by(DatasetVersion.version.desc()).all() if ds else []
    runs_ = [S.run_out(r, c.db) for r in c.db.query(EvalRun).filter(EvalRun.agent_id == a.id, EvalRun.dataset_version_id.in_([v.id for v in vers]))
             .order_by(EvalRun.id.desc())] if vers else []
    exps = c.db.query(Experiment).filter_by(agent_id=a.id, status="completed").order_by(Experiment.id.desc()).all()
    return {"dataset_id": ds.id if ds else None, "latest_version_id": vers[0].id if vers else None, "cases": cases_, "runs": runs_,
            "experiments": [{"id": e.id, "name": e.name, "kind": e.kind, "verdict": (e.gate_verdict or {}).get("verdict"), "decision": e.decision,
                             "fixed": (e.comparison or {}).get("fixed", []), "regressed": (e.comparison or {}).get("regressed", []),
                             "gate_verdict": e.gate_verdict} for e in exps]}


@router.post("/agents/{agent_id}/regression/run")
def run_regression(agent_id: int, c: Ctx = Depends(ctx)):
    a = call(S.get_owned, c.db, Agent, agent_id, c.tenant)
    ds = c.db.query(Dataset).filter_by(agent_id=a.id, kind="regression").first()
    if ds is None:
        raise HTTPException(400, "No regression suite yet: convert a confirmed failure first")
    v = c.db.query(DatasetVersion).filter_by(dataset_id=ds.id).order_by(DatasetVersion.version.desc()).first()
    return S.run_out(call(S.create_run, c.db, c.tenant, c.user, a.id, None, v.id, "regression"), c.db)


@router.get("/history")
def history(q: str = "", agent_id: int | None = None, c: Ctx = Depends(ctx)):
    return S.history(c.db, c.tenant, q, agent_id)


@router.get("/audit")
def audit_log(limit: int = Query(100, le=500), c: Ctx = Depends(ctx)):
    return [{"id": a.id, "actor": a.actor, "action": a.action, "entity_type": a.entity_type, "entity_id": a.entity_id,
             "details": a.details, "created_at": S.iso(a.created_at)}
            for a in c.db.query(AuditEvent).filter_by(tenant_id=c.tenant).order_by(AuditEvent.id.desc()).limit(limit)]


class ReviewIn(BaseModel):
    target_type: str
    target_id: str
    verdict: str
    labels: dict = {}
    note: str = ""


@router.post("/reviews")
def review(body: ReviewIn, c: Ctx = Depends(ctx)):
    h = call(S.add_review, c.db, c.tenant, c.user, body.target_type, body.target_id, body.verdict, body.labels, body.note)
    return {"id": h.id}


# ------------------------------------------------------------------ dashboard & meta metrics
@router.get("/dashboard")
def dashboard(agent_id: int | None = None, config_id: int | None = None, run_id: int | None = None, c: Ctx = Depends(ctx)):
    agents_ = c.db.query(Agent).filter_by(tenant_id=c.tenant).order_by(Agent.id).all()
    q = c.db.query(EvalRun).filter_by(tenant_id=c.tenant, status="completed")
    if agent_id:
        q = q.filter_by(agent_id=agent_id)
    if config_id:
        q = q.filter_by(config_id=config_id)
    runs_ = q.order_by(EvalRun.id).all()
    sel = next((r for r in runs_ if r.id == run_id), runs_[-1] if runs_ else None)
    dist = {}
    if sel:
        fa = c.db.query(FailureAnalysis).filter_by(run_id=sel.id).order_by(FailureAnalysis.id.desc()).first()
        dist = fa.summary.get("primary_distribution", {}) if fa else {}
    exps = c.db.query(Experiment).filter_by(tenant_id=c.tenant)
    if agent_id:
        exps = exps.filter_by(agent_id=agent_id)
    exps = exps.order_by(Experiment.id.desc()).all()
    per_agent = []
    for a in agents_:
        ar = [r for r in c.db.query(EvalRun).filter_by(agent_id=a.id, status="completed").order_by(EvalRun.id)]
        cur = [r for r in ar if r.config_id == a.current_config_id]
        latest = cur[-1] if cur else (ar[-1] if ar else None)
        per_agent.append({"id": a.id, "name": a.name, "domain": a.domain, "runs": len(ar),
                          "current_config_version": c.db.get(AgentConfig, a.current_config_id).version,
                          "latest_run_id": latest.id if latest else None,
                          "task_success_rate": latest.metrics.get("task_success_rate") if latest else None,
                          "quality_score": latest.metrics.get("quality_score") if latest else None,
                          "verdict": (latest.gate_verdict or {}).get("verdict") if latest else None})
    return {"agents": per_agent,
            "runs": [S.run_out(r, c.db) for r in runs_],
            "selected_run": S.run_out(sel, c.db) if sel else None,
            "failure_distribution": dist or (sel.metrics.get("failed_checks", {}) if sel else {}),
            "failure_distribution_source": "failure analysis (primary category)" if dist else "failed deterministic checks",
            "experiments": [{"id": e.id, "name": e.name, "kind": e.kind, "status": e.status, "decision": e.decision,
                             "verdict": (e.gate_verdict or {}).get("verdict"), "fixed": len((e.comparison or {}).get("fixed", [])),
                             "regressed": len((e.comparison or {}).get("regressed", [])), "created_at": S.iso(e.created_at)} for e in exps]}


@router.get("/meta/evaluation-quality")
def evaluation_quality(c: Ctx = Depends(ctx)):
    reviews = c.db.query(HumanReview).filter_by(tenant_id=c.tenant).all()
    case_reviews = [h for h in reviews if h.target_type == "case_result"]
    cluster_reviews = [h for h in reviews if h.target_type == "cluster"]
    analyses = c.db.query(FailureAnalysis).filter_by(tenant_id=c.tenant).order_by(FailureAnalysis.id.desc()).all()
    labeled = [{"analysis_id": fa.id, "run_id": fa.run_id, **fa.summary["labeled_accuracy"]} for fa in analyses if fa.summary.get("labeled_accuracy")]
    for x in labeled:
        x.pop("rows", None)
    cal_path = DATA_DIR / "calibration" / "judge_calibration_results.json"
    cal = [r for r in (json.loads(cal_path.read_text()) if cal_path.exists() else [])]
    live = [r for r in c.db.query(EvalRun).filter_by(tenant_id=c.tenant) if r.status == "completed"]
    return {
        "judge_human_agreement": {"reviews": len(case_reviews), "agreement_rate": round(sum(h.verdict == "agree" for h in case_reviews) / len(case_reviews), 4) if case_reviews else None,
                                  "pending_review": c.db.query(CaseResult).join(EvalRun, EvalRun.id == CaseResult.run_id).filter(EvalRun.tenant_id == c.tenant, CaseResult.needs_review.is_(True)).count()},
        "judge_calibration": _latest_calibration(c),
        "judge_calibration_reports": cal,
        "failure_classification": labeled[:5],
        "diagnosis_validation": {"reviews": len(cluster_reviews),
                                 "confirmed_rate": round(sum(h.verdict == "confirm" for h in cluster_reviews) / len(cluster_reviews), 4) if cluster_reviews else None},
        "evaluation_execution_ms": [{"run_id": r.id, "duration_ms": r.duration_ms, "cases": (r.metrics or {}).get("total_cases")} for r in live[-10:]],
    }


def _latest_calibration(c: Ctx) -> dict | None:
    row = get_setting(c.db, c.tenant, "model_roles").get("_calibration")
    return row


@router.post("/meta/judge-calibration")
def run_calibration(c: Ctx = Depends(ctx)):
    """Score the human-labelled calibration set with the configured judge and report agreement."""
    from ..evaluation.calibration import calibrate
    judge = build_judge(c.db, c.tenant)
    try:
        report = calibrate(judge)
    except LLMError as e:
        raise HTTPException(502, json.dumps(e.as_dict()))
    roles = get_setting(c.db, c.tenant, "model_roles")
    roles["_calibration"] = report
    put_setting(c.db, c.tenant, "model_roles", roles, c.user)
    return report


@router.get("/meta/platform")
def platform_metrics(c: Ctx = Depends(ctx)):
    out = {"onboarding": [], "failure_to_validated_fix": [], "time_to_verdict": [],
           "not_measurable": ["Percentage of agent teams able to run evaluations independently (needs multi-team usage data)"]}
    for a in c.db.query(Agent).filter_by(tenant_id=c.tenant):
        first = c.db.query(EvalRun).filter_by(agent_id=a.id, status="completed").order_by(EvalRun.finished_at).first()
        out["onboarding"].append({"agent": a.name, "registered_at": S.iso(a.created_at),
                                  "first_completed_run_at": S.iso(first.finished_at) if first else None,
                                  "minutes": round((first.finished_at - a.created_at).total_seconds() / 60, 2) if first else None})
    for e in c.db.query(Experiment).filter_by(tenant_id=c.tenant, status="completed"):
        out["time_to_verdict"].append({"experiment": e.name, "ms": e.time_to_verdict_ms})
        if e.decision == "accept" and e.source_run_id:
            src = c.db.get(EvalRun, e.source_run_id)
            out["failure_to_validated_fix"].append({"experiment": e.name, "minutes": round((e.decided_at - src.finished_at).total_seconds() / 60, 2)})
    return out


# ------------------------------------------------------------------ settings: LLM providers
def _provider_public(c: Ctx) -> dict:
    ps = get_setting(c.db, c.tenant, "providers")
    roles = get_setting(c.db, c.tenant, "model_roles")
    return {"providers": [{**p, "credentials": CREDENTIALS.status(p["name"]) if p["requires_api_key"] else {"configured": True, "source": "not required"},
                           "settings": ps.get(p["name"], {})} for p in provider_catalog()],
            "model_roles": {k: v for k, v in roles.items() if not k.startswith("_")},
            "pricing": get_setting(c.db, c.tenant, "pricing"),
            "agent_models": [{"agent_id": a.id, "agent": a.name, "config_version": c.db.get(AgentConfig, a.current_config_id).version,
                              "provider": c.db.get(AgentConfig, a.current_config_id).settings.get("model_provider", SIMLM),
                              "model": c.db.get(AgentConfig, a.current_config_id).settings.get("model")}
                             for a in c.db.query(Agent).filter_by(tenant_id=c.tenant)],
            "deterministic_options": {"agent": SIMLM, "judge": DETERMINISTIC, "analyzer": DETERMINISTIC}}


@router.get("/settings/llm")
def llm_settings(c: Ctx = Depends(ctx)):
    return _provider_public(c)


class ProviderIn(BaseModel):
    base_url: str | None = None
    api_key: str | None = None


@router.put("/settings/llm/providers/{name}")
def save_provider(name: str, body: ProviderIn, c: Ctx = Depends(ctx)):
    if name not in PROVIDERS:
        raise HTTPException(404, "Unknown provider")
    ps = get_setting(c.db, c.tenant, "providers")
    if body.base_url is not None:
        ps[name] = {**ps.get(name, {}), "base_url": body.base_url.strip()}
        put_setting(c.db, c.tenant, "providers", ps, c.user)
    if body.api_key:
        CREDENTIALS.set(name, body.api_key)
        S.audit(c.db, c.tenant, c.user, "credentials.updated", "provider", name, {"stored": "encrypted"})
        c.db.commit()
    return _provider_public(c)


@router.delete("/settings/llm/providers/{name}/key")
def delete_key(name: str, c: Ctx = Depends(ctx)):
    CREDENTIALS.delete(name)
    S.audit(c.db, c.tenant, c.user, "credentials.removed", "provider", name, {})
    c.db.commit()
    return _provider_public(c)


class TestIn(BaseModel):
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None


@router.post("/settings/llm/providers/{name}/test")
def test_provider(name: str, body: TestIn, c: Ctx = Depends(ctx)):
    """Real connection test. An unsaved key/URL may be tested without being stored."""
    if name not in PROVIDERS:
        raise HTTPException(404, "Unknown provider")
    ps = get_setting(c.db, c.tenant, "providers")
    cls = PROVIDERS[name]
    prov = cls(api_key=body.api_key or (CREDENTIALS.get(name) if cls.requires_api_key else None),
               base_url=body.base_url or ps.get(name, {}).get("base_url"))
    return prov.test_connection(body.model or None)


@router.get("/settings/llm/providers/{name}/models")
def provider_models(name: str, c: Ctx = Depends(ctx)):
    try:
        return {"models": build_provider(name, get_setting(c.db, c.tenant, "providers"), CREDENTIALS).list_models()}
    except LLMError as e:
        raise HTTPException(502, json.dumps(e.as_dict()))


class RolesIn(BaseModel):
    judge: dict | None = None
    analyzer: dict | None = None


@router.put("/settings/llm/roles")
def save_roles(body: RolesIn, c: Ctx = Depends(ctx)):
    roles = get_setting(c.db, c.tenant, "model_roles")
    for role, val in body.model_dump(exclude_none=True).items():
        p = val.get("provider")
        if p != DETERMINISTIC and p not in PROVIDERS:
            raise HTTPException(400, f"Unknown provider {p}")
        if p != DETERMINISTIC and not val.get("model"):
            raise HTTPException(400, f"Select a model for the {role}")
        roles[role] = {**roles.get(role, {}), **val}
    return {k: v for k, v in put_setting(c.db, c.tenant, "model_roles", roles, c.user).items() if not k.startswith("_")}


@router.put("/settings/llm/pricing")
def save_pricing(body: dict, c: Ctx = Depends(ctx)):
    for k, v in body.items():
        if ":" not in k or not isinstance(v, dict):
            raise HTTPException(400, "Pricing keys are provider:model with {input_per_mtok, output_per_mtok}")
    return put_setting(c.db, c.tenant, "pricing", body, c.user)


@router.get("/settings/llm/compatibility")
def compatibility(provider: str, model: str, role: str = "agent", c: Ctx = Depends(ctx)):
    """Check that a model can serve a role: it must return valid structured JSON (all roles use JSON)."""
    if provider in (SIMLM, DETERMINISTIC):
        return {"ok": True, "detail": "Built-in deterministic component"}
    try:
        prov = build_provider(provider, get_setting(c.db, c.tenant, "providers"), CREDENTIALS)
        r = prov.chat(ModelSpec(provider, model, max_tokens=80, timeout_s=90), "Compatibility check.",
                      [{"role": "user", "content": 'Return {"action": "final", "response": "ok", "outcome": "resolved"}'}],
                      json_mode=True, required_keys=["action"])
        return {"ok": True, "json_mode": r.json_mode, "latency_ms": r.latency_ms, "context_window": prov.context_window(model),
                "detail": f"{model} returned valid structured output ({r.json_mode} JSON mode)"}
    except LLMError as e:
        return {"ok": False, "error": e.as_dict()}
