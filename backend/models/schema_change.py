"""Detected schema changes.

scope = BATCH     incoming file compared with its contract (run_id is set)
scope = CONTRACT  contract version N compared with N-1 (run_id is NULL)
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.models._types import Timestamp
from backend.utils.id_generator import utcnow


class SchemaChange(Base):
    __tablename__ = "schema_changes"
    __table_args__ = (
        Index("ix_schema_changes_run", "run_id"),
        Index("ix_schema_changes_class", "classification"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scope: Mapped[str] = mapped_column(String(10), nullable=False)
    run_id: Mapped[Optional[int]] = mapped_column(ForeignKey("pipeline_runs.id", ondelete="CASCADE"))
    contract_version_id: Mapped[int] = mapped_column(ForeignKey("contract_versions.id", ondelete="CASCADE"), nullable=False)
    change_type: Mapped[str] = mapped_column(String(50), nullable=False)
    column_name: Mapped[Optional[str]] = mapped_column(String(100))
    old_schema: Mapped[Optional[dict]] = mapped_column(JSON)
    new_schema: Mapped[Optional[dict]] = mapped_column(JSON)
    severity: Mapped[str] = mapped_column(String(10), nullable=False)          # INFO | WARNING | CRITICAL
    classification: Mapped[str] = mapped_column(String(15), nullable=False)    # BREAKING | NON_BREAKING
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    detected_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow, nullable=False)
