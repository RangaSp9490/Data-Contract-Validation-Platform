from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.services import llm_service

router = APIRouter(prefix="/api/ai", tags=["ai assistant"])


class ExplainRequest(BaseModel):
    task: llm_service.Task
    run_id: Optional[str] = Field(default=None, max_length=40)
    contract_id: Optional[int] = None
    version: Optional[int] = None


@router.get("/status")
def ai_status():
    """Which LLM provider is configured and whether it is reachable (never returns the API key)."""
    return llm_service.status()


@router.post("/explain", response_model=llm_service.AIResponse)
def explain(req: ExplainRequest, db: Session = Depends(get_db)):
    """Explain a run / its rejections / schema changes, or a contract version. Advisory only."""
    return llm_service.explain(db, req.task, req.run_id, req.contract_id, req.version)
