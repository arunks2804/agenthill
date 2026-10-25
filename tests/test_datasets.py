import json

import pytest

from agenthill import db as D
from agenthill import services as S
from agenthill.config import DATA_DIR


def test_golden_datasets_are_valid():
    for f, env in (("it_incident_golden.json", "it_service_desk"), ("hr_support_golden.json", "hr_services")):
        data = json.loads((DATA_DIR / "datasets" / f).read_text())
        for case in data["cases"]:
            assert S.validate_case(case, env) == [], case["case_key"]
        cats = {c["category"] for c in data["cases"]}
        assert {"normal", "edge", "adversarial"} <= cats


def test_template_registration_loads_and_publishes(db, it_agent):
    ds = db.query(D.Dataset).filter_by(agent_id=it_agent.id).one()
    v = db.query(D.DatasetVersion).filter_by(dataset_id=ds.id).one()
    assert v.version == 1 and len(v.cases) == 19


def test_invalid_cases_are_rejected(db, it_agent):
    ds = db.query(D.Dataset).filter_by(agent_id=it_agent.id).one()
    res = S.import_cases(db, "acme", "ana", ds.id, [
        {"case_key": "X-1", "category": "weird", "input": {"employee_id": "E1", "message": "hi"}, "expected": {}},
        {"case_key": "X-2", "category": "edge", "input": {"employee_id": "E1001", "message": "hi"}, "expected": {"required_tools": ["format_disk"]}},
        {"case_key": "IT-001", "category": "edge", "input": {"employee_id": "E1001", "message": "dup"}, "expected": {}},
        {"case_key": "X-3", "category": "edge", "input": {"employee_id": "E1001", "message": "ok"}, "expected": {"outcome": "resolved"}},
    ])
    assert res["added"] == ["X-3"]
    errs = {e["case_key"]: " ".join(e["errors"]) for e in res["errors"]}
    assert "invalid category" in errs["X-1"] and "unknown tools" in errs["X-2"] and "duplicate" in errs["IT-001"]


def test_published_version_is_immutable(db, it_agent):
    ds = db.query(D.Dataset).filter_by(agent_id=it_agent.id).one()
    v1 = db.query(D.DatasetVersion).filter_by(dataset_id=ds.id).one()
    tc = db.query(D.TestCase).filter_by(dataset_id=ds.id, case_key="IT-001").one()
    tc.input = {**tc.input, "message": "changed after publishing"}
    db.commit()
    db.refresh(v1)
    assert next(c for c in v1.cases if c["case_key"] == "IT-001")["input"]["message"] != "changed after publishing"
    v2 = S.publish_version(db, "acme", "ana", ds.id, "edit")
    assert v2.version == 2 and next(c for c in v2.cases if c["case_key"] == "IT-001")["input"]["message"] == "changed after publishing"


def test_generated_cases_need_human_review(db, it_agent):
    ds = db.query(D.Dataset).filter_by(agent_id=it_agent.id).one()
    res = S.generate_cases(db, "acme", "ana", ds.id, count=3)
    assert len(res["added"]) == 3 and res["generator"] == "template-paraphrase"
    gen = db.query(D.TestCase).filter_by(dataset_id=ds.id, source="generated").all()
    assert all(c.status == "pending_review" for c in gen)
    v = S.publish_version(db, "acme", "ana", ds.id)
    assert not any(c["case_key"].startswith("GEN-") for c in v.cases)  # not ground truth until approved
    S.review_case(db, "acme", "sam", gen[0].id, "approve")
    v = S.publish_version(db, "acme", "ana", ds.id)
    assert sum(c["case_key"].startswith("GEN-") for c in v.cases) == 1


def test_review_rejects_invalid_edits(db, it_agent):
    ds = db.query(D.Dataset).filter_by(agent_id=it_agent.id).one()
    S.generate_cases(db, "acme", "ana", ds.id, count=1)
    gen = db.query(D.TestCase).filter_by(source="generated").first()
    with pytest.raises(S.ServiceError):
        S.review_case(db, "acme", "sam", gen.id, "approve", {"category": "nonsense"})
