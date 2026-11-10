"""Identifiers, hashing and time helpers.

Record keys are *deterministic*: the same contract + primary key always hashes to
the same key, which is what makes publishing idempotent.
"""
import hashlib
import json
import secrets
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping


def utcnow() -> datetime:
    """Naive UTC datetime (MySQL DATETIME has no time zone)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_run_id() -> str:
    return f"RUN-{utcnow():%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def idempotency_key(checksum: str, contract_version_id: int) -> str:
    return hashlib.sha256(f"{checksum}|{contract_version_id}".encode()).hexdigest()


def primary_key_value(values: Iterable[Any]) -> str:
    """Human-readable PK string, e.g. 'ORD-0001' or 'A|7' for composite keys."""
    return "|".join(str(v) for v in values)


def record_key(contract_id: int, pk_value: str) -> str:
    return hashlib.sha256(f"{contract_id}|{pk_value}".encode()).hexdigest()


def payload_hash(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode()
    ).hexdigest()
