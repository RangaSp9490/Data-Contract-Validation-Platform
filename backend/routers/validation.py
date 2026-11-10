from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from backend.database import get_db, new_session
from backend.schemas.pipeline_schema import (RejectedList, RejectedOut,
                                             RunReport, ValidRecordList)
from backend.services import quarantine_service, run_service
from backend.services.data_validator import ERROR_CODES

router = APIRouter(prefix="/api", tags=["validation & quarantine"])


@router.get("/runs/{run_id}/report", response_model=RunReport)
def run_report(run_id: str, db: Session = Depends(get_db)):
    """Schema changes, error counts by code/column and sample rejections for one run."""
    return run_service.run_report(db, run_id)


@router.get("/schema-changes")
def schema_changes(classification: Optional[str] = Query(None, pattern="^(BREAKING|NON_BREAKING)$"),
                   scope: Optional[str] = Query(None, pattern="^(BATCH|CONTRACT)$"),
                   contract_id: Optional[int] = None, limit: int = Query(100, ge=1, le=1000),
                   db: Session = Depends(get_db)):
    return run_service.list_schema_changes(db, classification, scope, contract_id, limit)


@router.get("/error-codes")
def error_codes():
    return [{"error_code": k, "description": v} for k, v in ERROR_CODES.items()]


@router.get("/rejected", response_model=RejectedList)
def list_rejected(run_id: Optional[str] = None, error_code: Optional[str] = None,
                  contract_id: Optional[int] = None, limit: int = Query(50, ge=1, le=500),
                  offset: int = Query(0, ge=0), db: Session = Depends(get_db)):
    total, rows = quarantine_service.list_rejected(db, run_id, error_code, contract_id, limit, offset)
    return RejectedList(total=total, items=run_service.rejected_out(rows, quarantine_service.lookup_context(db, rows)))


@router.get("/rejected/export")
def export_rejected(run_id: Optional[str] = None, error_code: Optional[str] = None,
                    contract_id: Optional[int] = None):
    name = f"rejected_{run_id or 'all'}{'_' + error_code if error_code else ''}.csv"

    def stream():
        # FastAPI closes request-scoped (Depends) sessions BEFORE a streaming body is sent,
        # so the stream owns its own session and always releases it.
        db = new_session()
        try:
            yield from quarantine_service.export_csv(db, run_id, error_code, contract_id)
        finally:
            db.close()

    return StreamingResponse(stream(),
                             media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/rejected/{rejected_id}", response_model=RejectedOut)
def get_rejected(rejected_id: int, db: Session = Depends(get_db)):
    rec = quarantine_service.get_rejected(db, rejected_id)
    return run_service.rejected_out([rec], quarantine_service.lookup_context(db, [rec]))[0]


@router.get("/valid-records/export")
def export_valid_records(contract_id: int, db: Session = Depends(get_db)):
    """Download a contract's published records as CSV."""
    from backend.services import contract_service

    dataset = contract_service.get_contract(db, contract_id).dataset_name   # 404 before streaming starts

    def stream():
        session = new_session()          # own session: Depends sessions close before streaming
        try:
            yield from run_service.export_valid_records_csv(session, contract_id)
        finally:
            session.close()

    return StreamingResponse(stream(), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="published_{dataset}.csv"'})


@router.get("/valid-records", response_model=ValidRecordList)
def valid_records(contract_id: int, search: Optional[str] = Query(None, max_length=100),
                  limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0),
                  db: Session = Depends(get_db)):
    """Published records of a contract (the target dataset)."""
    return run_service.list_valid_records(db, contract_id, limit, offset, search)
