"""Pipeline orchestration: one call to process_run() executes a whole batch.

State machine
  PENDING -> SKIPPED_DUPLICATE   identical batch (checksum + contract version) already succeeded
  PENDING -> RUNNING -> FAILED   unreadable file / breaking schema change / unexpected error
  PENDING -> RUNNING -> COMPLETED | COMPLETED_WITH_ERRORS

Transaction boundaries
  T1  run -> RUNNING (committed, so progress is visible to the dashboard)
  T2  schema changes + quarantined rows + published rows + final status, ONE commit.
      If anything in T2 fails, it is rolled back entirely and the run is marked FAILED
      in a separate transaction - a run is never "successful" with half its rows published.
"""
import logging
import time
import uuid
from typing import List, Optional

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend import database
from backend.models import (ContractVersion, PipelineRun, RunStatus,
                            SchemaChange, SourceFile)
from backend.services import (contract_service, data_validator,
                              publishing_service, quarantine_service,
                              schema_validator, transformation_service)
from backend.services.compatibility_service import Change
from backend.services.ingestion_service import FileParseError, parse_file
from backend.utils.id_generator import idempotency_key, new_run_id, utcnow

log = logging.getLogger(__name__)


def create_run(db: Session, version: ContractVersion, source: SourceFile, file_name: str,
               source_system: Optional[str], force: bool = False, attempt: int = 1) -> PipelineRun:
    run = PipelineRun(
        run_id=new_run_id(), contract_id=version.contract_id, contract_version_id=version.id,
        source_file_id=source.id, source_file_name=file_name[:255], source_system=source_system,
        idempotency_key=idempotency_key(source.checksum_sha256, version.id),
        attempt=attempt, force_reprocess=force, status=RunStatus.PENDING,
    )
    db.add(run)
    db.commit()
    log.info("run created", extra={"run_id": run.run_id, "file": file_name, "contract_version_id": version.id})
    return run


def _finish(run: PipelineRun, status: str, t0: float, reason: Optional[str] = None) -> None:
    run.status = status
    run.ended_at = utcnow()
    run.duration_ms = int((time.perf_counter() - t0) * 1000)
    run.failure_reason = reason


def _find_duplicate(db: Session, run: PipelineRun) -> Optional[PipelineRun]:
    return db.scalar(select(PipelineRun).where(
        PipelineRun.idempotency_key == run.idempotency_key, PipelineRun.id != run.id,
        PipelineRun.status.in_(RunStatus.SUCCESSFUL + (RunStatus.RUNNING,)),
    ).order_by(PipelineRun.id.desc()).limit(1))


def _store_changes(db: Session, run: PipelineRun, changes: List[Change]) -> None:
    for c in changes:
        db.add(SchemaChange(scope="BATCH", run_id=run.id, contract_version_id=run.contract_version_id, **c.to_dict()))
    breaking = sum(c.is_breaking for c in changes)
    run.schema_change_count = len(changes)
    run.breaking_change_count = breaking
    run.non_breaking_change_count = len(changes) - breaking


def process_run(run_pk: int) -> None:
    """Execute a PENDING run. Safe to call from a background task: never raises."""
    db = database.new_session()
    t0 = time.perf_counter()
    try:
        run = db.get(PipelineRun, run_pk)
        if run is None or run.status != RunStatus.PENDING:
            return
        duplicate = None if run.force_reprocess else _find_duplicate(db, run)
        if duplicate is not None:
            run.duplicate_of_run_id = duplicate.run_id
            what = "is already being processed" if duplicate.status == RunStatus.RUNNING else "was already processed"
            _finish(run, RunStatus.SKIPPED_DUPLICATE, t0,
                    f"Identical batch (same checksum and contract version) {what} by {duplicate.run_id}; "
                    "nothing was republished. Use force=true to reprocess.")
            run.started_at = run.ended_at
            db.commit()
            log.info("duplicate batch skipped", extra={"run_id": run.run_id, "duplicate_of": duplicate.run_id})
            return

        run.status, run.started_at = RunStatus.RUNNING, utcnow()
        db.commit()                                                       # T1
        _execute(db, run, t0)
    except Exception as exc:  # noqa: BLE001 - last line of defence, recorded on the run
        db.rollback()
        error_id = uuid.uuid4().hex[:12]
        log.error("pipeline run failed unexpectedly", exc_info=exc, extra={"run_pk": run_pk, "error_id": error_id})
        run = db.get(PipelineRun, run_pk)
        if run is not None:
            _finish(run, RunStatus.FAILED, t0,
                    f"Unexpected processing error ({type(exc).__name__}, error_id={error_id}). All changes of "
                    "this run were rolled back; nothing was published. The run can be retried safely.")
            run.published_count = run.inserted_count = run.updated_count = 0
            run.valid_count = run.rejected_count = 0
            db.commit()
    finally:
        db.close()


def _execute(db: Session, run: PipelineRun, t0: float) -> None:
    version = contract_service.get_version_by_id(db, run.contract_version_id)
    source = db.get(SourceFile, run.source_file_id)
    mode = version.compatibility_mode

    timings = {}
    mark = time.perf_counter()

    def lap(stage: str) -> None:
        nonlocal mark
        now = time.perf_counter()
        timings[stage] = round((now - mark) * 1000, 1)
        mark = now

    # 1. parse
    try:
        batch = parse_file(source.stored_path, source.file_format)
    except FileParseError as exc:
        _finish(run, RunStatus.FAILED, t0, f"Input rejected: {exc}")
        db.commit()
        log.warning("input rejected", extra={"run_id": run.run_id, "reason": str(exc)})
        return
    run.input_count = batch.row_count
    lap("parse_ms")

    # 2. schema validation + transformation
    structural, rename_map = schema_validator.compare_columns(batch, version.columns, mode)
    tr = transformation_service.transform(batch, version.columns, rename_map)
    changes = structural + schema_validator.detect_value_drift(batch, tr, version.columns, mode)
    _store_changes(db, run, changes)
    lap("schema_and_transform_ms")
    run.transformation_summary = {**tr.summary, "stage_timings_ms": timings}

    breaking = [c for c in changes if c.is_breaking]
    if breaking:
        detail = "; ".join(f"{c.change_type}({c.column_name})" for c in breaking)
        _finish(run, RunStatus.FAILED, t0,
                f"Blocked by {len(breaking)} breaking schema change(s): {detail}. No records were published.")
        db.commit()
        log.warning("breaking schema change", extra={"run_id": run.run_id, "changes": detail})
        return

    # 3. record validation
    outcome = data_validator.validate_records(
        batch, tr, version,
        fk_lookup=lambda ref_id, values: publishing_service.lookup_published_keys(db, ref_id, values),
        now=pd.Timestamp.now(tz="UTC"),
    )
    lap("record_validation_ms")

    # 4. quarantine + publish (same transaction)
    run.rejected_count = quarantine_service.quarantine(db, run, batch, outcome)
    lap("quarantine_ms")
    valid_idx = outcome.valid_mask[outcome.valid_mask].index
    run.valid_count = len(valid_idx)
    serial = tr.serialized(version.columns).loc[valid_idx]
    event_ts = tr.data[version.event_time_column].loc[valid_idx] if version.event_time_column else None
    records = publishing_service.build_records(serial, version.primary_key, version.contract_id, event_ts)
    lap("build_records_ms")
    stats = publishing_service.publish_records(
        db, contract_id=version.contract_id, contract_version_id=version.id, run_pk=run.id,
        source_file_id=run.source_file_id, records=records)
    run.inserted_count, run.updated_count = stats["inserted"], stats["updated"]
    run.unchanged_count, run.stale_skipped_count = stats["unchanged"], stats["stale_skipped"]
    run.late_arriving_count = stats["late_arriving"]
    run.published_count = stats["inserted"] + stats["updated"]
    lap("publish_ms")
    run.transformation_summary = {**tr.summary, "stage_timings_ms": dict(timings)}

    status = RunStatus.COMPLETED if run.rejected_count == 0 else RunStatus.COMPLETED_WITH_ERRORS
    _finish(run, status, t0)
    db.commit()                                                           # T2
    log.info("run finished", extra={
        "run_id": run.run_id, "status": status, "input": run.input_count, "valid": run.valid_count,
        "rejected": run.rejected_count, "published": run.published_count, "duration_ms": run.duration_ms})


def recover_interrupted_runs() -> int:
    """Called at server start-up. Runs execute inside the server process, so any run still
    PENDING/RUNNING when the server starts was interrupted by the previous shutdown or crash.
    Its work was never committed (single transaction), so nothing was published: mark it
    FAILED with a clear reason so it can be retried."""
    db = database.new_session()
    try:
        stuck = db.scalars(select(PipelineRun).where(PipelineRun.status.in_(RunStatus.ACTIVE))).all()
        now = utcnow()
        for run in stuck:
            previous = run.status
            run.status = RunStatus.FAILED
            run.ended_at = now
            run.failure_reason = (f"Interrupted: the server stopped while this run was {previous}. "
                                  "No records were published (the run's work is a single transaction). "
                                  "Retry the run to process the batch again.")
        db.commit()
        if stuck:
            log.warning("interrupted runs recovered", extra={"runs": [r.run_id for r in stuck]})
        return len(stuck)
    finally:
        db.close()


def retry_run(db: Session, previous: PipelineRun, force: bool = False) -> PipelineRun:
    """New attempt for the same file + contract version (the old run stays as history)."""
    version = contract_service.get_version_by_id(db, previous.contract_version_id)
    source = db.get(SourceFile, previous.source_file_id)
    return create_run(db, version, source, previous.source_file_name, previous.source_system,
                      force=force, attempt=previous.attempt + 1)
