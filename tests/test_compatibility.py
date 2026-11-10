"""Contract-evolution compatibility rules (pure unit tests, no database)."""
import copy

import pytest

from backend.schemas.contract_schema import VersionSpec
from backend.services.compatibility_service import compare_versions, snapshot_from_columns
from tests.conftest import load_contract


def classify(old_body, new_body):
    old = VersionSpec(**old_body)
    new = VersionSpec(**new_body)
    changes = compare_versions(snapshot_from_columns(old.columns, old.compatibility_mode),
                               snapshot_from_columns(new.columns, new.compatibility_mode))
    return {(c.change_type, c.column_name): c.classification for c in changes}


@pytest.fixture()
def orders():
    body = load_contract("orders")
    for k in ("name", "dataset_name", "description", "owner"):
        body.pop(k)
    return body


def col(body, name):
    return next(c for c in body["columns"] if c["name"] == name)


def test_no_changes(orders):
    assert classify(orders, copy.deepcopy(orders)) == {}


def test_add_optional_nullable_column_is_non_breaking(orders):
    new = copy.deepcopy(orders)
    new["columns"].append({"name": "coupon", "data_type": "string", "required": False, "nullable": True})
    assert classify(orders, new) == {("COLUMN_ADDED_OPTIONAL", "coupon"): "NON_BREAKING"}


def test_add_column_with_default_is_non_breaking(orders):
    new = copy.deepcopy(orders)
    new["columns"].append({"name": "priority", "data_type": "integer", "default_value": 0})
    assert classify(orders, new) == {("COLUMN_ADDED_OPTIONAL", "priority"): "NON_BREAKING"}


def test_add_required_column_without_default_is_breaking(orders):
    new = copy.deepcopy(orders)
    new["columns"].append({"name": "warehouse", "data_type": "string"})
    assert classify(orders, new) == {("COLUMN_ADDED_REQUIRED", "warehouse"): "BREAKING"}


def test_remove_required_column_is_breaking(orders):
    new = copy.deepcopy(orders)
    new["columns"] = [c for c in new["columns"] if c["name"] != "currency"]
    assert classify(orders, new) == {("COLUMN_REMOVED", "currency"): "BREAKING"}


def test_remove_optional_column_depends_on_policy(orders):
    new = copy.deepcopy(orders)
    new["columns"] = [c for c in new["columns"] if c["name"] != "channel"]
    assert classify(orders, new) == {("COLUMN_REMOVED", "channel"): "NON_BREAKING"}
    strict = copy.deepcopy(orders)
    strict["compatibility_mode"] = "STRICT"
    assert classify(strict, new) == {("COLUMN_REMOVED", "channel"): "BREAKING"}


def test_incompatible_type_change_is_breaking(orders):
    new = copy.deepcopy(orders)
    col(new, "quantity")["data_type"] = "string"
    assert classify(orders, new) == {("TYPE_CHANGED", "quantity"): "BREAKING"}


def test_type_widening_is_non_breaking(orders):
    new = copy.deepcopy(orders)
    col(new, "quantity")["data_type"] = "decimal"
    assert classify(orders, new) == {("TYPE_WIDENED", "quantity"): "NON_BREAKING"}


def test_nullable_to_non_nullable_is_breaking(orders):
    new = copy.deepcopy(orders)
    col(new, "ship_ts").update(nullable=False)
    assert classify(orders, new) == {("NULLABILITY_TIGHTENED", "ship_ts"): "BREAKING"}


def test_primary_key_change_is_breaking(orders):
    new = copy.deepcopy(orders)
    col(new, "product_id")["is_primary_key"] = True
    assert classify(orders, new)[("PRIMARY_KEY_CHANGED", None)] == "BREAKING"


def test_unmapped_rename_of_required_column_is_breaking(orders):
    new = copy.deepcopy(orders)
    col(new, "unit_price")["name"] = "unit_price_amount"
    new["business_rules"][0]["factors"] = ["quantity", "unit_price_amount"]
    assert classify(orders, new) == {("COLUMN_RENAMED_UNMAPPED", "unit_price_amount"): "BREAKING"}


def test_mapped_rename_is_non_breaking(orders):
    new = copy.deepcopy(orders)
    col(new, "unit_price").update(name="unit_price_amount", renamed_from="unit_price")
    new["business_rules"][0]["factors"] = ["quantity", "unit_price_amount"]
    assert classify(orders, new) == {("COLUMN_RENAMED", "unit_price_amount"): "NON_BREAKING"}


def test_enum_value_added_depends_on_policy(orders):
    new = copy.deepcopy(orders)
    col(new, "status")["allowed_values"].append("RETURNED")
    assert classify(orders, new) == {("ALLOWED_VALUES_ADDED", "status"): "NON_BREAKING"}
    strict = copy.deepcopy(orders)
    strict["compatibility_mode"] = "STRICT"
    assert classify(strict, new) == {("ALLOWED_VALUES_ADDED", "status"): "BREAKING"}


def test_enum_value_removed_is_breaking(orders):
    new = copy.deepcopy(orders)
    col(new, "status")["allowed_values"].remove("CANCELLED")
    assert classify(orders, new) == {("ALLOWED_VALUES_REMOVED", "status"): "BREAKING"}


def test_range_tightened_is_breaking_and_relaxed_is_not(orders):
    tighter = copy.deepcopy(orders)
    col(tighter, "quantity")["max_value"] = 100
    assert classify(orders, tighter) == {("RANGE_TIGHTENED", "quantity"): "BREAKING"}
    looser = copy.deepcopy(orders)
    col(looser, "quantity")["max_value"] = 5000
    assert classify(orders, looser) == {("RANGE_RELAXED", "quantity"): "NON_BREAKING"}


def test_breaking_change_through_api_records_schema_change(client, orders_contract):
    body = load_contract("orders")
    col(body, "quantity")["data_type"] = "string"
    body["acknowledge_breaking"] = True
    resp = client.post(f"/api/contracts/{orders_contract['id']}/versions", json=body)
    assert resp.status_code == 201
    changes = client.get("/api/schema-changes", params={"scope": "CONTRACT", "classification": "BREAKING"}).json()
    assert changes[0]["change_type"] == "TYPE_CHANGED" and changes[0]["contract_version"] == 2
