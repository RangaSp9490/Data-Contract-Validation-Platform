"""Quarantine: persist rejected records with their reasons, and query/export them.

The stored `original_record` is the row exactly as received (before trimming, casting
or normalisation), so every rejection is traceable to the source file + row number.
"""
import csv
import io
import json
from typing import Any, Dict, Iterator, List, Optional, Tuple

import pandas as pd
from sqlalchemy import func, insert, select
from sqlalchemy.orm import Session, selectinload

from backend.models import (ContractVersion, PipelineRun, RejectedRecord,
                            RejectionError, SourceFile)
from backend.services.data_validator import ValidationOutcome
from backend.services.errors import NotFoundError
from backend.services.ingestion_service import ParsedBatch
from backend.utils.id_generator import utcnow

INSERT_CHUNK = 2000


def _original_record(batch: ParsedBatch, i: int) -> Dict[str, Any]:
    if i in batch.malformed_raw:
        raw = batch.malformed_raw[i]
        return {"_raw_fields": raw} if isinstance(raw, list) else {"_raw_value": raw}
    row = batch.original.iloc[i]
    return {k: (None if v is None or (isinstance(v, float) and pd.isna(v)) else v) for k, v in row.items()}


def quarantine(db: Session, run: PipelineRun, batch: ParsedBatch, outcome: ValidationOutcome) -> int:
    """Insert rejected records + their errors (inside the caller's transaction)."""
    if outcome.errors.empty:
        return 0
    now = utcnow()
    errors = outcome.errors
    codes_per_row = errors.groupby("row", sort=True)["error_code"].agg(lambda s: list(dict.fromkeys(s)))
    counts = errors.groupby("row", sort=True).size()

    records = [{
        "run_id": run.id, "source_file_id": run.source_file_id, "contract_version_id": run.contract_version_id,
        "source_row_number": int(row) + 1, "original_record": _original_record(batch, int(row)),
        "error_codes": codes, "error_count": int(counts[row]), "rejected_at": now,
    } for row, codes in codes_per_row.items()]
    for start in range(0, len(records), INSERT_CHUNK):
        db.execute(insert(RejectedRecord), records[start:start + INSERT_CHUNK])

    ids = dict(db.execute(select(RejectedRecord.source_row_number, RejectedRecord.id)
                          .where(RejectedRecord.run_id == run.id)).all())
    error_rows = [{
        "rejected_record_id": ids[int(r.row) + 1], "run_id": run.id, "error_code": r.error_code,
        "column_name": r.column_name, "message": str(r.message)[:500],
        "observed_value": None if r.observed_value is None or pd.isna(r.observed_value) else str(r.observed_value)[:255],
    } for r in errors.itertuples(index=False)]
    for start in range(0, len(error_rows), INSERT_CHUNK):
        db.execute(insert(RejectionError), error_rows[start:start + INSERT_CHUNK])
    return len(records)


# ------------------------------------------------------------------ queries
def _filtered(run_id: Optional[str], error_code: Optional[str], contract_id: Optional[int]):
    stmt = select(RejectedRecord)
    if run_id:
        stmt = stmt.join(PipelineRun, PipelineRun.id == RejectedRecord.run_id).where(PipelineRun.run_id == run_id)
    if contract_id:
        stmt = stmt.join(ContractVersion, ContractVersion.id == RejectedRecord.contract_version_id) \
                   .where(ContractVersion.contract_id == contract_id)
    if error_code:
        stmt = stmt.where(RejectedRecord.id.in_(
            select(RejectionError.rejected_record_id).where(RejectionError.error_code == error_code)))
    return stmt


def list_rejected(db: Session, run_id: Optional[str] = None, error_code: Optional[str] = None,
                  contract_id: Optional[int] = None, limit: int = 50, offset: int = 0
                  ) -> Tuple[int, List[RejectedRecord]]:
    base = _filtered(run_id, error_code, contract_id)
    total = db.scalar(select(func.count()).select_from(base.subquery()))
    rows = db.scalars(base.options(selectinload(RejectedRecord.errors))
                      .order_by(RejectedRecord.run_id.desc(), RejectedRecord.source_row_number)
                      .limit(limit).offset(offset)).all()
    return total, rows


def get_rejected(db: Session, rejected_id: int) -> RejectedRecord:
    rec = db.scalar(select(RejectedRecord).where(RejectedRecord.id == rejected_id)
                    .options(selectinload(RejectedRecord.errors)))
    if rec is None:
        raise NotFoundError(f"Rejected record {rejected_id} not found")
    return rec


def lookup_context(db: Session, records: List[RejectedRecord]) -> Dict[str, Dict[int, str]]:
    """run pk -> run_id string, source_file pk -> file name (for API responses/exports)."""
    run_ids = {r.run_id for r in records}
    file_ids = {r.source_file_id for r in records}
    runs = dict(db.execute(select(PipelineRun.id, PipelineRun.run_id).where(PipelineRun.id.in_(run_ids))).all()) \
        if run_ids else {}
    files = dict(db.execute(select(SourceFile.id, SourceFile.file_name).where(SourceFile.id.in_(file_ids))).all()) \
        if file_ids else {}
    return {"runs": runs, "files": files}


def error_summary(db: Session, run_pk: int) -> List[Dict[str, Any]]:
    rows = db.execute(select(RejectionError.error_code, RejectionError.column_name, func.count())
                      .where(RejectionError.run_id == run_pk)
                      .group_by(RejectionError.error_code, RejectionError.column_name)
                      .order_by(func.count().desc())).all()
    return [{"error_code": code, "column_name": col, "count": n} for code, col, n in rows]


def export_csv(db: Session, run_id: Optional[str], error_code: Optional[str],
               contract_id: Optional[int]) -> Iterator[str]:
    """Stream rejected records as CSV, 1000 rows at a time."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["run_id", "source_file", "source_row_number", "error_codes", "rejection_reasons",
                     "original_record"])
    yield buf.getvalue()
    offset = 0
    while True:
        _, rows = list_rejected(db, run_id, error_code, contract_id, limit=1000, offset=offset)
        if not rows:
            break
        ctx = lookup_context(db, rows)
        buf.seek(0)
        buf.truncate()
        for r in rows:
            reasons = " | ".join(f"[{e.error_code}] {e.message}" + (f" (value: {e.observed_value})"
                                                                     if e.observed_value is not None else "")
                                 for e in r.errors)
            writer.writerow([ctx["runs"].get(r.run_id), ctx["files"].get(r.source_file_id), r.source_row_number,
                             ";".join(r.error_codes), reasons, json.dumps(r.original_record, default=str)])
        yield buf.getvalue()
        offset += 1000
