import os
import sys
import tempfile
from pathlib import Path

import pytest

_TMP = tempfile.mkdtemp(prefix="agenthill-test-")
os.environ["AGENTHILL_VAR_DIR"] = _TMP
os.environ["AGENTHILL_DATABASE_URL"] = f"sqlite:///{_TMP}/test.db"
for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
    os.environ.pop(var, None)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from agenthill import db as D  # noqa: E402
from agenthill import services as S  # noqa: E402

D.init_engine(os.environ["AGENTHILL_DATABASE_URL"])


@pytest.fixture()
def db():
    D.reset_db()
    s = D.SessionLocal()
    S.ensure_tenants(s)
    yield s
    s.close()


@pytest.fixture()
def it_agent(db):
    return S.register_from_template(db, "acme", "leo", "it_incident_agent")


@pytest.fixture()
def baseline_run(db, it_agent):
    dv = db.query(D.DatasetVersion).first()
    run = S.create_run(db, "acme", "ana", it_agent.id, None, dv.id, "baseline", background=False)
    db.expire_all()
    return db.get(D.EvalRun, run.id)


@pytest.fixture()
def client(db):
    from fastapi.testclient import TestClient
    from agenthill.main import app
    return TestClient(app)


def hdr(user="maya", tenant="acme"):
    return {"X-User": user, "X-Tenant-ID": tenant}
