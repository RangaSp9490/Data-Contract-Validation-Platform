"""Contract management: create contracts, add immutable versions, read them back."""
import logging
from typing import List, Optional

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from backend.models import (ContractColumn, ContractForeignKey, ContractVersion,
                            DataContract, SchemaChange)
from backend.schemas.contract_schema import (ChangeOut, CompatibilityReport,
                                             ContractCreate, ContractDetail,
                                             ContractOut, ForeignKeyOut,
                                             VersionCreate, VersionOut,
                                             VersionSpec)
from backend.services import compatibility_service as compat
from backend.services.errors import BadRequestError, ConflictError, NotFoundError

log = logging.getLogger(__name__)

_VERSION_LOAD = (
    selectinload(ContractVersion.columns),
    selectinload(ContractVersion.foreign_keys).selectinload(ContractForeignKey.ref_contract),
)


# ------------------------------------------------------------------ reads
def get_contract(db: Session, contract_id: int) -> DataContract:
    contract = db.get(DataContract, contract_id)
    if contract is None:
        raise NotFoundError(f"Contract {contract_id} not found")
    return contract


def get_contract_by_dataset(db: Session, dataset_name: str) -> DataContract:
    contract = db.scalar(select(DataContract).where(DataContract.dataset_name == dataset_name))
    if contract is None:
        raise NotFoundError(f"No contract for dataset '{dataset_name}'")
    return contract


def get_version(db: Session, contract_id: int, version: Optional[int] = None) -> ContractVersion:
    """A specific version, or the latest one when `version` is None."""
    stmt = select(ContractVersion).where(ContractVersion.contract_id == contract_id).options(*_VERSION_LOAD)
    if version is None:
        stmt = stmt.order_by(ContractVersion.version.desc()).limit(1)
    else:
        stmt = stmt.where(ContractVersion.version == version)
    found = db.scalar(stmt)
    if found is None:
        get_contract(db, contract_id)  # raises 404 for unknown contract
        raise NotFoundError(f"Contract {contract_id} has no version {version}")
    return found


def get_version_by_id(db: Session, version_id: int) -> ContractVersion:
    found = db.scalar(select(ContractVersion).where(ContractVersion.id == version_id).options(*_VERSION_LOAD))
    if found is None:
        raise NotFoundError(f"Contract version id {version_id} not found")
    return found


def list_contracts(db: Session) -> List[ContractOut]:
    contracts = db.scalars(
        select(DataContract).options(selectinload(DataContract.versions)).order_by(DataContract.id)
    ).all()
    return [contract_out(c) for c in contracts]


def contract_out(contract: DataContract) -> ContractOut:
    nums = [v.version for v in contract.versions]
    return ContractOut(
        id=contract.id, name=contract.name, dataset_name=contract.dataset_name,
        description=contract.description, owner=contract.owner,
        created_at=contract.created_at, updated_at=contract.updated_at,
        latest_version=max(nums) if nums else None, versions=sorted(nums),
    )


def version_out(db: Session, version: ContractVersion, with_changes: bool = True) -> VersionOut:
    changes = []
    if with_changes:
        rows = db.scalars(select(SchemaChange).where(
            SchemaChange.contract_version_id == version.id, SchemaChange.scope == "CONTRACT"
        ).order_by(SchemaChange.id)).all()
        changes = [ChangeOut.model_validate(r) for r in rows]
    return VersionOut(
        id=version.id, contract_id=version.contract_id, version=version.version,
        compatibility_mode=version.compatibility_mode, event_time_column=version.event_time_column,
        primary_key=version.primary_key, unique_constraints=version.unique_constraints or [],
        business_rules=version.business_rules or [], change_notes=version.change_notes,
        created_by=version.created_by, created_at=version.created_at,
        columns=version.columns,
        foreign_keys=[ForeignKeyOut(column=fk.column_name, ref_dataset=fk.ref_contract.dataset_name,
                                    ref_column=fk.ref_column) for fk in version.foreign_keys],
        schema_changes=changes,
    )


def contract_detail(db: Session, contract_id: int, version: Optional[int] = None) -> ContractDetail:
    contract = get_contract(db, contract_id)
    current = version_out(db, get_version(db, contract_id, version)) if contract.versions else None
    return ContractDetail(**contract_out(contract).model_dump(), current=current)


# ------------------------------------------------------------------ writes
def _resolve_foreign_keys(db: Session, spec: VersionSpec, own_dataset: str) -> List[ContractForeignKey]:
    fks = []
    for fk in spec.foreign_keys:
        if fk.ref_dataset == own_dataset:
            raise BadRequestError("Self-referencing foreign keys are not supported")
        try:
            ref = get_contract_by_dataset(db, fk.ref_dataset)
        except NotFoundError as exc:
            raise BadRequestError(f"Foreign key '{fk.column}': {exc.message}") from exc
        ref_pk = get_version(db, ref.id).primary_key
        if ref_pk != [fk.ref_column]:
            raise BadRequestError(
                f"Foreign key '{fk.column}' must reference the single-column primary key of "
                f"'{fk.ref_dataset}' ({ref_pk}), got '{fk.ref_column}'")
        fks.append(ContractForeignKey(column_name=fk.column, ref_contract_id=ref.id, ref_column=fk.ref_column))
    return fks


def _build_version(db: Session, contract_id: int, number: int, spec: VersionSpec, dataset: str) -> ContractVersion:
    version = ContractVersion(
        contract_id=contract_id, version=number, compatibility_mode=spec.compatibility_mode,
        event_time_column=spec.event_time_column, unique_constraints=spec.unique_constraints,
        business_rules=[r.model_dump() for r in spec.business_rules],
        change_notes=spec.change_notes, created_by=spec.created_by,
    )
    version.columns = [
        ContractColumn(ordinal=i, **c.model_dump()) for i, c in enumerate(spec.columns, start=1)
    ]
    version.foreign_keys = _resolve_foreign_keys(db, spec, dataset)
    return version


def create_contract(db: Session, payload: ContractCreate) -> ContractDetail:
    if db.scalar(select(DataContract.id).where(DataContract.dataset_name == payload.dataset_name)):
        raise ConflictError(f"A contract for dataset '{payload.dataset_name}' already exists; "
                            "create a new version instead")
    contract = DataContract(name=payload.name, dataset_name=payload.dataset_name,
                            description=payload.description, owner=payload.owner)
    db.add(contract)
    db.flush()
    db.add(_build_version(db, contract.id, 1, payload, payload.dataset_name))
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError("Contract could not be created due to a conflicting concurrent change") from exc
    log.info("contract created", extra={"contract_id": contract.id, "dataset": contract.dataset_name})
    db.refresh(contract)
    return contract_detail(db, contract.id)


def _referencing_datasets(db: Session, contract_id: int) -> List[str]:
    """Other contracts whose foreign keys point at this contract."""
    return sorted(set(db.scalars(
        select(DataContract.dataset_name)
        .join(ContractVersion, ContractVersion.contract_id == DataContract.id)
        .join(ContractForeignKey, ContractForeignKey.version_id == ContractVersion.id)
        .where(ContractForeignKey.ref_contract_id == contract_id, DataContract.id != contract_id)).all()))


def delete_preview(db: Session, contract_id: int) -> dict:
    """What a permanent delete would remove (shown in the UI warning before confirming)."""
    from backend.models import PipelineRun, RejectedRecord, ValidRecord  # local import avoids a cycle

    contract = get_contract(db, contract_id)
    run_ids = select(PipelineRun.id).where(PipelineRun.contract_id == contract_id)
    count = lambda stmt: db.scalar(select(func.count()).select_from(stmt.subquery()))  # noqa: E731
    referencing = _referencing_datasets(db, contract_id)
    return {
        "contract_id": contract_id, "name": contract.name, "dataset_name": contract.dataset_name,
        "versions": count(select(ContractVersion.id).where(ContractVersion.contract_id == contract_id)),
        "pipeline_runs": count(run_ids),
        "published_records": count(select(ValidRecord.id).where(ValidRecord.contract_id == contract_id)),
        "rejected_records": count(select(RejectedRecord.id).where(RejectedRecord.run_id.in_(run_ids))),
        "referenced_by": referencing,
        "can_delete": not referencing,
    }


def delete_contract(db: Session, contract_id: int) -> dict:
    """PERMANENTLY delete a contract and everything that belongs to it, in one transaction:
    published records, pipeline runs (with their rejected records, rejection errors and batch
    schema changes), all versions (columns, foreign keys, contract-level schema changes) and
    uploaded source files no longer used by any other run.

    Refused (409) only when another contract references this one through a foreign key,
    because deleting the parent would break that contract's referential-integrity check.
    """
    from pathlib import Path

    from sqlalchemy import delete as sql_delete

    from backend.models import (PipelineRun, RejectedRecord, SourceFile,
                                ValidRecord)

    preview = delete_preview(db, contract_id)
    if not preview["can_delete"]:
        raise ConflictError(
            f"Contract '{preview['dataset_name']}' cannot be deleted because contract(s) "
            f"{preview['referenced_by']} reference it through a foreign key. Delete "
            f"{' and '.join(preview['referenced_by'])} first.", details=preview)

    file_ids = set(db.scalars(select(PipelineRun.source_file_id).where(PipelineRun.contract_id == contract_id)))
    try:
        # children first; pipeline_runs -> rejected_records / rejection_errors / batch schema_changes
        # and contract_versions -> columns / foreign keys / schema_changes cascade in the database.
        db.execute(sql_delete(ValidRecord).where(ValidRecord.contract_id == contract_id))
        db.execute(sql_delete(PipelineRun).where(PipelineRun.contract_id == contract_id))
        db.execute(sql_delete(ContractVersion).where(ContractVersion.contract_id == contract_id))
        db.execute(sql_delete(DataContract).where(DataContract.id == contract_id))

        # source files that no other contract's run still uses
        still_used = set(db.scalars(select(PipelineRun.source_file_id).where(PipelineRun.source_file_id.in_(file_ids)))) \
            if file_ids else set()
        orphan_ids = file_ids - still_used
        orphan_paths = list(db.scalars(select(SourceFile.stored_path).where(SourceFile.id.in_(orphan_ids)))) \
            if orphan_ids else []
        if orphan_ids:
            db.execute(sql_delete(SourceFile).where(SourceFile.id.in_(orphan_ids)))
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.expire_all()

    removed_files = 0
    for p in orphan_paths:            # only after the DB commit succeeded
        try:
            Path(p).unlink(missing_ok=True)
            removed_files += 1
        except OSError:
            log.warning("could not remove stored file", extra={"path": p})
    # "name" is a reserved LogRecord attribute: passing it in `extra` raises KeyError.
    log_fields = {k: v for k, v in preview.items() if k != "name"}
    log.info("contract deleted permanently",
             extra={**log_fields, "contract_name": preview["name"], "files_removed": removed_files})
    return {"deleted": True, **{k: preview[k] for k in (
        "contract_id", "dataset_name", "versions", "pipeline_runs", "published_records", "rejected_records")},
        "source_files_removed": len(orphan_ids)}


def check_compatibility(db: Session, contract_id: int, spec: VersionSpec) -> (
        "tuple[ContractVersion, list[compat.Change], CompatibilityReport]"):
    """Dry run: compare the latest version with a proposed spec. Nothing is written."""
    latest = get_version(db, contract_id)
    old = compat.snapshot_from_columns(latest.columns, latest.compatibility_mode)
    new = compat.snapshot_from_columns(spec.columns, spec.compatibility_mode)
    changes = compat.compare_versions(old, new)
    report = CompatibilityReport(
        from_version=latest.version, **compat.summarize(changes),
        changes=[ChangeOut(**c.to_dict()) for c in changes],
    )
    return latest, changes, report


def create_version(db: Session, contract_id: int, payload: VersionCreate):
    """Create version N+1. Previous versions are never modified."""
    contract = get_contract(db, contract_id)
    latest, changes, report = check_compatibility(db, contract_id, payload)
    if not report.compatible and not payload.acknowledge_breaking:
        raise ConflictError(
            f"Proposed version contains {report.breaking_count} breaking change(s). Review them and "
            "resubmit with acknowledge_breaking=true to create the version anyway.",
            details=report.model_dump(),
        )
    version = _build_version(db, contract_id, latest.version + 1, payload, contract.dataset_name)
    db.add(version)
    db.flush()
    for c in changes:
        db.add(SchemaChange(scope="CONTRACT", run_id=None, contract_version_id=version.id, **c.to_dict()))
    contract.updated_at = version.created_at
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError("Another version was created concurrently; reload and retry") from exc
    log.info("contract version created", extra={
        "contract_id": contract_id, "version": version.version, "breaking": report.breaking_count})
    return version_out(db, get_version(db, contract_id, version.version)), report
