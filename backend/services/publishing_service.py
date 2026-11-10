"""Idempotent publishing of valid records into `valid_records`.

Identity: record_key = sha256(contract_id | primary key value). UNIQUE(contract_id, record_key)
means a record can exist only once per contract, whatever happens.

For each valid record we compare with what is already published:
  new key                          -> INSERT
  same payload hash                -> unchanged (identical re-delivery, nothing written)
  existing event_ts newer          -> stale_skipped (late / out-of-order event never overwrites newer data)
  otherwise                        -> UPDATE
The write itself is an INSERT ... ON DUPLICATE KEY UPDATE whose update branch is guarded by
event_ts too, so even two concurrent runs cannot make older data win.

Everything runs in the caller's transaction: the pipeline commits quarantine, published
rows and the final run status together, or rolls all of it back.
"""
import hashlib
import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

import pandas as pd
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from backend.models import ValidRecord
from backend.utils.id_generator import record_key, utcnow

log = logging.getLogger(__name__)

LOOKUP_CHUNK = 1000
WRITE_CHUNK = 2000

_NEWER_OR_EQUAL = "(VALUES(event_ts) IS NULL OR event_ts IS NULL OR VALUES(event_ts) >= event_ts)"
UPSERT_SQL = text(f"""
INSERT INTO valid_records
  (contract_id, contract_version_id, record_key, primary_key_value, payload, record_hash, event_ts,
   source_file_id, source_row_number, first_run_id, last_run_id, created_at, updated_at)
VALUES
  (:contract_id, :contract_version_id, :record_key, :primary_key_value, :payload, :record_hash, :event_ts,
   :source_file_id, :source_row_number, :run_id, :run_id, :now, :now)
ON DUPLICATE KEY UPDATE
  contract_version_id = IF({_NEWER_OR_EQUAL}, VALUES(contract_version_id), contract_version_id),
  payload             = IF({_NEWER_OR_EQUAL}, VALUES(payload), payload),
  record_hash         = IF({_NEWER_OR_EQUAL}, VALUES(record_hash), record_hash),
  source_file_id      = IF({_NEWER_OR_EQUAL}, VALUES(source_file_id), source_file_id),
  source_row_number   = IF({_NEWER_OR_EQUAL}, VALUES(source_row_number), source_row_number),
  last_run_id         = IF({_NEWER_OR_EQUAL}, VALUES(last_run_id), last_run_id),
  updated_at          = IF({_NEWER_OR_EQUAL}, VALUES(updated_at), updated_at),
  event_ts            = IF({_NEWER_OR_EQUAL}, VALUES(event_ts), event_ts)
""")  # event_ts must be assigned LAST: MySQL evaluates assignments left to right.


def lookup_published_keys(db: Session, contract_id: int, values: Set[str]) -> Set[str]:
    """Which of `values` exist as published primary keys of `contract_id` (used for FK checks)."""
    found: Set[str] = set()
    vals = list(values)
    for start in range(0, len(vals), LOOKUP_CHUNK):
        chunk = vals[start:start + LOOKUP_CHUNK]
        found.update(db.scalars(select(ValidRecord.primary_key_value).where(
            ValidRecord.contract_id == contract_id, ValidRecord.primary_key_value.in_(chunk))).all())
    return found


def current_watermark(db: Session, contract_id: int) -> Optional[datetime]:
    return db.scalar(select(func.max(ValidRecord.event_ts)).where(ValidRecord.contract_id == contract_id))


def _to_naive_utc(ts: Any) -> Optional[datetime]:
    if ts is None or pd.isna(ts):
        return None
    return ts.tz_convert("UTC").tz_localize(None).to_pydatetime()


def build_records(serial: pd.DataFrame, primary_key: List[str], contract_id: int,
                  event_ts: Optional[pd.Series]) -> List[Dict[str, Any]]:
    """Valid rows -> publishable dicts with deterministic keys and payload hashes."""
    payloads = serial.to_dict("records")
    pk_values = serial[primary_key].astype(str).agg("|".join, axis=1) if len(primary_key) > 1 \
        else serial[primary_key[0]].astype(str)
    # Optimisation: serialise each payload ONCE; the hash is computed from the exact JSON string
    # that is stored (previously json.dumps ran twice per record: once to hash, once to store).
    events = [_to_naive_utc(v) for v in event_ts.tolist()] if event_ts is not None else [None] * len(payloads)
    encoder = json.JSONEncoder(sort_keys=True, separators=(",", ":"), default=str)
    out = []
    for row, payload, pk, ev in zip(serial.index, payloads, pk_values, events):
        payload_json = encoder.encode(payload)
        out.append({
            "row": int(row), "primary_key_value": pk, "record_key": record_key(contract_id, pk),
            "payload_json": payload_json, "record_hash": hashlib.sha256(payload_json.encode()).hexdigest(),
            "event_ts": ev,
        })
    return out


def publish_records(db: Session, *, contract_id: int, contract_version_id: int, run_pk: int,
                    source_file_id: int, records: List[Dict[str, Any]]) -> Dict[str, int]:
    stats = {"inserted": 0, "updated": 0, "unchanged": 0, "stale_skipped": 0, "late_arriving": 0}
    if not records:
        return stats

    watermark = current_watermark(db, contract_id)
    if watermark is not None:
        stats["late_arriving"] = sum(1 for r in records if r["event_ts"] is not None and r["event_ts"] < watermark)

    existing: Dict[str, tuple] = {}
    keys = [r["record_key"] for r in records]
    for start in range(0, len(keys), LOOKUP_CHUNK):
        rows = db.execute(select(ValidRecord.record_key, ValidRecord.record_hash, ValidRecord.event_ts).where(
            ValidRecord.contract_id == contract_id, ValidRecord.record_key.in_(keys[start:start + LOOKUP_CHUNK])))
        existing.update({k: (h, ts) for k, h, ts in rows})

    now = utcnow()
    to_write = []
    for r in records:
        prev = existing.get(r["record_key"])
        if prev is None:
            stats["inserted"] += 1
        elif prev[0] == r["record_hash"]:
            stats["unchanged"] += 1
            continue
        elif prev[1] is not None and r["event_ts"] is not None and r["event_ts"] < prev[1]:
            stats["stale_skipped"] += 1
            continue
        else:
            stats["updated"] += 1
        to_write.append({
            "contract_id": contract_id, "contract_version_id": contract_version_id,
            "record_key": r["record_key"], "primary_key_value": r["primary_key_value"][:255],
            "payload": r["payload_json"], "record_hash": r["record_hash"],
            "event_ts": r["event_ts"], "source_file_id": source_file_id, "source_row_number": r["row"] + 1,
            "run_id": run_pk, "now": now,
        })
    for start in range(0, len(to_write), WRITE_CHUNK):
        write_chunk(db, to_write[start:start + WRITE_CHUNK])
    return stats


def write_chunk(db: Session, rows: List[Dict[str, Any]]) -> None:
    """Separate function so tests can inject a failure in the middle of publishing."""
    db.execute(UPSERT_SQL, rows)
