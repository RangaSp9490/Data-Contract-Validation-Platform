from datetime import date
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Response, status
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.schemas.pipeline_schema import MetricsSummary, RunList, RunOut
from backend.services import pipeline_service, run_service
from backend.services.errors import BadRequestError

router = APIRouter(prefix="/api", tags=["pipeline runs"])


@router.get("/runs", response_model=RunList)
def list_runs(status_filter: Optional[str] = Query(None, alias="status"), contract_id: Optional[int] = None,
              limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0), db: Session = Depends(get_db)):
    return run_service.list_runs(db, status_filter, contract_id, limit, offset)


@router.get("/runs/{run_id}", response_model=RunOut)
def get_run(run_id: str, db: Session = Depends(get_db)):
    return run_service.run_out(run_service.get_run(db, run_id))


@router.post("/runs/{run_id}/retry", response_model=RunOut, status_code=status.HTTP_202_ACCEPTED)
def retry_run(run_id: str, response: Response, background: BackgroundTasks,
              force: bool = Query(False, description="Reprocess even if the batch already succeeded (backfill)"),
              sync: bool = Query(False), db: Session = Depends(get_db)):
    """Start a new attempt for the same source file and contract version."""
    new_run = pipeline_service.retry_run(db, run_service.get_run(db, run_id), force=force)
    if sync:
        pipeline_service.process_run(new_run.id)
        db.expire_all()
        response.status_code = status.HTTP_200_OK
    else:
        background.add_task(pipeline_service.process_run, new_run.id)
    return run_service.run_out(run_service.get_run(db, new_run.run_id))


@router.get("/metrics/summary", response_model=MetricsSummary)
def metrics_summary(contract_id: Optional[int] = None,
                    date_from: Optional[date] = Query(None, description="YYYY-MM-DD (UTC, inclusive)"),
                    date_to: Optional[date] = Query(None, description="YYYY-MM-DD (UTC, inclusive)"),
                    db: Session = Depends(get_db)):
    """Operational metrics for the dashboard (all computed from the database), optionally filtered."""
    if date_from and date_to and date_from > date_to:
        raise BadRequestError("date_from must be on or before date_to")
    return run_service.metrics(db, contract_id, date_from, date_to)
