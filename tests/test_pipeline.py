"""End-to-end pipeline behaviour through the HTTP API."""
import json

from tests.conftest import customers_csv, good_customer, good_order, orders_csv, upload


def publish_customers(client, contract_id, n=3):
    resp = upload(client, contract_id, "customers.csv", customers_csv(*[good_customer(i) for i in range(1, n + 1)]))
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "COMPLETED", resp.json()
    return resp.json()


def test_happy_path_customers(client, customers_contract):
    run = publish_customers(client, customers_contract["id"], n=5)
    assert run["input_count"] == 5
    assert run["valid_count"] == 5 and run["rejected_count"] == 0
    assert run["published_count"] == 5 and run["inserted_count"] == 5
    assert run["schema_change_count"] == 0
    assert run["duration_ms"] is not None
    assert run["source_checksum"] and len(run["source_checksum"]) == 64

    records = client.get("/api/valid-records", params={"contract_id": customers_contract["id"]}).json()
    assert records["total"] == 5
    first = records["items"][0]
    assert first["primary_key_value"] == "CUST-00001"
    assert first["payload"]["signup_ts"] == "2024-01-01T10:00:00Z"   # ISO-8601 UTC
    assert first["first_run_id"] == run["run_id"]


def test_happy_path_orders_with_fk(client, orders_contract, customers_contract):
    publish_customers(client, customers_contract["id"])
    resp = upload(client, orders_contract["id"], "orders.csv", orders_csv(*[good_order(i) for i in range(1, 11)]))
    run = resp.json()
    assert run["status"] == "COMPLETED", run
    assert run["published_count"] == 10


def test_json_upload(client, customers_contract):
    rows = [dict(zip(["customer_id", "full_name", "email", "country", "segment", "signup_ts", "updated_ts"],
                     good_customer(i).split(","))) for i in (1, 2)]
    rows[1]["segment"] = None                    # default 'RETAIL' applied
    resp = upload(client, customers_contract["id"], "customers.json", json.dumps(rows))
    run = resp.json()
    assert run["status"] == "COMPLETED", run
    assert run["transformation_summary"]["defaults_applied"] == {"segment": 1}


def test_empty_input_fails_run(client, customers_contract):
    resp = upload(client, customers_contract["id"], "empty.csv", customers_csv())
    run = resp.json()
    assert run["status"] == "FAILED"
    assert "empty batch" in run["failure_reason"]
    assert run["published_count"] == 0


def test_zero_byte_file_rejected(client, customers_contract):
    resp = upload(client, customers_contract["id"], "zero.csv", b"")
    assert resp.status_code == 400


def test_unsupported_extension_rejected(client, customers_contract):
    resp = upload(client, customers_contract["id"], "data.xlsx", b"abc")
    assert resp.status_code == 400
    assert "Unsupported file type" in resp.json()["detail"]


def test_malformed_csv_rows_are_quarantined(client, customers_contract):
    content = customers_csv(good_customer(1), "CUST-00002,only,three", good_customer(3))
    run = upload(client, customers_contract["id"], "malformed.csv", content).json()
    assert run["status"] == "COMPLETED_WITH_ERRORS"
    assert run["valid_count"] == 2 and run["rejected_count"] == 1
    rej = client.get("/api/rejected", params={"run_id": run["run_id"]}).json()["items"][0]
    assert rej["source_row_number"] == 2
    assert rej["error_codes"] == ["MALFORMED_ROW"]
    assert rej["original_record"] == {"_raw_fields": ["CUST-00002", "only", "three"]}


def test_malformed_csv_quoting_fails_run(client, customers_contract):
    content = customers_csv(good_customer(1)) + 'CUST-00002,"unterminated,a@b.com,IN,RETAIL,x,y\n'
    run = upload(client, customers_contract["id"], "badquote.csv", content).json()
    assert run["status"] == "FAILED"
    assert "Malformed CSV" in run["failure_reason"]


def test_malformed_json_fails_run(client, customers_contract):
    run = upload(client, customers_contract["id"], "bad.json", '[{"customer_id": "CUST-00001",').json()
    assert run["status"] == "FAILED"
    assert "Malformed JSON" in run["failure_reason"]


def test_run_listing_and_report(client, customers_contract):
    content = customers_csv(good_customer(1), good_customer(2, country="FR"))
    run = upload(client, customers_contract["id"], "c.csv", content).json()
    runs = client.get("/api/runs").json()
    assert runs["total"] == 1 and runs["items"][0]["run_id"] == run["run_id"]
    report = client.get(f"/api/runs/{run['run_id']}/report").json()
    assert report["error_summary"][0]["error_code"] == "INVALID_ALLOWED_VALUE"
    assert report["schema_changes"][0]["change_type"] == "ALLOWED_VALUES_DRIFT"
    assert report["sample_rejections"][0]["source_file"] == "c.csv"


def test_metrics_come_from_database(client, customers_contract):
    publish_customers(client, customers_contract["id"], n=4)
    upload(client, customers_contract["id"], "bad.csv",
           customers_csv(good_customer(10), good_customer(11, email="not-an-email")))
    m = client.get("/api/metrics/summary").json()
    assert m["total_runs"] == 2
    assert m["total_records_processed"] == 6
    assert m["valid_records"] == 5 and m["rejected_records"] == 1
    assert m["validation_success_pct"] == round(500 / 6, 2)
    assert m["top_error_codes"][0]["error_code"] == "PATTERN_MISMATCH"
    assert m["published_rows_current"] == 5


def test_async_upload_returns_pending_then_completes(client, customers_contract):
    resp = upload(client, customers_contract["id"], "async.csv", customers_csv(good_customer(1)), sync="false")
    assert resp.status_code == 202
    # TestClient executes background tasks before returning, so the run is finished now.
    run = client.get(f"/api/runs/{resp.json()['run_id']}").json()
    assert run["status"] == "COMPLETED"


def test_export_rejected_csv(client, customers_contract):
    run = upload(client, customers_contract["id"], "x.csv",
                 customers_csv(good_customer(1, country="XX"), good_customer(2))).json()
    resp = client.get("/api/rejected/export", params={"run_id": run["run_id"]})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    lines = resp.text.strip().splitlines()
    assert lines[0].startswith("run_id,source_file,source_row_number")
    assert len(lines) == 2 and "INVALID_ALLOWED_VALUE" in lines[1]
