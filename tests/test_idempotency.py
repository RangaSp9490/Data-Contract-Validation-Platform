"""Idempotency, partial failure + retry, reprocessing, late-arriving and out-of-order data."""
import pytest
from sqlalchemy import func, select

from backend.models import RejectedRecord, ValidRecord
from backend.services import publishing_service
from tests.conftest import customers_csv, good_customer, good_order, orders_csv, upload


def count_valid(db, contract_id):
    db.rollback()  # end the read snapshot so we see committed data
    return db.scalar(select(func.count()).select_from(ValidRecord).where(ValidRecord.contract_id == contract_id))


def test_reprocessing_same_batch_is_skipped(client, db, customers_contract):
    cid = customers_contract["id"]
    content = customers_csv(*[good_customer(i) for i in range(1, 6)], good_customer(6, country="ZZ"))
    first = upload(client, cid, "batch.csv", content).json()
    second = upload(client, cid, "batch_copy.csv", content).json()     # same bytes, different name
    assert first["status"] == "COMPLETED_WITH_ERRORS"
    assert second["status"] == "SKIPPED_DUPLICATE"
    assert second["duplicate_of_run_id"] == first["run_id"]
    assert count_valid(db, cid) == 5
    assert db.scalar(select(func.count()).select_from(RejectedRecord)) == 1     # quarantine not duplicated


def test_forced_reprocess_does_not_duplicate(client, db, customers_contract):
    cid = customers_contract["id"]
    content = customers_csv(*[good_customer(i) for i in range(1, 6)])
    upload(client, cid, "batch.csv", content)
    forced = upload(client, cid, "batch.csv", content, force="true").json()
    assert forced["status"] == "COMPLETED"
    assert forced["unchanged_count"] == 5 and forced["published_count"] == 0
    assert count_valid(db, cid) == 5


def test_overlapping_batches_update_in_place(client, db, customers_contract):
    cid = customers_contract["id"]
    upload(client, cid, "a.csv", customers_csv(good_customer(1), good_customer(2)))
    run = upload(client, cid, "b.csv", customers_csv(
        good_customer(2, full_name="Renamed Customer", updated_ts="2024-02-01T00:00:00Z"), good_customer(3))).json()
    assert (run["inserted_count"], run["updated_count"]) == (1, 1)
    assert count_valid(db, cid) == 3


def test_partial_failure_rolls_back_and_retry_succeeds(client, db, customers_contract, monkeypatch):
    cid = customers_contract["id"]
    content = customers_csv(*[good_customer(i) for i in range(1, 11)])
    original = publishing_service.write_chunk

    def write_half_then_crash(session, rows):
        original(session, rows[: len(rows) // 2])      # some rows really are written...
        raise RuntimeError("simulated connection loss during publish")

    monkeypatch.setattr(publishing_service, "write_chunk", write_half_then_crash)
    failed = upload(client, cid, "batch.csv", content).json()
    assert failed["status"] == "FAILED"
    assert "rolled back" in failed["failure_reason"]
    assert failed["published_count"] == 0
    assert count_valid(db, cid) == 0                    # ...but the transaction rolled them back

    monkeypatch.setattr(publishing_service, "write_chunk", original)
    # a FAILED run does not count as "already processed": retry works without force
    retried = client.post(f"/api/runs/{failed['run_id']}/retry", params={"sync": "true"}).json()
    assert retried["status"] == "COMPLETED" and retried["attempt"] == 2
    assert retried["published_count"] == 10
    assert count_valid(db, cid) == 10

    # retrying again after success is a duplicate unless forced
    again = client.post(f"/api/runs/{retried['run_id']}/retry", params={"sync": "true"}).json()
    assert again["status"] == "SKIPPED_DUPLICATE"


@pytest.fixture()
def orders_ready(client, orders_contract, customers_contract):
    upload(client, customers_contract["id"], "c.csv", customers_csv(good_customer(1)))
    return orders_contract["id"]


def test_late_arriving_older_event_does_not_overwrite_newer(client, db, orders_ready):
    oid = orders_ready
    newer = good_order(1, status="DELIVERED", ship_ts="2024-03-03T00:00:00Z", updated_ts="2024-03-05T00:00:00Z")
    upload(client, oid, "day2.csv", orders_csv(newer))
    older = good_order(1, status="PAID", updated_ts="2024-03-02T00:00:00Z")
    late_new_order = good_order(2, updated_ts="2024-03-01T12:00:00Z")      # new key, but older than watermark
    run = upload(client, oid, "day1_late.csv", orders_csv(older, late_new_order)).json()
    assert run["status"] == "COMPLETED"
    assert run["stale_skipped_count"] == 1          # older version of ORD-000001 ignored
    assert run["inserted_count"] == 1               # late but new record is still published
    assert run["late_arriving_count"] == 2
    db.rollback()  # end the read snapshot so we see committed data
    rec = db.scalar(select(ValidRecord).where(ValidRecord.primary_key_value == "ORD-000001"))
    assert rec.payload["status"] == "DELIVERED"


def test_out_of_order_events_across_batches_newest_wins(client, db, orders_ready):
    oid = orders_ready
    events = [("PLACED", "2024-03-01T11:00:00Z"), ("SHIPPED", "2024-03-03T00:00:00Z"), ("PAID", "2024-03-02T00:00:00Z")]
    for i, (status, ts) in enumerate(events):
        row = good_order(1, status=status, updated_ts=ts, ship_ts="2024-03-03T00:00:00Z" if status == "SHIPPED" else "")
        upload(client, oid, f"evt{i}.csv", orders_csv(row))
    db.rollback()  # end the read snapshot so we see committed data
    rec = db.scalar(select(ValidRecord).where(ValidRecord.primary_key_value == "ORD-000001"))
    assert rec.payload["status"] == "SHIPPED"       # PAID arrived last but is older
    assert count_valid(db, oid) == 1
