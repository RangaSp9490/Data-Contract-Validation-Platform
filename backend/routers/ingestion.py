from typing import Optional

from fastapi import (APIRouter, BackgroundTasks, Depends, File, Form,
                     Response, UploadFile, status)
from sqlalchemy.orm import Session

from backend.config import get_settings
from backend.database import get_db
from backend.schemas.pipeline_schema import RunOut
from backend.services import (contract_service, ingestion_service,
                              pipeline_service, run_service)

router = APIRouter(prefix="/api/ingestion", tags=["ingestion"])


@router.post("/upload", response_model=RunOut, status_code=status.HTTP_202_ACCEPTED)
def upload_batch(
    response: Response,
    background: BackgroundTasks,
    file: UploadFile = File(..., description="CSV or JSON producer batch"),
    contract_id: int = Form(...),
    version: Optional[int] = Form(None, description="Contract version (default: latest)"),
    source_system: Optional[str] = Form(None, max_length=100),
    force: bool = Form(False, description="Reprocess even if this exact batch already succeeded"),
    sync: bool = Form(False, description="Process inside the request instead of in the background"),
    db: Session = Depends(get_db),
):
    """Accept a batch, store the original file and start a pipeline run.

    Async (default): returns 202 with a PENDING run; poll GET /api/runs/{run_id}.
    Sync: processes immediately and returns 200 with the finished run.
    """
    contract_version = contract_service.get_version(db, contract_id, version)
    data = ingestion_service.read_limited(file.file, get_settings().max_upload_bytes)
    source = ingestion_service.store_upload(db, file.filename or "upload", data)
    run = pipeline_service.create_run(db, contract_version, source, file.filename or source.file_name,
                                      source_system, force=force)
    if sync:
        pipeline_service.process_run(run.id)
        db.expire_all()
        response.status_code = status.HTTP_200_OK
    else:
        background.add_task(pipeline_service.process_run, run.id)
    return run_service.run_out(run_service.get_run(db, run.run_id))
