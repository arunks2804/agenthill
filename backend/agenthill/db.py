"""Persistence: SQLAlchemy ORM models over SQLite (PostgreSQL-compatible schema)."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .config import DATABASE_URL, VAR_DIR


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))


class Agent(Base):
    __tablename__ = "agents"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    domain: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    environment: Mapped[str] = mapped_column(String(60))
    template_id: Mapped[str | None] = mapped_column(String(60), nullable=True)
    intended_tasks: Mapped[list] = mapped_column(JSON, default=list)
    expected_outcomes: Mapped[list] = mapped_column(JSON, default=list)
    evaluation_criteria: Mapped[dict] = mapped_column(JSON, default=dict)
    quality_gates: Mapped[dict] = mapped_column(JSON, default=dict)
    current_config_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AgentConfig(Base):
    __tablename__ = "agent_configs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(String(200), default="")
    system_prompt: Mapped[str] = mapped_column(Text)
    tools: Mapped[list] = mapped_column(JSON)
    settings: Mapped[dict] = mapped_column(JSON)
    parent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="baseline")  # baseline | experimental | approved | rejected
    change_summary: Mapped[list] = mapped_column(JSON, default=list)
    created_by: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Dataset(Base):
    __tablename__ = "datasets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    kind: Mapped[str] = mapped_column(String(20), default="golden")  # golden | regression
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class TestCase(Base):
    __tablename__ = "test_cases"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id"), index=True)
    case_key: Mapped[str] = mapped_column(String(60))
    category: Mapped[str] = mapped_column(String(20))  # normal | edge | failure | adversarial | regression
    severity: Mapped[str] = mapped_column(String(20), default="medium")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    input: Mapped[dict] = mapped_column(JSON)
    expected: Mapped[dict] = mapped_column(JSON)
    faults: Mapped[dict] = mapped_column(JSON, default=dict)
    source: Mapped[str] = mapped_column(String(30), default="curated")  # curated | imported | generated | regression
    status: Mapped[str] = mapped_column(String(30), default="approved")  # approved | pending_review | rejected
    origin: Mapped[dict] = mapped_column(JSON, default=dict)
    reviewed_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class DatasetVersion(Base):
    __tablename__ = "dataset_versions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    cases: Mapped[list] = mapped_column(JSON)  # immutable snapshot of approved cases
    note: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class EvalRun(Base):
    __tablename__ = "eval_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id"), index=True)
    config_id: Mapped[int] = mapped_column(ForeignKey("agent_configs.id"))
    dataset_version_id: Mapped[int] = mapped_column(ForeignKey("dataset_versions.id"))
    purpose: Mapped[str] = mapped_column(String(40), default="adhoc")
    experiment_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="queued")
    progress: Mapped[dict] = mapped_column(JSON, default=dict)
    models: Mapped[dict] = mapped_column(JSON, default=dict)  # agent/judge models + params; never credentials
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    gate_verdict: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)


class CaseResult(Base):
    __tablename__ = "case_results"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("eval_runs.id"), index=True)
    case_key: Mapped[str] = mapped_column(String(60))
    case: Mapped[dict] = mapped_column(JSON)
    passed: Mapped[bool] = mapped_column(Boolean)
    status: Mapped[str] = mapped_column(String(20), default="passed")  # passed | failed | error
    outcome: Mapped[str] = mapped_column(String(30))
    response: Mapped[str] = mapped_column(Text)
    checks: Mapped[list] = mapped_column(JSON)
    judge: Mapped[dict] = mapped_column(JSON)
    score: Mapped[float] = mapped_column(Float)
    critical: Mapped[bool] = mapped_column(Boolean, default=False)
    tool_calls: Mapped[list] = mapped_column(JSON)
    retrieved: Mapped[list] = mapped_column(JSON)
    state_changes: Mapped[list] = mapped_column(JSON)
    latency_ms: Mapped[float] = mapped_column(Float)
    tokens: Mapped[int] = mapped_column(Integer)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    trace: Mapped[dict] = mapped_column(JSON)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)


class FailureAnalysis(Base):
    __tablename__ = "failure_analyses"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("eval_runs.id"), index=True)
    tenant_id: Mapped[str] = mapped_column(String(40), index=True)
    analyzer: Mapped[dict] = mapped_column(JSON, default=dict)
    clusters: Mapped[list] = mapped_column(JSON)
    case_diagnoses: Mapped[list] = mapped_column(JSON)
    summary: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Recommendation(Base):
    __tablename__ = "recommendations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(40), index=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id"), index=True)
    analysis_id: Mapped[int] = mapped_column(ForeignKey("failure_analyses.id"))
    run_id: Mapped[int] = mapped_column(Integer)
    cluster_key: Mapped[str] = mapped_column(String(120))
    title: Mapped[str] = mapped_column(String(240))
    category: Mapped[str] = mapped_column(String(60))
    change_type: Mapped[str] = mapped_column(String(40))
    failure_description: Mapped[str] = mapped_column(Text)
    evidence: Mapped[list] = mapped_column(JSON)
    root_cause: Mapped[str] = mapped_column(Text)
    proposed_change: Mapped[dict] = mapped_column(JSON)
    expected_impact: Mapped[str] = mapped_column(Text)
    confidence: Mapped[str] = mapped_column(String(20))
    priority: Mapped[str] = mapped_column(String(10))
    priority_score: Mapped[float] = mapped_column(Float)
    affected_cases: Mapped[list] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30), default="proposed")  # proposed | approved | rejected | validated | invalidated
    reviewed_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Experiment(Base):
    __tablename__ = "experiments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(40), index=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(30), default="improvement")  # improvement | model_comparison
    hypothesis: Mapped[str] = mapped_column(Text, default="")
    recommendation_ids: Mapped[list] = mapped_column(JSON, default=list)
    patches: Mapped[list] = mapped_column(JSON, default=list)
    baseline_config_id: Mapped[int] = mapped_column(Integer)
    candidate_config_id: Mapped[int] = mapped_column(Integer)
    dataset_version_id: Mapped[int] = mapped_column(Integer)
    source_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="pending_approval")
    approved_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    baseline_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    candidate_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comparison: Mapped[dict] = mapped_column(JSON, default=dict)
    gate_verdict: Mapped[dict] = mapped_column(JSON, default=dict)
    decision: Mapped[str | None] = mapped_column(String(20), nullable=True)
    decision_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    time_to_verdict_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_by: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class HumanReview(Base):
    __tablename__ = "human_reviews"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(40), index=True)
    target_type: Mapped[str] = mapped_column(String(30))  # case_result | cluster
    target_id: Mapped[str] = mapped_column(String(80))
    reviewer: Mapped[str] = mapped_column(String(80))
    verdict: Mapped[str] = mapped_column(String(30))  # agree | disagree | confirm | reject
    labels: Mapped[dict] = mapped_column(JSON, default=dict)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(40), index=True)
    actor: Mapped[str] = mapped_column(String(80))
    action: Mapped[str] = mapped_column(String(80))
    entity_type: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str] = mapped_column(String(40))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PlatformSetting(Base):
    __tablename__ = "platform_settings"
    tenant_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
    updated_by: Mapped[str] = mapped_column(String(80), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


_engine = None
SessionLocal = sessionmaker(expire_on_commit=False)


def init_engine(url: str | None = None):
    global _engine
    url = url or DATABASE_URL
    if url.startswith("sqlite:///"):
        VAR_DIR.mkdir(parents=True, exist_ok=True)
    _engine = create_engine(url, connect_args={"check_same_thread": False} if url.startswith("sqlite") else {})
    if url.startswith("sqlite"):
        @event.listens_for(_engine, "connect")
        def _pragma(conn, _):  # noqa: ANN001
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
    SessionLocal.configure(bind=_engine)
    Base.metadata.create_all(_engine)
    return _engine


def reset_db() -> None:
    Base.metadata.drop_all(_engine)
    Base.metadata.create_all(_engine)
