"""Source files and pipeline runs."""
from datetime import datetime
from typing import Optional

from sqlalchemy import (JSON, BigInteger, Boolean, ForeignKey, Index, Integer,
                        String, Text)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base
from backend.models._types import Timestamp
from backend.utils.id_generator import utcnow


class RunStatus:
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_ERRORS = "COMPLETED_WITH_ERRORS"
    FAILED = "FAILED"
    SKIPPED_DUPLICATE = "SKIPPED_DUPLICATE"

    SUCCESSFUL = (COMPLETED, COMPLETED_WITH_ERRORS)
    ACTIVE = (PENDING, RUNNING)


class SourceFile(Base):
    """One physical file, identified by content (SHA-256). Re-uploads reuse the row."""
    __tablename__ = "source_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_path: Mapped[str] = mapped_column(String(500), nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    file_format: Mapped[str] = mapped_column(String(10), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow, nullable=False)


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"
    __table_args__ = (
        Index("ix_runs_idempotency_status", "idempotency_key", "status"),
        Index("ix_runs_created", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    contract_id: Mapped[int] = mapped_column(ForeignKey("data_contracts.id"), nullable=False)
    contract_version_id: Mapped[int] = mapped_column(ForeignKey("contract_versions.id"), nullable=False)
    source_file_id: Mapped[int] = mapped_column(ForeignKey("source_files.id"), nullable=False)
    source_file_name: Mapped[str] = mapped_column(String(255), nullable=False)  # name as uploaded this time
    source_system: Mapped[Optional[str]] = mapped_column(String(100))
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    force_reprocess: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    duplicate_of_run_id: Mapped[Optional[str]] = mapped_column(String(40))

    status: Mapped[str] = mapped_column(String(30), nullable=False, default=RunStatus.PENDING, index=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(Timestamp)
    ended_at: Mapped[Optional[datetime]] = mapped_column(Timestamp)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer)

    input_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    valid_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rejected_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    published_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)   # inserted + updated
    inserted_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unchanged_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)   # identical re-delivery
    stale_skipped_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # older than published
    late_arriving_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    schema_change_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    breaking_change_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    non_breaking_change_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    failure_reason: Mapped[Optional[str]] = mapped_column(Text)
    transformation_summary: Mapped[Optional[dict]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow, onupdate=utcnow, nullable=False)

    source_file: Mapped[SourceFile] = relationship()
    version_ref = relationship("ContractVersion")
    contract = relationship("DataContract")
