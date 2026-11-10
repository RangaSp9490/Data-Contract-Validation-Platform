"""File intake and parsing.

Two-step design:
  1. store_upload()  - runs inside the HTTP request: checks extension/size, computes the
                       SHA-256 checksum, stores the original bytes (content-addressed) and
                       registers a source_files row. Bad requests fail fast with 4xx.
  2. parse_file()    - runs inside the pipeline: turns bytes into a ParsedBatch. Problems
                       found here (bad encoding, malformed JSON, empty file) fail the *run*
                       with a clear reason, so they show up in pipeline metrics.

Every value is read as text (no type guessing by pandas) so that type validation is done
explicitly by the contract, and the original record is preserved exactly.
"""
import csv
import io
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.config import get_settings
from backend.models import SourceFile
from backend.services.errors import BadRequestError, PayloadTooLargeError
from backend.utils.id_generator import sha256_bytes

log = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {".csv": "csv", ".json": "json"}
NULL_TOKENS = {"", "null", "none", "nan"}            # compared case-insensitively after strip
MALFORMED = "__malformed__"                          # internal marker column


class FileParseError(Exception):
    """The file cannot be read as a batch at all (the run fails)."""


@dataclass
class ParsedBatch:
    original: pd.DataFrame          # exact values as received (str / JSON scalars), for quarantine
    raw: pd.DataFrame               # stripped strings, NULL tokens -> None; used for processing
    malformed: pd.Series            # bool per row: wrong field count etc.
    malformed_detail: Dict[int, str] = field(default_factory=dict)
    malformed_raw: Dict[int, Any] = field(default_factory=dict)   # exact unparseable input

    @property
    def row_count(self) -> int:
        return len(self.raw)

    @property
    def columns(self) -> List[str]:
        return list(self.raw.columns)


# ------------------------------------------------------------------ upload
def detect_format(filename: str) -> str:
    ext = Path(filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise BadRequestError(f"Unsupported file type '{ext or 'none'}'. Allowed: .csv, .json")
    return ALLOWED_EXTENSIONS[ext]


def read_limited(stream, max_bytes: int) -> bytes:
    """Read an upload stream, refusing anything larger than max_bytes."""
    chunks, total = [], 0
    while True:
        chunk = stream.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise PayloadTooLargeError(f"File exceeds the {max_bytes // (1024 * 1024)} MB upload limit")
        chunks.append(chunk)
    data = b"".join(chunks)
    if not data:
        raise BadRequestError("Uploaded file is empty (0 bytes)")
    return data


def store_upload(db: Session, filename: str, data: bytes) -> SourceFile:
    """Store original bytes once per checksum; return the (possibly existing) source_files row."""
    file_format = detect_format(filename)
    checksum = sha256_bytes(data)
    existing = db.scalar(select(SourceFile).where(SourceFile.checksum_sha256 == checksum))
    if existing is not None:
        return existing

    upload_dir = get_settings().upload_path
    upload_dir.mkdir(parents=True, exist_ok=True)
    stored = upload_dir / f"{checksum}.{file_format}"
    if not stored.exists():
        stored.write_bytes(data)
    source = SourceFile(file_name=Path(filename).name[:255], stored_path=str(stored), checksum_sha256=checksum,
                        file_format=file_format, file_size_bytes=len(data))
    db.add(source)
    try:
        db.commit()
    except IntegrityError:          # same file uploaded concurrently
        db.rollback()
        source = db.scalar(select(SourceFile).where(SourceFile.checksum_sha256 == checksum))
    log.info("source file stored", extra={"checksum": checksum, "file_name": filename, "bytes": len(data)})
    return source


# ------------------------------------------------------------------ parsing
def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise FileParseError(f"File is not valid UTF-8 text (byte offset {exc.start})") from exc


def _normalize_raw(original: pd.DataFrame) -> pd.DataFrame:
    """Strip text and turn NULL tokens into None (vectorised per column)."""
    raw = pd.DataFrame(index=original.index)
    for col in original.columns:
        s = original[col]
        s = s.where(s.notna(), None)
        text = s.map(_scalar_to_text, na_action="ignore").str.strip()
        is_null = text.isna() | text.str.lower().isin(NULL_TOKENS)
        raw[col] = text.where(~is_null, None)
    return raw


def _scalar_to_text(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True)
    return str(value)


def parse_csv(data: bytes) -> ParsedBatch:
    text = _decode(data)
    try:
        rows = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except csv.Error as exc:
        raise FileParseError(f"Malformed CSV: {exc}") from exc
    rows = [r for r in rows if any(cell.strip() for cell in r)]       # ignore blank lines
    if not rows:
        raise FileParseError("File contains no header and no records")
    header = [h.strip() for h in rows[0]]
    if any(not h for h in header):
        raise FileParseError("CSV header contains an empty column name")
    dupes = sorted({h for h in header if header.count(h) > 1})
    if dupes:
        raise FileParseError(f"CSV header contains duplicate column names: {dupes}")
    body = rows[1:]
    if not body:
        raise FileParseError("File contains a header but no records (empty batch)")

    width = len(header)
    malformed_detail: Dict[int, str] = {}
    fixed: List[List[Any]] = []
    for i, r in enumerate(body):
        if len(r) != width:
            malformed_detail[i] = f"expected {width} fields, found {len(r)}"
            fixed.append([None] * width)
        else:
            fixed.append(r)
    original = pd.DataFrame(fixed, columns=header, dtype=object)
    malformed = pd.Series(False, index=original.index)
    malformed.iloc[list(malformed_detail)] = True
    return ParsedBatch(original=original, raw=_normalize_raw(original), malformed=malformed,
                        malformed_detail=malformed_detail,
                       malformed_raw={i: body[i] for i in malformed_detail})


def parse_json(data: bytes) -> ParsedBatch:
    text = _decode(data)
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        raise FileParseError(f"Malformed JSON at line {exc.lineno} column {exc.colno}: {exc.msg}") from exc
    records = doc.get("records") if isinstance(doc, dict) else doc
    if not isinstance(records, list):
        raise FileParseError('JSON must be an array of objects or an object with a "records" array')
    if not records:
        raise FileParseError("File contains no records (empty batch)")

    malformed_detail: Dict[int, str] = {}
    clean: List[Dict[str, Any]] = []
    for i, rec in enumerate(records):
        if isinstance(rec, dict):
            clean.append({str(k).strip(): v for k, v in rec.items()})
        else:
            malformed_detail[i] = f"record is a {type(rec).__name__}, expected an object"
            clean.append({})
    original = pd.DataFrame.from_records(clean).astype(object)
    if original.shape[1] == 0:
        raise FileParseError("JSON records contain no fields")
    original = original.where(original.notna(), None)
    malformed = pd.Series(False, index=original.index)
    malformed.iloc[list(malformed_detail)] = True
    return ParsedBatch(original=original, raw=_normalize_raw(original), malformed=malformed,
                        malformed_detail=malformed_detail,
                       malformed_raw={i: records[i] for i in malformed_detail})


def parse_file(path: str, file_format: str) -> ParsedBatch:
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        raise FileParseError(f"Stored source file is not readable: {Path(path).name}") from exc
    return parse_csv(data) if file_format == "csv" else parse_json(data)
