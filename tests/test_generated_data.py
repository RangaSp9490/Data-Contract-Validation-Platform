"""The generator's manifest must match what the pipeline actually rejects - row for row."""
import sys

from tests.conftest import ROOT, upload

sys.path.insert(0, str(ROOT / "scripts"))
import generate_data  # noqa: E402


def _csv(df):
    return df.to_csv(index=False)


def _rejected_rows(client, run_id):
    items = client.get("/api/rejected", params={"run_id": run_id, "limit": 500}).json()["items"]
    return {r["source_row_number"]: r for r in items}


def test_generator_is_deterministic():
    a = generate_data.generate(200, 40, seed=7)
    b = generate_data.generate(200, 40, seed=7)
    assert a[1].equals(b[1]) and a[2] == b[2]
    assert not a[1].equals(generate_data.generate(200, 40, seed=8)[1])


def test_pipeline_rejects_exactly_the_injected_rows(client, orders_contract, customers_contract):
    customers, orders, manifest = generate_data.generate(n_orders=500, n_customers=80, seed=42, anomaly_rate=0.1)
    exp = manifest["expected"]

    c_run = upload(client, customers_contract["id"], "customers.csv", _csv(customers)).json()
    assert (c_run["valid_count"], c_run["rejected_count"]) == (exp["customers_valid"], exp["customers_rejected"])

    o_run = upload(client, orders_contract["id"], "orders.csv", _csv(orders)).json()
    assert o_run["status"] == "COMPLETED_WITH_ERRORS", o_run
    assert (o_run["valid_count"], o_run["rejected_count"]) == (exp["orders_valid"], exp["orders_rejected"])
    assert o_run["published_count"] == exp["orders_valid"]

    rejected = _rejected_rows(client, o_run["run_id"])
    expected_codes = {
        "null_required": "NULL_NOT_ALLOWED", "duplicate_primary_key": "DUPLICATE_PRIMARY_KEY",
        "invalid_type": "INVALID_TYPE", "invalid_allowed_value": "INVALID_ALLOWED_VALUE",
        "negative_quantity": "VALUE_BELOW_MIN", "invalid_foreign_key": "FOREIGN_KEY_VIOLATION",
        "invalid_timestamp": "INVALID_TIMESTAMP", "total_mismatch": "BUSINESS_RULE_VIOLATION",
        "ship_before_order": "BUSINESS_RULE_VIOLATION", "duplicate_business_event": "DUPLICATE_BUSINESS_EVENT",
    }
    all_injected = set()
    for kind, rows in manifest["order_anomalies"].items():
        for row in rows:
            all_injected.add(row)
            assert expected_codes[kind] in rejected[row]["error_codes"], (kind, row, rejected[row]["error_codes"])
    assert set(rejected) == all_injected          # nothing else rejected


def test_scenario_files(client, orders_contract, customers_contract):
    customers, orders, manifest = generate_data.generate(n_orders=200, n_customers=50, seed=1)
    upload(client, customers_contract["id"], "customers.csv", _csv(customers))
    upload(client, orders_contract["id"], "orders.csv", _csv(orders))
    files = generate_data.scenario_files(orders, manifest, 1)

    def run(name):
        return upload(client, orders_contract["id"], name, _csv(files[name])).json()

    assert run("orders_unexpected_column.csv")["status"] == "COMPLETED"
    assert run("orders_missing_column.csv")["status"] == "FAILED"
    renamed = run("orders_renamed_column.csv")
    assert renamed["status"] == "FAILED" and "RENAME_CANDIDATE" in renamed["failure_reason"]
    late = run("orders_late.csv")
    assert late["status"] == "COMPLETED", late
    assert late["stale_skipped_count"] == 10 and late["inserted_count"] == 10
    assert late["late_arriving_count"] == 20
