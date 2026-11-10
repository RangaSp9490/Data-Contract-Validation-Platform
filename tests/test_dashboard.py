"""Dashboard filters, daily summary, published-data export, interrupted-run recovery."""
from datetime import date, timedelta

from backend.models import PipelineRun, RunStatus
from backend.services import pipeline_service
from tests.conftest import (customers_csv, good_customer, good_order, load_contract,
                            orders_csv, upload)


def _setup(client, customers_contract, orders_contract):
    upload(client, customers_contract["id"], "c.csv", customers_csv(good_customer(1), good_customer(2, country="XX")))
    upload(client, orders_contract["id"], "o.csv", orders_csv(*[good_order(i) for i in range(1, 5)]))


def test_filter_by_contract(client, customers_contract, orders_contract):
    _setup(client, customers_contract, orders_contract)
    all_ = client.get("/api/metrics/summary").json()
    cust = client.get("/api/metrics/summary", params={"contract_id": customers_contract["id"]}).json()
    orders = client.get("/api/metrics/summary", params={"contract_id": orders_contract["id"]}).json()
    assert all_["total_runs"] == 2 and cust["total_runs"] == 1 and orders["total_runs"] == 1
    assert (cust["valid_records"], cust["rejected_records"]) == (1, 1)
    assert (orders["valid_records"], orders["rejected_records"]) == (4, 0)
    assert cust["published_rows_current"] == 1 and orders["published_rows_current"] == 4
    assert [e["error_code"] for e in cust["top_error_codes"]] == ["INVALID_ALLOWED_VALUE"]
    assert orders["top_error_codes"] == []
    assert all(r["dataset_name"] == "orders" for r in orders["recent_runs"])


def test_filter_by_date(client, customers_contract, orders_contract):
    _setup(client, customers_contract, orders_contract)
    today = date.today()
    window = {"date_from": str(today - timedelta(days=1)), "date_to": str(today + timedelta(days=1))}
    assert client.get("/api/metrics/summary", params=window).json()["total_runs"] == 2
    future = {"date_from": str(today + timedelta(days=10)), "date_to": str(today + timedelta(days=20))}
    m = client.get("/api/metrics/summary", params=future).json()
    assert m["total_runs"] == 0 and m["total_records_processed"] == 0 and m["schema_changes"] == 0
    assert m["validation_success_pct"] is None and m["daily"] == []
    assert "success_trend" not in m


def test_invalid_date_range_rejected(client):
    resp = client.get("/api/metrics/summary", params={"date_from": "2024-02-01", "date_to": "2024-01-01"})
    assert resp.status_code == 400


def test_daily_summary(client, customers_contract, orders_contract):
    _setup(client, customers_contract, orders_contract)
    m = client.get("/api/metrics/summary").json()
    assert len(m["daily"]) == 1
    day = m["daily"][0]
    assert (day["runs"], day["processed"], day["valid"], day["rejected"]) == (2, 6, 5, 1)
    assert day["success_pct"] == round(500 / 6, 2)


def test_published_records_csv_export(client, customers_contract):
    upload(client, customers_contract["id"], "c.csv", customers_csv(good_customer(1), good_customer(2)))
    resp = client.get("/api/valid-records/export", params={"contract_id": customers_contract["id"]})
    assert resp.status_code == 200 and resp.headers["content-type"].startswith("text/csv")
    assert 'filename="published_customers.csv"' in resp.headers["content-disposition"]
    lines = resp.text.strip().splitlines()
    assert lines[0].startswith("customer_id,full_name,email,country,segment,signup_ts,updated_ts")
    assert len(lines) == 3 and lines[1].startswith("CUST-00001,Customer 1,c1@example.com,IN,RETAIL,2024-01-01T10:00:00Z")
    assert client.get("/api/valid-records/export", params={"contract_id": 999}).status_code == 404


def test_interrupted_runs_are_recovered_on_startup(client, db, customers_contract):
    run = upload(client, customers_contract["id"], "c.csv", customers_csv(good_customer(1))).json()
    # simulate a server crash in the middle of two runs
    stuck = db.query(PipelineRun).filter_by(run_id=run["run_id"]).one()
    stuck.status = RunStatus.RUNNING
    clone = PipelineRun(run_id="RUN-TEST-PENDING", contract_id=stuck.contract_id,
                        contract_version_id=stuck.contract_version_id, source_file_id=stuck.source_file_id,
                        source_file_name="c.csv", idempotency_key="x" * 64, status=RunStatus.PENDING)
    db.add(clone)
    db.commit()

    assert pipeline_service.recover_interrupted_runs() == 2
    for rid, was in ((run["run_id"], "RUNNING"), ("RUN-TEST-PENDING", "PENDING")):
        r = client.get(f"/api/runs/{rid}").json()
        assert r["status"] == "FAILED"
        assert f"while this run was {was}" in r["failure_reason"]
    assert pipeline_service.recover_interrupted_runs() == 0          # idempotent
    retried = client.post("/api/runs/RUN-TEST-PENDING/retry", params={"sync": "true"}).json()
    assert retried["status"] in ("COMPLETED", "SKIPPED_DUPLICATE")
