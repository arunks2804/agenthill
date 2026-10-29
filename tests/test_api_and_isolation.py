from agenthill import db as D
from agenthill import services as S

from .conftest import hdr


def test_tenant_isolation_over_api(client, db, baseline_run):
    S.register_from_template(db, "globex", "leo", "it_incident_agent")
    acme_agents = client.get("/api/agents", headers=hdr()).json()
    globex_agents = client.get("/api/agents", headers=hdr(tenant="globex")).json()
    assert {a["tenant_id"] for a in acme_agents} == {"acme"} and {a["tenant_id"] for a in globex_agents} == {"globex"}
    assert client.get(f"/api/runs/{baseline_run.id}", headers=hdr()).status_code == 200
    assert client.get(f"/api/runs/{baseline_run.id}", headers=hdr(tenant="globex")).status_code == 404
    res_id = db.query(D.CaseResult).filter_by(run_id=baseline_run.id).first().id
    assert client.get(f"/api/results/{res_id}", headers=hdr(tenant="globex")).status_code == 404
    assert client.get("/api/runs", headers=hdr(tenant="globex")).json() == []
    assert client.get("/api/agents", headers=hdr(tenant="initech")).status_code == 403


def test_api_end_to_end_run_and_dashboard(client, db, it_agent):
    dv = db.query(D.DatasetVersion).first()
    r = client.post("/api/runs", headers=hdr("ana"), json={"agent_id": it_agent.id, "dataset_version_id": dv.id}).json()
    S.wait_for_runs([r["id"]], 60)
    run = client.get(f"/api/runs/{r['id']}", headers=hdr()).json()
    assert run["status"] == "completed" and len(run["results"]) == 19
    assert client.post(f"/api/runs/{r['id']}/analyze", headers=hdr("ana")).status_code == 200
    dash = client.get(f"/api/dashboard?agent_id={it_agent.id}", headers=hdr()).json()
    assert dash["selected_run"]["id"] == r["id"] and dash["failure_distribution"]
    res = client.get(f"/api/results/{run['results'][0]['id']}", headers=hdr()).json()
    assert res["trace"]["spans"]
    otlp = client.get(f"/api/results/{run['results'][0]['id']}/otlp", headers=hdr()).json()
    assert otlp["resourceSpans"][0]["scopeSpans"][0]["spans"]


def test_credentials_never_leak(client, db, it_agent):
    secret = "sk-test-SECRET-abcdef1234567890"
    r = client.put("/api/settings/llm/providers/openai", headers=hdr(), json={"api_key": secret})
    assert r.status_code == 200 and secret not in r.text
    st = client.get("/api/settings/llm", headers=hdr()).json()
    oa = next(p for p in st["providers"] if p["name"] == "openai")
    assert oa["credentials"] == {"configured": True, "source": "encrypted store"}
    dv = db.query(D.DatasetVersion).first()
    run = S.create_run(db, "acme", "ana", it_agent.id, None, dv.id, background=False)
    from agenthill.config import VAR_DIR
    raw = (VAR_DIR / "credentials.enc").read_bytes()
    assert secret.encode() not in raw
    db_bytes = (VAR_DIR / "test.db").read_bytes()
    assert secret.encode() not in db_bytes
    assert secret not in client.get("/api/audit", headers=hdr()).text
    assert secret not in client.get(f"/api/runs/{run.id}", headers=hdr()).text
    client.delete("/api/settings/llm/providers/openai/key", headers=hdr())
    st = client.get("/api/settings/llm", headers=hdr()).json()
    assert next(p for p in st["providers"] if p["name"] == "openai")["credentials"]["configured"] is False


def test_model_role_validation(client):
    assert client.put("/api/settings/llm/roles", headers=hdr(), json={"judge": {"provider": "nope", "model": "x"}}).status_code == 400
    assert client.put("/api/settings/llm/roles", headers=hdr(), json={"judge": {"provider": "ollama", "model": ""}}).status_code == 400
    ok = client.put("/api/settings/llm/roles", headers=hdr(), json={"judge": {"provider": "ollama", "model": "llama3.1:8b"}})
    assert ok.status_code == 200 and ok.json()["judge"]["model"] == "llama3.1:8b"


def test_missing_cloud_key_is_reported_not_substituted(client):
    r = client.post("/api/settings/llm/providers/anthropic/test", headers=hdr(), json={"model": "claude-x"}).json()
    assert r["ok"] is False and r["error"]["code"] == "missing_key"


def test_unavailable_ollama_is_reported(client):
    r = client.post("/api/settings/llm/providers/ollama/test", headers=hdr(), json={"base_url": "http://127.0.0.1:9", "model": "m"}).json()
    assert r["ok"] is False and r["error"]["code"] == "unavailable" and "ollama serve" in r["error"]["hint"]


def test_same_agent_cannot_be_registered_twice_per_customer(client, db, it_agent):
    t = S.load_template("it_incident_agent")
    bc = t["baseline_config"]
    body = {**t, "name": "  it incident resolution agent ", "template_id": "it_incident_agent", "system_prompt": bc["system_prompt"],
            "tools": bc["tools"], "settings": bc["settings"], "load_template_dataset": True}
    r = client.post("/api/agents", headers=hdr("leo"), json=body)
    assert r.status_code == 409 and "already registered" in r.json()["detail"]
    assert len(client.get("/api/agents", headers=hdr()).json()) == 1
    assert db.query(D.Dataset).count() == 1
    assert client.post("/api/agents", headers=hdr("leo", tenant="globex"), json=body).status_code == 200
