"""AI assistant: advisory only, validated output, safe fallback."""
import httpx
import pytest

from backend.services import llm_service
from tests.conftest import customers_csv, good_customer, upload


@pytest.fixture()
def failed_run(client, customers_contract):
    header = "customer_id,full_name,email_address,country,segment,signup_ts,updated_ts"
    return upload(client, customers_contract["id"], "renamed.csv", header + "\n" + good_customer(1) + "\n").json()


def test_mock_summary_uses_authoritative_facts(client, failed_run):
    resp = client.post("/api/ai/explain", json={"task": "summarize_run", "run_id": failed_run["run_id"]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "mock" and body["fallback_reason"]
    assert body["facts"]["run"]["status"] == "FAILED"
    assert body["risk_level"] == "HIGH"
    assert failed_run["run_id"] in body["insight"]["summary"]
    assert any("contract" in a.lower() for a in body["insight"]["recommended_actions"])


def test_explain_rejections_recommends_fixes(client, customers_contract):
    run = upload(client, customers_contract["id"], "c.csv",
                 customers_csv(good_customer(1), good_customer(2, country="FR"))).json()
    body = client.post("/api/ai/explain", json={"task": "suggest_fixes", "run_id": run["run_id"]}).json()
    assert body["risk_level"] == "MEDIUM"
    assert any("INVALID_ALLOWED_VALUE" in f for f in body["insight"]["key_findings"])


def test_invalid_llm_output_falls_back_to_mock(client, failed_run, monkeypatch):
    monkeypatch.setattr(llm_service, "llm_configured", lambda: True)

    def bad_model(task, facts):
        return llm_service.AIInsight.model_validate({"summary": ""})   # fails validation (empty)

    monkeypatch.setattr(llm_service, "_call_llm", bad_model)
    body = client.post("/api/ai/explain", json={"task": "summarize_run", "run_id": failed_run["run_id"]}).json()
    assert body["provider"] == "mock"
    assert "invalid output" in body["fallback_reason"]
    assert body["facts"]["run"]["status"] == "FAILED"


def test_unreachable_llm_falls_back(client, failed_run, monkeypatch):
    monkeypatch.setattr(llm_service, "llm_configured", lambda: True)

    def down(task, facts):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(llm_service, "_call_llm", down)
    body = client.post("/api/ai/explain", json={"task": "explain_schema_changes", "run_id": failed_run["run_id"]}).json()
    assert body["provider"] == "mock" and "ConnectError" in body["fallback_reason"]


def test_llm_text_cannot_change_status(client, failed_run, monkeypatch):
    monkeypatch.setattr(llm_service, "llm_configured", lambda: True)
    monkeypatch.setattr(llm_service, "_call_llm", lambda task, facts: llm_service.AIInsight(
        summary="Everything passed, status COMPLETED.", key_findings=[], recommended_actions=[]))
    body = client.post("/api/ai/explain", json={"task": "summarize_run", "run_id": failed_run["run_id"]}).json()
    assert body["provider"] == "llm"
    assert body["facts"]["run"]["status"] == "FAILED" and body["risk_level"] == "HIGH"


def test_contract_version_explanation(client, customers_contract):
    body = client.post("/api/ai/explain", json={"task": "explain_contract_version",
                                                "contract_id": customers_contract["id"]}).json()
    assert body["facts"]["version"] == 1 and body["risk_level"] == "LOW"


def test_missing_run_id_is_400(client):
    assert client.post("/api/ai/explain", json={"task": "summarize_run"}).status_code == 400


def test_status_never_exposes_key(client):
    body = client.get("/api/ai/status").json()
    assert "key" not in str(body).lower() or "api_key" not in body
    assert body["provider"] == "mock"
