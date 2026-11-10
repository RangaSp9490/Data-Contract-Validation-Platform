"""Contract tables.

data_contracts        1 ──< contract_versions 1 ──< contract_columns
                                         1 ──< contract_foreign_keys >── data_contracts (referenced)

A version is immutable once created: edits always create version N+1.
"""
from datetime import datetime
from typing import Any, List, Optional

from sqlalchemy import (JSON, Boolean, Float, ForeignKey, Integer, String, Text,
                        UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base
from backend.models._types import Timestamp
from backend.utils.id_generator import utcnow


class DataContract(Base):
    __tablename__ = "data_contracts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    dataset_name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    description: Mapped[Optional[str]] = mapped_column(Text)
    owner: Mapped[Optional[str]] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow, onupdate=utcnow, nullable=False)

    versions: Mapped[List["ContractVersion"]] = relationship(
        back_populates="contract", order_by="ContractVersion.version"
    )

    @property
    def latest_version(self) -> Optional["ContractVersion"]:
        return self.versions[-1] if self.versions else None


class ContractVersion(Base):
    __tablename__ = "contract_versions"
    __table_args__ = (UniqueConstraint("contract_id", "version", name="uq_contract_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    contract_id: Mapped[int] = mapped_column(ForeignKey("data_contracts.id", ondelete="RESTRICT"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    # BACKWARD: additive changes are safe.  STRICT: any surprise is breaking.
    compatibility_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="BACKWARD")
    # Column used for late-arriving / out-of-order handling (newest event wins).
    event_time_column: Mapped[Optional[str]] = mapped_column(String(100))
    # List of column-name lists, e.g. [["customer_id", "product_id", "order_ts"]]
    unique_constraints: Mapped[List[List[str]]] = mapped_column(JSON, nullable=False, default=list)
    # Declarative rule objects (see services/data_validator.py) - never executable code.
    business_rules: Mapped[List[dict]] = mapped_column(JSON, nullable=False, default=list)
    change_notes: Mapped[Optional[str]] = mapped_column(Text)
    created_by: Mapped[Optional[str]] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow, nullable=False)

    contract: Mapped[DataContract] = relationship(back_populates="versions")
    columns: Mapped[List["ContractColumn"]] = relationship(
        back_populates="version", order_by="ContractColumn.ordinal", cascade="all, delete-orphan"
    )
    foreign_keys: Mapped[List["ContractForeignKey"]] = relationship(
        back_populates="version", cascade="all, delete-orphan",
        foreign_keys="ContractForeignKey.version_id",
    )

    @property
    def primary_key(self) -> List[str]:
        return [c.name for c in self.columns if c.is_primary_key]


class ContractColumn(Base):
    __tablename__ = "contract_columns"
    __table_args__ = (UniqueConstraint("version_id", "name", name="uq_version_column"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("contract_versions.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    data_type: Mapped[str] = mapped_column(String(20), nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    nullable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_primary_key: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    allowed_values: Mapped[Optional[List[Any]]] = mapped_column(JSON)
    default_value: Mapped[Optional[Any]] = mapped_column(JSON)
    min_value: Mapped[Optional[float]] = mapped_column(Float)
    max_value: Mapped[Optional[float]] = mapped_column(Float)
    max_length: Mapped[Optional[int]] = mapped_column(Integer)
    pattern: Mapped[Optional[str]] = mapped_column(String(255))
    normalize: Mapped[Optional[str]] = mapped_column(String(10))       # 'lower' | 'upper'
    renamed_from: Mapped[Optional[str]] = mapped_column(String(100))   # compatibility mapping
    description: Mapped[Optional[str]] = mapped_column(String(255))

    version: Mapped[ContractVersion] = relationship(back_populates="columns")


class ContractForeignKey(Base):
    __tablename__ = "contract_foreign_keys"
    __table_args__ = (UniqueConstraint("version_id", "column_name", name="uq_version_fk_column"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("contract_versions.id", ondelete="CASCADE"), nullable=False)
    column_name: Mapped[str] = mapped_column(String(100), nullable=False)
    ref_contract_id: Mapped[int] = mapped_column(ForeignKey("data_contracts.id", ondelete="RESTRICT"), nullable=False)
    # Must be the referenced contract's (single-column) primary key.
    ref_column: Mapped[str] = mapped_column(String(100), nullable=False)

    version: Mapped[ContractVersion] = relationship(back_populates="foreign_keys", foreign_keys=[version_id])
    ref_contract: Mapped[DataContract] = relationship(foreign_keys=[ref_contract_id])
