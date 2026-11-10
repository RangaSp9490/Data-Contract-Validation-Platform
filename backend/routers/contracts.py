import json
from typing import List, Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from backend.config import PROJECT_ROOT
from backend.database import get_db
from backend.schemas.contract_schema import (CompatibilityReport, ContractCreate,
                                             ContractDetail, ContractOut,
                                             VersionCreate, VersionCreated,
                                             VersionOut, VersionSpec)
from backend.services import contract_service

router = APIRouter(prefix="/api/contracts", tags=["contracts"])


@router.get("", response_model=List[ContractOut])
def list_contracts(db: Session = Depends(get_db)):
    return contract_service.list_contracts(db)


@router.post("", response_model=ContractDetail, status_code=status.HTTP_201_CREATED)
def create_contract(payload: ContractCreate, db: Session = Depends(get_db)):
    """Create a contract together with its version 1."""
    return contract_service.create_contract(db, payload)


@router.get("/templates")
def contract_templates():
    """Sample contract definitions (data/samples/contracts/*.json) used to prefill the UI editor."""
    folder = PROJECT_ROOT / "data" / "samples" / "contracts"
    return {p.stem.replace("_contract", ""): json.loads(p.read_text(encoding="utf-8"))
            for p in sorted(folder.glob("*_contract.json"))}


@router.get("/{contract_id}", response_model=ContractDetail)
def get_contract(contract_id: int, version: Optional[int] = Query(None, ge=1), db: Session = Depends(get_db)):
    """Contract metadata plus the requested version (latest by default)."""
    return contract_service.contract_detail(db, contract_id, version)


@router.get("/{contract_id}/delete-preview")
def delete_preview(contract_id: int, db: Session = Depends(get_db)):
    """Counts of everything a permanent delete would remove (used by the UI warning)."""
    return contract_service.delete_preview(db, contract_id)


@router.delete("/{contract_id}")
def delete_contract(contract_id: int, db: Session = Depends(get_db)):
    """PERMANENTLY delete the contract with all versions, runs, published and rejected records.
    409 only if another contract references it through a foreign key (delete that one first)."""
    return contract_service.delete_contract(db, contract_id)


@router.get("/{contract_id}/versions/{version}", response_model=VersionOut)
def get_version(contract_id: int, version: int, db: Session = Depends(get_db)):
    return contract_service.version_out(db, contract_service.get_version(db, contract_id, version))


@router.post("/{contract_id}/compatibility-check", response_model=CompatibilityReport)
def compatibility_check(contract_id: int, spec: VersionSpec, db: Session = Depends(get_db)):
    """Dry run: classify the changes a proposed version would introduce. Nothing is saved."""
    return contract_service.check_compatibility(db, contract_id, spec)[2]


@router.post("/{contract_id}/versions", response_model=VersionCreated, status_code=status.HTTP_201_CREATED)
def create_version(contract_id: int, payload: VersionCreate, db: Session = Depends(get_db)):
    """Create version N+1. Breaking changes return 409 unless acknowledge_breaking=true."""
    version, report = contract_service.create_version(db, contract_id, payload)
    return VersionCreated(version=version, compatibility=report)
