"""Record-level validation, quarantine contents and domain rules."""
import pandas as pd
import pytest

from backend.services import data_validator, schema_validator, transformation_service
from backend.services.ingestion_service import parse_csv
from tests.conftest import customers_csv, good_customer, good_order, orders_csv, upload


@pytest.fixture()
def orders_env(client, orders_contract, customers_contract):
    resp = upload(client, customers_contract["id"], "c.csv", customers_csv(good_customer(1), good_customer(2)))
    assert resp.json()["status"] == "COMPLETED"
    return orders_contract["id"]


def run_orders(client, contract_id, *rows):
    resp = upload(client, contract_id, "orders.csv", orders_csv(*rows))
    assert resp.status_code == 200, resp.text
    run = resp.json()
    rejected = client.get("/api/rejected", params={"run_id": run["run_id"], "limit": 500}).json()["items"]
    return run, {r["source_row_number"]: r for r in rejected}


def codes(rec):
    return set(rec["error_codes"])


def test_null_in_non_nullable_field(client, orders_env):
    run, rej = run_orders(client, orders_env, good_order(1), good_order(2, product_id=""))
    assert codes(rej[2]) == {"NULL_NOT_ALLOWED"}
    assert rej[2]["errors"][0]["column_name"] == "product_id"
    assert run["published_count"] == 1


def test_invalid_data_type(client, orders_env):
    _, rej = run_orders(client, orders_env, good_order(1), good_order(2), good_order(3, quantity="2.5"))
    assert "INVALID_TYPE" in codes(rej[3])
    assert rej[3]["errors"][0]["observed_value"] == "2.5"


def test_invalid_timestamp(client, orders_env):
    _, rej = run_orders(client, orders_env, good_order(1), good_order(2), good_order(3, order_ts="2024-13-45"))
    assert "INVALID_TIMESTAMP" in codes(rej[3])


def test_invalid_allowed_value(client, orders_env):
    _, rej = run_orders(client, orders_env, good_order(1), good_order(2, status="LOST"))
    assert codes(rej[2]) == {"INVALID_ALLOWED_VALUE"}


def test_allowed_value_case_normalised_when_permitted(client, orders_env):
    run, rej = run_orders(client, orders_env, good_order(1, status="shipped", ship_ts="2024-03-02T00:00:00Z"))
    assert run["status"] == "COMPLETED" and not rej
    assert run["transformation_summary"]["values_case_normalized"] == {"status": 1}


def test_duplicate_primary_key_keeps_first(client, orders_env):
    # different order_ts so this is ONLY a PK duplicate, not also a duplicate business event
    redelivered = good_order(1, quantity=3, total_amount="31.50", order_ts="2024-03-01T09:00:00Z")
    run, rej = run_orders(client, orders_env, good_order(1), good_order(2), redelivered)
    assert run["published_count"] == 2
    assert codes(rej[3]) == {"DUPLICATE_PRIMARY_KEY"}
    assert "first occurrence at row 1" in rej[3]["errors"][0]["message"]


def test_duplicate_business_event(client, orders_env):
    dup = good_order(2, order_ts=good_order(1)["order_ts"], product_id=good_order(1)["product_id"])
    _, rej = run_orders(client, orders_env, good_order(1), dup)
    assert codes(rej[2]) == {"DUPLICATE_BUSINESS_EVENT"}


def test_invalid_foreign_key(client, orders_env):
    _, rej = run_orders(client, orders_env, good_order(1), good_order(2, customer_id="CUST-99999"))
    assert codes(rej[2]) == {"FOREIGN_KEY_VIOLATION"}
    assert "customers.customer_id" in rej[2]["errors"][0]["message"]


def test_negative_quantity_and_multiple_errors_per_record(client, orders_env):
    bad = good_order(2, quantity=-1, currency="XYZ", customer_id="CUST-55555")
    _, rej = run_orders(client, orders_env, good_order(1), bad)
    assert codes(rej[2]) >= {"VALUE_BELOW_MIN", "INVALID_ALLOWED_VALUE", "FOREIGN_KEY_VIOLATION",
                             "BUSINESS_RULE_VIOLATION"}
    assert rej[2]["error_count"] == len(rej[2]["errors"]) >= 4
    assert rej[2]["original_record"]["quantity"] == "-1"            # original preserved, untouched


# ---- domain-specific edge cases ---------------------------------------------------------
def test_domain_total_must_equal_quantity_times_price(client, orders_env):
    within_tolerance = good_order(1, quantity=3, unit_price="3.33", total_amount="9.99")
    wrong_total = good_order(2, quantity=3, unit_price="3.33", total_amount="10.50")
    run, rej = run_orders(client, orders_env, within_tolerance, wrong_total)
    assert 1 not in rej
    err = rej[2]["errors"][0]
    assert err["error_code"] == "BUSINESS_RULE_VIOLATION" and "total_matches_quantity_x_price" in err["message"]


def test_domain_cannot_ship_before_order(client, orders_env):
    ok = good_order(1, status="SHIPPED", ship_ts="2024-03-05T00:00:00Z")
    bad = good_order(2, status="SHIPPED", ship_ts="2024-02-01T00:00:00Z")
    _, rej = run_orders(client, orders_env, ok, bad)
    assert 1 not in rej and "ship_after_order" in rej[2]["errors"][0]["message"]


def test_domain_order_in_future_rejected(client, orders_env):
    _, rej = run_orders(client, orders_env, good_order(1), good_order(2, order_ts="2200-01-01T00:00:00Z",
                                                                        updated_ts="2200-01-01T00:00:00Z"),
                        good_order(3, order_ts="2999-01-01T00:00:00Z"))
    assert codes(rej[2]) == {"FUTURE_TIMESTAMP"}
    # beyond pandas' timestamp range (year 2262) -> reported as an invalid timestamp, never accepted
    assert "INVALID_TIMESTAMP" in codes(rej[3])


def test_timestamp_formats_standardised_to_iso(client, orders_env):
    row = good_order(1, order_ts="2024-03-01 10:00:00", updated_ts="01/03/2024 12:00:00")
    run, rej = run_orders(client, orders_env, row)
    assert run["status"] == "COMPLETED" and not rej
    payload = client.get("/api/valid-records", params={"contract_id": orders_env}).json()["items"][0]["payload"]
    assert payload["order_ts"] == "2024-03-01T10:00:00Z" and payload["updated_ts"] == "2024-03-01T12:00:00Z"


def test_rejection_filters_by_error_type(client, orders_env):
    run, _ = run_orders(client, orders_env, good_order(1, status="LOST"), good_order(2, customer_id="CUST-99999"))
    fk = client.get("/api/rejected", params={"error_code": "FOREIGN_KEY_VIOLATION"}).json()
    assert fk["total"] == 1 and fk["items"][0]["source_row_number"] == 2
    single = client.get(f"/api/rejected/{fk['items'][0]['id']}").json()
    assert single["run_id"] == run["run_id"] and single["source_file"] == "orders.csv"


# ---- pure unit tests (no DB) -------------------------------------------------------------
class _Col:
    def __init__(self, name, data_type, **kw):
        self.name, self.data_type = name, data_type
        defaults = dict(required=True, nullable=False, is_primary_key=False, allowed_values=None, default_value=None,
                        min_value=None, max_value=None, max_length=None, pattern=None, normalize=None,
                        renamed_from=None)
        defaults.update(kw)
        self.__dict__.update(defaults)


class _Version:
    def __init__(self, columns, rules=(), unique=()):
        self.columns, self.business_rules, self.unique_constraints = list(columns), list(rules), list(unique)
        self.foreign_keys = []

    @property
    def primary_key(self):
        return [c.name for c in self.columns if c.is_primary_key]


def _validate(csv_text, version):
    batch = parse_csv(csv_text.encode())
    _, rename = schema_validator.compare_columns(batch, version.columns, "BACKWARD")
    tr = transformation_service.transform(batch, version.columns, rename)
    return data_validator.validate_records(batch, tr, version, now=pd.Timestamp("2025-01-01", tz="UTC"))


def test_compare_rule_does_not_flag_passing_rows():
    version = _Version([_Col("id", "integer", is_primary_key=True), _Col("a", "timestamp"), _Col("b", "timestamp")],
                       rules=[{"type": "compare_columns", "name": "b_after_a", "left": "b", "op": ">=", "right": "a"}])
    out = _validate("id,a,b\n1,2024-01-01,2024-01-02\n2,2024-01-05,2024-01-01\n3,2024-01-01,\n", version)
    assert list(out.valid_mask) == [True, False, False]          # row 3: b is NULL -> NULL_NOT_ALLOWED only
    assert set(out.errors[out.errors.row == 2]["error_code"]) == {"NULL_NOT_ALLOWED"}


def test_integer_and_boolean_casting():
    version = _Version([_Col("id", "integer", is_primary_key=True), _Col("flag", "boolean")])
    out = _validate("id,flag\n1,yes\n2.0,FALSE\n3.5,maybe\n", version)
    assert list(out.valid_mask) == [True, True, False]
    assert set(out.errors["error_code"]) == {"INVALID_TYPE"} and len(out.errors) == 2
