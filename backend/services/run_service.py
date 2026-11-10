"""Read side for runs, reports, published records and dashboard metrics."""
from datetime import date, datetime, time, timedelta
from typing import List, Optional

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, aliased

from backend.models import (ContractVersion, DataContract, PipelineRun,
                            RejectionError, RunStatus, SchemaChange,
                            SourceFile, ValidRecord)
from backend.schemas.contract_schema import ChangeOut
from backend.schemas.pipeline_schema import (ErrorCount, MetricsSummary,
                                             RejectedOut, RunList, RunOut,
                                             RunReport, ValidRecordList,
                                             ValidRecordOut)
from backend.services import quarantine_service
from backend.services.data_validator import ERROR_CODES
from backend.services.errors import NotFoundError


def get_run(db: Session, run_id: str) -> PipelineRun:
    run = db.scalar(select(PipelineRun).where(PipelineRun.run_id == run_id))
    if run is None:
        raise NotFoundError(f"Run {run_id} not found")
    return run


def run_out(run: PipelineRun) -> RunOut:
    out = RunOut.model_validate(run)
    out.contract_name = run.contract.name if run.contract else None
    out.dataset_name = run.contract.dataset_name if run.contract else None
    out.contract_version = run.version_ref.version if run.version_ref else None
    out.source_checksum = run.source_file.checksum_sha256 if run.source_file else None
    processed = run.valid_count + run.rejected_count
    out.success_rate = round(100.0 * run.valid_count / processed, 2) if processed else None
    return out


def list_runs(db: Session, status: Optional[str] = None, contract_id: Optional[int] = None,
              limit: int = 50, offset: int = 0) -> RunList:
    stmt = select(PipelineRun)
    if status:
        stmt = stmt.where(PipelineRun.status == status)
    if contract_id:
        stmt = stmt.where(PipelineRun.contract_id == contract_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    runs = db.scalars(stmt.order_by(PipelineRun.id.desc()).limit(limit).offset(offset)).all()
    return RunList(total=total, items=[run_out(r) for r in runs])


def rejected_out(records, ctx) -> List[RejectedOut]:
    return [RejectedOut(
        id=r.id, run_id=ctx["runs"].get(r.run_id), source_file=ctx["files"].get(r.source_file_id),
        source_row_number=r.source_row_number, original_record=r.original_record, error_codes=r.error_codes,
        error_count=r.error_count, rejected_at=r.rejected_at, errors=r.errors) for r in records]


def run_report(db: Session, run_id: str) -> RunReport:
    run = get_run(db, run_id)
    changes = db.scalars(select(SchemaChange).where(SchemaChange.run_id == run.id).order_by(SchemaChange.id)).all()
    summary = [ErrorCount(**e, description=ERROR_CODES.get(e["error_code"]))
               for e in quarantine_service.error_summary(db, run.id)]
    _, sample = quarantine_service.list_rejected(db, run_id=run_id, limit=20)
    return RunReport(run=run_out(run), schema_changes=[ChangeOut.model_validate(c) for c in changes],
                     error_summary=summary,
                     sample_rejections=rejected_out(sample, quarantine_service.lookup_context(db, sample)))


def list_schema_changes(db: Session, classification: Optional[str], scope: Optional[str],
                        contract_id: Optional[int], limit: int = 100):
    stmt = select(SchemaChange, PipelineRun.run_id, ContractVersion.version, DataContract.dataset_name) \
        .join(ContractVersion, ContractVersion.id == SchemaChange.contract_version_id) \
        .join(DataContract, DataContract.id == ContractVersion.contract_id) \
        .outerjoin(PipelineRun, PipelineRun.id == SchemaChange.run_id)
    if classification:
        stmt = stmt.where(SchemaChange.classification == classification)
    if scope:
        stmt = stmt.where(SchemaChange.scope == scope)
    if contract_id:
        stmt = stmt.where(DataContract.id == contract_id)
    rows = db.execute(stmt.order_by(SchemaChange.id.desc()).limit(limit)).all()
    return [{**ChangeOut.model_validate(c).model_dump(), "id": c.id, "scope": c.scope, "run_id": rid,
             "contract_version": ver, "dataset_name": ds, "detected_at": c.detected_at}
            for c, rid, ver, ds in rows]


def list_valid_records(db: Session, contract_id: int, limit: int = 50, offset: int = 0,
                       search: Optional[str] = None) -> ValidRecordList:
    first, last = aliased(PipelineRun), aliased(PipelineRun)
    base = select(ValidRecord).where(ValidRecord.contract_id == contract_id)
    if search:
        base = base.where(ValidRecord.primary_key_value.like(f"%{search}%"))
    total = db.scalar(select(func.count()).select_from(base.subquery()))
    stmt = base.add_columns(first.run_id, last.run_id) \
        .join(first, first.id == ValidRecord.first_run_id).join(last, last.id == ValidRecord.last_run_id) \
        .order_by(ValidRecord.primary_key_value).limit(limit).offset(offset)
    items = [ValidRecordOut(primary_key_value=v.primary_key_value, payload=v.payload, event_ts=v.event_ts,
                            contract_version_id=v.contract_version_id, source_row_number=v.source_row_number,
                            first_run_id=f, last_run_id=l, updated_at=v.updated_at)
             for v, f, l in db.execute(stmt).all()]
    return ValidRecordList(total=total, items=items)


def _run_filters(contract_id: Optional[int], date_from: Optional[date], date_to: Optional[date]) -> list:
    conds = []
    if contract_id:
        conds.append(PipelineRun.contract_id == contract_id)
    if date_from:
        conds.append(PipelineRun.created_at >= datetime.combine(date_from, time.min))
    if date_to:
        conds.append(PipelineRun.created_at < datetime.combine(date_to + timedelta(days=1), time.min))
    return conds


def metrics(db: Session, contract_id: Optional[int] = None, date_from: Optional[date] = None,
            date_to: Optional[date] = None) -> MetricsSummary:
    """Dashboard metrics, optionally filtered by contract and by run creation date (UTC, inclusive)."""
    rf = _run_filters(contract_id, date_from, date_to)
    by_status = dict(db.execute(select(PipelineRun.status, func.count()).where(*rf)
                                .group_by(PipelineRun.status)).all())
    agg = db.execute(select(
        func.count(), func.coalesce(func.sum(PipelineRun.input_count), 0),
        func.coalesce(func.sum(PipelineRun.valid_count), 0), func.coalesce(func.sum(PipelineRun.rejected_count), 0),
        func.coalesce(func.sum(PipelineRun.published_count), 0), func.avg(PipelineRun.duration_ms),
        func.coalesce(func.sum(PipelineRun.duration_ms), 0),
    ).where(PipelineRun.status != RunStatus.SKIPPED_DUPLICATE, *rf)).one()
    _, processed, valid, rejected, published, avg_ms, total_ms = agg

    sc = select(func.count(), func.coalesce(func.sum(case((SchemaChange.classification == "BREAKING", 1), else_=0)), 0)) \
        .join(ContractVersion, ContractVersion.id == SchemaChange.contract_version_id)
    if contract_id:
        sc = sc.where(ContractVersion.contract_id == contract_id)
    if date_from:
        sc = sc.where(SchemaChange.detected_at >= datetime.combine(date_from, time.min))
    if date_to:
        sc = sc.where(SchemaChange.detected_at < datetime.combine(date_to + timedelta(days=1), time.min))
    changes = db.execute(sc).one()

    top = db.execute(select(RejectionError.error_code, func.count())
                     .join(PipelineRun, PipelineRun.id == RejectionError.run_id).where(*rf)
                     .group_by(RejectionError.error_code).order_by(func.count().desc()).limit(10)).all()
    recent = db.scalars(select(PipelineRun).where(*rf).order_by(PipelineRun.id.desc()).limit(10)).all()

    day = func.date(PipelineRun.created_at)
    daily_rows = db.execute(select(
        day, func.count(), func.coalesce(func.sum(PipelineRun.input_count), 0),
        func.coalesce(func.sum(PipelineRun.valid_count), 0), func.coalesce(func.sum(PipelineRun.rejected_count), 0),
        func.coalesce(func.sum(case((PipelineRun.status == RunStatus.FAILED, 1), else_=0)), 0),
    ).where(*rf).group_by(day).order_by(day.desc()).limit(31)).all()
    daily = [{"date": str(d), "runs": int(n), "processed": int(p), "valid": int(v), "rejected": int(rj),
              "failed_runs": int(fl), "success_pct": round(100.0 * int(v) / (int(v) + int(rj)), 2) if (int(v) + int(rj)) else None}
             for d, n, p, v, rj, fl in reversed(daily_rows)]

    pub = select(func.count()).select_from(ValidRecord)
    if contract_id:
        pub = pub.where(ValidRecord.contract_id == contract_id)
    checked = int(valid) + int(rejected)
    return MetricsSummary(
        total_runs=sum(by_status.values()), runs_by_status=by_status,
        failed_runs=by_status.get(RunStatus.FAILED, 0),
        total_records_processed=int(processed), valid_records=int(valid), rejected_records=int(rejected),
        published_records=int(published),
        validation_success_pct=round(100.0 * int(valid) / checked, 2) if checked else None,
        avg_duration_ms=round(float(avg_ms), 1) if avg_ms is not None else None, total_duration_ms=int(total_ms),
        schema_changes=int(changes[0]), breaking_changes=int(changes[1]),
        non_breaking_changes=int(changes[0]) - int(changes[1]),
        published_rows_current=db.scalar(pub),
        contracts=db.scalar(select(func.count()).select_from(DataContract)),
        top_error_codes=[ErrorCount(error_code=c, column_name=None, count=n, description=ERROR_CODES.get(c))
                         for c, n in top],
        recent_runs=[run_out(r) for r in recent],
        daily=daily,
        filters={"contract_id": contract_id, "date_from": str(date_from) if date_from else None,
                 "date_to": str(date_to) if date_to else None},
    )


def export_valid_records_csv(db: Session, contract_id: int):
    """Stream the published records of a contract as CSV (columns of the latest contract version)."""
    import csv
    import io

    from backend.services import contract_service

    columns = [c.name for c in contract_service.get_version(db, contract_id).columns]
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(columns + ["_contract_version_id", "_event_ts", "_last_updated_utc"])
    yield buf.getvalue()
    last_id = 0
    while True:
        rows = db.scalars(select(ValidRecord).where(ValidRecord.contract_id == contract_id, ValidRecord.id > last_id)
                          .order_by(ValidRecord.id).limit(2000)).all()
        if not rows:
            break
        buf.seek(0)
        buf.truncate()
        for r in rows:
            writer.writerow([("" if r.payload.get(c) is None else r.payload.get(c)) for c in columns] +
                            [r.contract_version_id, r.event_ts.isoformat() if r.event_ts else "", r.updated_at.isoformat()])
        yield buf.getvalue()
        last_id = rows[-1].id