"""Deterministic transformations: raw text -> typed, contract-shaped columns.

Rules (documented in DESIGN_DECISIONS.md):
  * Strings are trimmed; NULL tokens ("", null, none, nan) become NULL (done at parse time).
  * Case normalisation ('lower'/'upper') only where the contract column declares `normalize`.
  * Numbers are parsed with pandas.to_numeric; integers must have no fractional part.
  * Booleans accept true/false/1/0/yes/no/y/n/t/f (case-insensitive).
  * Timestamps: ISO-8601 first, then the explicit fallback formats below (slash dates are
    DAY-first). Values without an offset are interpreted as UTC. Output is ISO-8601 UTC 'Z'.
  * Declared renames (`renamed_from`) are mapped to the contract name.
  * Documented defaults fill columns that are absent and NULL values.

A value that cannot be converted is NOT fixed: it is flagged in `invalid[col]` and the
record validator turns that into an INVALID_TYPE / INVALID_TIMESTAMP rejection.
The original record is never modified (it lives in ParsedBatch.original).
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from backend.services.ingestion_service import ParsedBatch

TRUE_TOKENS = {"true", "1", "yes", "y", "t"}
FALSE_TOKENS = {"false", "0", "no", "n", "f"}
FALLBACK_TS_FORMATS = ["%Y/%m/%d %H:%M:%S", "%Y/%m/%d", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y"]
NUMERIC_TYPES = ("integer", "float", "decimal")
TIME_TYPES = ("timestamp", "date")


@dataclass
class TransformResult:
    data: pd.DataFrame                     # typed values, one column per contract column
    invalid: Dict[str, pd.Series]          # raw value present but not convertible to the contract type
    present_columns: List[str]             # contract columns that were present in the batch
    summary: Dict[str, Any] = field(default_factory=dict)

    def serialized(self, columns: List[Any]) -> pd.DataFrame:
        """JSON-friendly representation (ISO strings for time values) - used for keys and payloads."""
        out = pd.DataFrame(index=self.data.index)
        for col in columns:
            out[col.name] = serialize_series(self.data[col.name], col.data_type)
        return out


# ------------------------------------------------------------------ casting
def cast_series(raw: pd.Series, data_type: str) -> Tuple[pd.Series, pd.Series]:
    """Return (typed values, invalid mask). `raw` holds stripped strings or None."""
    present = raw.notna()
    if data_type == "string":
        return raw.astype(object), pd.Series(False, index=raw.index)
    if data_type in NUMERIC_TYPES:
        num = pd.to_numeric(raw, errors="coerce")
        ok = num.notna() & np.isfinite(num.astype(float).fillna(0))
        if data_type == "integer":
            ok &= (num.fillna(0) % 1 == 0)
            typed = num.where(ok).astype("Float64").astype("Int64")
        else:
            typed = num.where(ok).astype("Float64")
        return typed, present & ~ok
    if data_type == "boolean":
        low = raw.str.lower()
        typed = pd.Series(pd.NA, index=raw.index, dtype="boolean")
        typed[low.isin(TRUE_TOKENS)] = True
        typed[low.isin(FALSE_TOKENS)] = False
        return typed, present & typed.isna()
    if data_type in TIME_TYPES:
        typed = parse_timestamps(raw)
        if data_type == "date":
            typed = typed.dt.normalize()
        return typed, present & typed.isna()
    raise ValueError(f"unsupported data type {data_type}")


def parse_timestamps(raw: pd.Series) -> pd.Series:
    ts = pd.to_datetime(raw, errors="coerce", utc=True, format="ISO8601")
    for fmt in FALLBACK_TS_FORMATS:
        todo = raw.notna() & ts.isna()
        if not todo.any():
            break
        ts.loc[todo] = pd.to_datetime(raw[todo], errors="coerce", utc=True, format=fmt)
    return ts


def serialize_series(s: pd.Series, data_type: str) -> pd.Series:
    if data_type == "timestamp":
        base = s.dt.strftime("%Y-%m-%dT%H:%M:%S")
        ms = (s.dt.microsecond // 1000).fillna(0).astype(int)
        frac = pd.Series(np.where(ms > 0, "." + ms.astype(str).str.zfill(3), ""), index=s.index)
        return (base + frac + "Z").where(s.notna(), None).astype(object)
    if data_type == "date":
        return s.dt.strftime("%Y-%m-%d").where(s.notna(), None).astype(object)
    if data_type == "integer":
        return pd.Series([None if pd.isna(v) else int(v) for v in s], index=s.index, dtype=object)
    if data_type in ("float", "decimal"):
        return pd.Series([None if pd.isna(v) else float(v) for v in s], index=s.index, dtype=object)
    if data_type == "boolean":
        return pd.Series([None if pd.isna(v) else bool(v) for v in s], index=s.index, dtype=object)
    return s.where(s.notna(), None).astype(object)


def _typed_constant(value: Any, data_type: str, n: int, index) -> pd.Series:
    raw = pd.Series([None if value is None else str(value)] * n, index=index, dtype=object)
    if value is not None and data_type == "boolean":
        raw[:] = "true" if value is True or str(value).lower() in TRUE_TOKENS else "false"
    return cast_series(raw, data_type)[0]


# ------------------------------------------------------------------ main entry
def transform(batch: ParsedBatch, columns: List[Any], rename_map: Optional[Dict[str, str]] = None) -> TransformResult:
    """Build the typed, contract-shaped frame. `rename_map` maps batch name -> contract name."""
    rename_map = rename_map or {}
    raw = batch.raw.rename(columns=rename_map)
    n, index = len(raw), raw.index
    data = pd.DataFrame(index=index)
    invalid: Dict[str, pd.Series] = {}
    present: List[str] = []
    summary: Dict[str, Any] = {"columns_renamed": dict(rename_map), "defaults_applied": {},
                               "values_case_normalized": {}, "timestamps_standardized": {}}

    for col in columns:
        if col.name in raw.columns:
            present.append(col.name)
            s = raw[col.name]
            if col.data_type == "string" and col.normalize:
                normalized = s.str.lower() if col.normalize == "lower" else s.str.upper()
                changed = int((s.notna() & (normalized != s)).sum())
                if changed:
                    summary["values_case_normalized"][col.name] = changed
                s = normalized
            typed, bad = cast_series(s, col.data_type)
            if col.data_type in TIME_TYPES:
                already_iso_utc = s.str.endswith("Z", na=False).astype(bool)
                converted = int((typed.notna() & ~already_iso_utc).sum())
                if converted:
                    summary["timestamps_standardized"][col.name] = converted
        else:
            typed = _typed_constant(None, col.data_type, n, index)
            bad = pd.Series(False, index=index)

        if col.default_value is not None:
            fill = typed.isna() & ~bad & ~batch.malformed
            if fill.any():
                typed = typed.copy()
                typed[fill] = _typed_constant(col.default_value, col.data_type, int(fill.sum()), index[fill])
                summary["defaults_applied"][col.name] = int(fill.sum())
        data[col.name] = typed
        invalid[col.name] = bad
    return TransformResult(data=data, invalid=invalid, present_columns=present, summary=summary)
