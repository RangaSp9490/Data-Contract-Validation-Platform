"""Published (valid) and quarantined (rejected) records."""
from datetime import datetime
from typing import Any, List, Optional

from sqlalchemy import (JSON, BigInteger, ForeignKey, Index, Integer, String,
                        UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base
from backend.models._types import Timestamp
from backend.utils.id_generator import utcnow


class ValidRecord(Base):
    """Published target table (generic: payload is JSON, one row per contract PK).

    UNIQUE(contract_id, record_key) is the idempotency guarantee: record_key is
    sha256(contract_id | primary key value), so republishing a batch can only
    update rows in place, never duplicate them.
    """
    __tablename__ = "valid_records"
    __table_args__ = (
        UniqueConstraint("contract_id", "record_key", name="uq_valid_contract_record_key"),
        Index("ix_valid_contract_pk", "contract_id", "primary_key_value"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    contract_id: Mapped[int] = mapped_column(ForeignKey("data_contracts.id"), nullable=False)
    contract_version_id: Mapped[int] = mapped_column(ForeignKey("contract_versions.id"), nullable=False)
    record_key: Mapped[str] = mapped_column(String(64), nullable=False)
    primary_key_value: Mapped[str] = mapped_column(String(255), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    record_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    event_ts: Mapped[Optional[datetime]] = mapped_column(Timestamp)
    source_file_id: Mapped[int] = mapped_column(ForeignKey("source_files.id"), nullable=False)
    source_row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    first_run_id: Mapped[int] = mapped_column(ForeignKey("pipeline_runs.id"), nullable=False)
    last_run_id: Mapped[int] = mapped_column(ForeignKey("pipeline_runs.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow, nullable=False)


class RejectedRecord(Base):
    """Quarantine: the original row exactly as received, plus why it was rejected."""
    __tablename__ = "rejected_records"
    __table_args__ = (UniqueConstraint("run_id", "source_row_number", name="uq_rejected_run_row"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("pipeline_runs.id", ondelete="CASCADE"), nullable=False)
    source_file_id: Mapped[int] = mapped_column(ForeignKey("source_files.id"), nullable=False)
    contract_version_id: Mapped[int] = mapped_column(ForeignKey("contract_versions.id"), nullable=False)
    source_row_number: Mapped[int] = mapped_column(Integer, nullable=False)  # 1-based data row (header excluded)
    original_record: Mapped[dict] = mapped_column(JSON, nullable=False)
    error_codes: Mapped[List[str]] = mapped_column(JSON, nullable=False)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False)
    rejected_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow, nullable=False)

    errors: Mapped[List["RejectionError"]] = relationship(
        back_populates="rejected_record", cascade="all, delete-orphan", order_by="RejectionError.id"
    )


class RejectionError(Base):
    """One row per validation error, so quarantine can be filtered by error type via an index."""
    __tablename__ = "rejection_errors"
    __table_args__ = (
        Index("ix_rejection_errors_run_code", "run_id", "error_code"),
        Index("ix_rejection_errors_code", "error_code"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    rejected_record_id: Mapped[int] = mapped_column(ForeignKey("rejected_records.id", ondelete="CASCADE"), nullable=False)
    run_id: Mapped[int] = mapped_column(ForeignKey("pipeline_runs.id", ondelete="CASCADE"), nullable=False)
    error_code: Mapped[str] = mapped_column(String(50), nullable=False)
    column_name: Mapped[Optional[str]] = mapped_column(String(100))
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    observed_value: Mapped[Optional[str]] = mapped_column(String(255))

    rejected_record: Mapped[RejectedRecord] = relationship(back_populates="errors")
