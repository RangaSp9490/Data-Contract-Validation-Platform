import logging

from tests.conftest import load_contract


def test_create_and_get_contract(client, customers_contract):
    assert customers_contract["dataset_name"] == "customers"
    assert customers_contract["latest_version"] == 1
    current = customers_contract["current"]
    assert current["primary_key"] == ["customer_id"]
    assert [c["name"] for c in current["columns"]][:2] == ["customer_id", "full_name"]

    got = client.get(f"/api/contracts/{customers_contract['id']}").json()
    assert got["current"]["columns"][3]["allowed_values"] == ["IN", "US", "GB", "DE", "SG"]
    assert len(client.get("/api/contracts").json()) == 1


def test_duplicate_dataset_rejected(client, customers_contract):
    resp = client.post("/api/contracts", json=load_contract("customers"))
    assert resp.status_code == 409


def test_contract_requires_primary_key(client):
    body = load_contract("customers")
    for col in body["columns"]:
        col["is_primary_key"] = False
    resp = client.post("/api/contracts", json=body)
    assert resp.status_code == 422


def test_rule_referencing_unknown_column_rejected(client):
    body = load_contract("customers")
    body["business_rules"][0]["left"] = "does_not_exist"
    assert client.post("/api/contracts", json=body).status_code == 422


def test_foreign_key_to_unknown_dataset_rejected(client):
    resp = client.post("/api/contracts", json=load_contract("orders"))  # customers not created yet
    assert resp.status_code == 400
    assert "customers" in resp.json()["detail"]


def test_foreign_key_resolved(orders_contract):
    fks = orders_contract["current"]["foreign_keys"]
    assert fks == [{"column": "customer_id", "ref_dataset": "customers", "ref_column": "customer_id"}]


def test_non_breaking_version_is_created_and_v1_preserved(client, customers_contract):
    cid = customers_contract["id"]
    spec = load_contract("customers")
    spec["columns"].append({"name": "loyalty_tier", "data_type": "string", "required": False, "nullable": True})
    resp = client.post(f"/api/contracts/{cid}/versions", json=spec)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["version"]["version"] == 2
    assert body["compatibility"]["compatible"] is True
    assert [c["change_type"] for c in body["compatibility"]["changes"]] == ["COLUMN_ADDED_OPTIONAL"]

    v1 = client.get(f"/api/contracts/{cid}/versions/1").json()
    assert "loyalty_tier" not in [c["name"] for c in v1["columns"]]   # never silently overwritten
    assert client.get(f"/api/contracts/{cid}").json()["versions"] == [1, 2]


def test_breaking_version_requires_acknowledgement(client, customers_contract):
    cid = customers_contract["id"]
    spec = load_contract("customers")
    spec["columns"] = [c for c in spec["columns"] if c["name"] != "full_name"]   # remove required column
    resp = client.post(f"/api/contracts/{cid}/versions", json=spec)
    assert resp.status_code == 409
    details = resp.json()["details"]
    assert details["breaking_count"] == 1
    assert details["changes"][0]["change_type"] == "COLUMN_REMOVED"
    assert client.get(f"/api/contracts/{cid}").json()["versions"] == [1]   # nothing created

    spec["acknowledge_breaking"] = True
    resp = client.post(f"/api/contracts/{cid}/versions", json=spec)
    assert resp.status_code == 201
    assert resp.json()["version"]["schema_changes"][0]["classification"] == "BREAKING"


def test_compatibility_check_is_dry_run(client, customers_contract):
    cid = customers_contract["id"]
    spec = load_contract("customers")
    spec["columns"][3]["allowed_values"].append("FR")
    report = client.post(f"/api/contracts/{cid}/compatibility-check", json=spec).json()
    assert report["compatible"] is True
    assert report["changes"][0]["change_type"] == "ALLOWED_VALUES_ADDED"
    assert client.get(f"/api/contracts/{cid}").json()["versions"] == [1]


def test_unknown_contract_404(client):
    assert client.get("/api/contracts/999").status_code == 404


# ---- safe delete -------------------------------------------------------------------------
def test_unused_contract_can_be_deleted_with_all_versions(client, customers_contract):
    cid = customers_contract["id"]
    spec = load_contract("customers")
    spec["columns"].append({"name": "tier", "data_type": "string", "required": False, "nullable": True})
    assert client.post(f"/api/contracts/{cid}/versions", json=spec).status_code == 201   # v2 exists too
    resp = client.delete(f"/api/contracts/{cid}")
    assert resp.status_code == 200 and resp.json()["deleted"] is True
    assert client.get(f"/api/contracts/{cid}").status_code == 404
    assert client.get("/api/contracts").json() == []
    # the dataset name is free again
    assert client.post("/api/contracts", json=load_contract("customers")).status_code == 201


def test_used_contract_is_deleted_permanently_with_all_data(client, db, customers_contract):
    from pathlib import Path

    from sqlalchemy import func, select

    from backend.models import (ContractColumn, ContractVersion, PipelineRun, RejectedRecord,
                                RejectionError, SchemaChange, SourceFile, ValidRecord)
    from tests.conftest import customers_csv, good_customer, upload

    cid = customers_contract["id"]
    run = upload(client, cid, "c.csv", customers_csv(good_customer(1), good_customer(2, country="XX"))).json()
    assert (run["published_count"], run["rejected_count"]) == (1, 1)
    stored = Path(db.scalar(select(SourceFile.stored_path)))
    assert stored.exists()

    preview = client.get(f"/api/contracts/{cid}/delete-preview").json()
    assert preview["can_delete"] and preview["pipeline_runs"] == 1
    assert (preview["published_records"], preview["rejected_records"]) == (1, 1)

    resp = client.delete(f"/api/contracts/{cid}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["deleted"] is True and resp.json()["source_files_removed"] == 1
    assert client.get(f"/api/contracts/{cid}").status_code == 404
    db.rollback()
    for model in (ContractVersion, ContractColumn, PipelineRun, ValidRecord, RejectedRecord,
                  RejectionError, SchemaChange, SourceFile):
        assert db.scalar(select(func.count()).select_from(model)) == 0, model.__tablename__
    assert not stored.exists()                                   # original upload removed too
    assert client.get("/api/metrics/summary").json()["total_runs"] == 0


def test_shared_source_file_is_kept_for_other_contract(client, db):
    """The same file uploaded to two contracts: deleting one keeps the file for the other."""
    from sqlalchemy import func, select

    from backend.models import SourceFile
    from tests.conftest import customers_csv, good_customer, upload

    a = client.post("/api/contracts", json=load_contract("customers")).json()
    other = load_contract("customers")
    other["dataset_name"] = "customers_copy"
    b = client.post("/api/contracts", json=other).json()
    content = customers_csv(good_customer(1))
    upload(client, a["id"], "same.csv", content)
    upload(client, b["id"], "same.csv", content)
    assert client.delete(f"/api/contracts/{a['id']}").json()["source_files_removed"] == 0
    db.rollback()
    assert db.scalar(select(func.count()).select_from(SourceFile)) == 1
    assert client.get("/api/runs").json()["total"] == 1             # b's run untouched


def test_referenced_contract_cannot_be_deleted(client, orders_contract, customers_contract):
    resp = client.delete(f"/api/contracts/{customers_contract['id']}")
    assert resp.status_code == 409
    assert resp.json()["details"]["referenced_by"] == ["orders"]
    # the child (orders) is unused, so it can be deleted - after which customers can be too
    assert client.delete(f"/api/contracts/{orders_contract['id']}").status_code == 200
    assert client.delete(f"/api/contracts/{customers_contract['id']}").status_code == 200


def test_delete_logs_without_crashing(client, customers_contract, caplog):
    # Regression: the delete log passed a reserved LogRecord key ("name") and returned 500,
    # but only when INFO logging is enabled (as on the real server), so force INFO here.
    caplog.set_level(logging.INFO, logger="backend.services.contract_service")
    resp = client.delete(f"/api/contracts/{customers_contract['id']}")
    assert resp.status_code == 200 and resp.json()["deleted"] is True
    rec = next(r for r in caplog.records if r.getMessage() == "contract deleted permanently")
    assert rec.contract_name == customers_contract["name"] and rec.dataset_name == "customers"


def test_delete_unknown_contract_404(client):
    assert client.delete("/api/contracts/999").status_code == 404
