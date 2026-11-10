"""Batch schema validation: missing/unexpected/renamed columns, type & nullability drift."""
from tests.conftest import (ORDER_HEADER, customers_csv, good_customer,
                            good_order, load_contract, orders_csv, upload)


def _changes(client, run):
    return {c["change_type"]: c for c in client.get(f"/api/runs/{run['run_id']}/report").json()["schema_changes"]}


def _publish_customer(client, customers_contract):
    assert upload(client, customers_contract["id"], "c.csv", customers_csv(good_customer(1))).json()["status"] == "COMPLETED"


def test_missing_required_column_is_breaking_and_blocks_publish(client, orders_contract, customers_contract):
    _publish_customer(client, customers_contract)
    header = ORDER_HEADER.replace("currency,", "")
    run = upload(client, orders_contract["id"], "o.csv", orders_csv(good_order(1), header=header)).json()
    assert run["status"] == "FAILED"
    assert run["published_count"] == 0 and run["breaking_change_count"] == 1
    assert "MISSING_REQUIRED_COLUMN(currency)" in run["failure_reason"]
    change = _changes(client, run)["MISSING_REQUIRED_COLUMN"]
    assert change["classification"] == "BREAKING" and change["severity"] == "CRITICAL"
    assert client.get("/api/valid-records", params={"contract_id": orders_contract["id"]}).json()["total"] == 0


def test_missing_primary_key_column(client, customers_contract):
    header = "full_name,email,country,segment,signup_ts,updated_ts"
    content = header + "\nAnn,a@x.com,IN,RETAIL,2024-01-01T00:00:00Z,2024-01-01T00:00:00Z\n"
    run = upload(client, customers_contract["id"], "c.csv", content).json()
    assert run["status"] == "FAILED"
    assert "PRIMARY_KEY_COLUMN_MISSING" in _changes(client, run)


def test_unexpected_column_is_not_confused_with_missing(client, orders_contract, customers_contract):
    _publish_customer(client, customers_contract)
    header = ORDER_HEADER + ",gift_wrap"
    run = upload(client, orders_contract["id"], "o.csv",
                 orders_csv(good_order(1, gift_wrap="yes"), header=header)).json()
    assert run["status"] == "COMPLETED", run
    changes = _changes(client, run)
    assert set(changes) == {"UNEXPECTED_COLUMN"}
    assert changes["UNEXPECTED_COLUMN"]["classification"] == "NON_BREAKING"
    rec = client.get("/api/valid-records", params={"contract_id": orders_contract["id"]}).json()["items"][0]
    assert "gift_wrap" not in rec["payload"]          # not published


def test_unexpected_column_breaking_under_strict_policy(client):
    body = load_contract("customers")
    body["compatibility_mode"] = "STRICT"
    cid = client.post("/api/contracts", json=body).json()["id"]
    header = "customer_id,full_name,email,country,segment,signup_ts,updated_ts,extra"
    content = header + "\n" + good_customer(1) + ",x\n"
    run = upload(client, cid, "c.csv", content).json()
    assert run["status"] == "FAILED"
    assert _changes(client, run)["UNEXPECTED_COLUMN"]["classification"] == "BREAKING"


def test_missing_optional_column_is_non_breaking_and_default_applied(client, customers_contract):
    header = "customer_id,full_name,email,country,signup_ts,updated_ts"
    content = header + "\nCUST-00001,Ann,a@x.com,IN,2024-01-01T00:00:00Z,2024-01-02T00:00:00Z\n"
    run = upload(client, customers_contract["id"], "c.csv", content).json()
    assert run["status"] == "COMPLETED", run
    assert _changes(client, run)["MISSING_OPTIONAL_COLUMN"]["classification"] == "NON_BREAKING"
    rec = client.get("/api/valid-records", params={"contract_id": customers_contract["id"]}).json()["items"][0]
    assert rec["payload"]["segment"] == "RETAIL"


def test_rename_candidate_detected(client, customers_contract):
    header = "customer_id,full_name,email_address,country,segment,signup_ts,updated_ts"
    content = header + "\n" + good_customer(1) + "\n"
    run = upload(client, customers_contract["id"], "c.csv", content).json()
    changes = _changes(client, run)
    assert run["status"] == "FAILED"
    assert "RENAME_CANDIDATE" in changes and "UNEXPECTED_COLUMN" not in changes
    assert "email_address" in changes["RENAME_CANDIDATE"]["explanation"]


def test_declared_rename_mapping_is_accepted(client, customers_contract):
    spec = load_contract("customers")
    for col in spec["columns"]:
        if col["name"] == "email":
            col["name"], col["renamed_from"] = "email_address", "email"
    spec["unique_constraints"] = [["email_address"]]
    resp = client.post(f"/api/contracts/{customers_contract['id']}/versions", json=spec)
    assert resp.status_code == 201, resp.text
    assert resp.json()["compatibility"]["changes"][0]["change_type"] == "COLUMN_RENAMED"
    # an old producer still sending 'email' is mapped to v2's 'email_address'
    run = upload(client, customers_contract["id"], "old.csv", customers_csv(good_customer(1))).json()
    assert run["status"] == "COMPLETED", run
    assert "COLUMN_RENAME_MAPPED" in _changes(client, run)


def test_column_level_type_change_is_breaking(client, orders_contract, customers_contract):
    _publish_customer(client, customers_contract)
    rows = [good_order(i, quantity="two") for i in range(1, 6)]
    run = upload(client, orders_contract["id"], "o.csv", orders_csv(*rows)).json()
    assert run["status"] == "FAILED"
    change = _changes(client, run)["TYPE_MISMATCH"]
    assert change["column_name"] == "quantity" and change["new_schema"]["inferred_type"] == "string"


def test_isolated_bad_value_is_record_level_not_schema(client, orders_contract, customers_contract):
    _publish_customer(client, customers_contract)
    rows = [good_order(i) for i in range(1, 6)] + [good_order(6, quantity="two")]
    run = upload(client, orders_contract["id"], "o.csv", orders_csv(*rows)).json()
    assert run["status"] == "COMPLETED_WITH_ERRORS"
    assert "TYPE_MISMATCH" not in _changes(client, run)
    assert run["rejected_count"] == 1


def test_all_null_non_nullable_column_is_breaking(client, customers_contract):
    rows = [good_customer(i, full_name="") for i in (1, 2, 3)]
    run = upload(client, customers_contract["id"], "c.csv", customers_csv(*rows)).json()
    assert run["status"] == "FAILED"
    assert _changes(client, run)["NULLABILITY_VIOLATION"]["column_name"] == "full_name"
