"""Batch schema validation: observed batch schema vs. the selected contract version.

Step 1  compare_columns()       structural: missing / unexpected / renamed columns
Step 2  detect_value_drift()    column-level drift after type casting: type mismatch,
                                nullability, allowed-value drift

Column-level vs record-level: a few bad values in a column are a RECORD problem
(those rows are quarantined). Only when the *column as a whole* no longer matches the
contract is it a SCHEMA change:
  TYPE_MISMATCH          more than TYPE_MISMATCH_RATIO of non-null values fail to cast
  NULLABILITY_VIOLATION  a non-nullable column is NULL in every row

Change types:
  MISSING_REQUIRED_COLUMN    BREAKING      (PRIMARY_KEY_COLUMN_MISSING when it is a key column)
  MISSING_OPTIONAL_COLUMN    NON_BREAKING  default applied / NULL
  UNEXPECTED_COLUMN          BACKWARD: NON_BREAKING (column ignored)  STRICT: BREAKING
  COLUMN_RENAME_MAPPED       NON_BREAKING  old name accepted via renamed_from
  RENAME_CANDIDATE           BREAKING if the contract column is required, else by policy
  TYPE_MISMATCH              BREAKING
  NULLABILITY_VIOLATION      BREAKING
  NULL_VALUES_IN_NON_NULLABLE NON_BREAKING (affected rows quarantined)
  ALLOWED_VALUES_DRIFT       BACKWARD: NON_BREAKING (rows quarantined)  STRICT: BREAKING
"""
from typing import Any, Dict, List, Tuple

import pandas as pd

from backend.services.compatibility_service import (BREAKING, CRITICAL, INFO,
                                                    NON_BREAKING, WARNING,
                                                    Change, find_rename_pairs,
                                                    policy)
from backend.services.ingestion_service import ParsedBatch
from backend.services.transformation_service import (TransformResult,
                                                     cast_series)

TYPE_MISMATCH_RATIO = 0.5
MAX_REPORTED_VALUES = 10


def column_spec(col: Any) -> Dict[str, Any]:
    return {"name": col.name, "data_type": col.data_type, "required": col.required, "nullable": col.nullable,
            "is_primary_key": col.is_primary_key, "allowed_values": col.allowed_values,
            "default_value": col.default_value}


def infer_type(raw: pd.Series) -> str:
    """Best-effort observed type of a raw text column (reporting only)."""
    values = raw.dropna()
    if values.empty:
        return "null"
    for candidate in ("integer", "float", "boolean", "timestamp"):
        if not cast_series(values, candidate)[1].any():
            return candidate
    return "string"


def compare_columns(batch: ParsedBatch, columns: List[Any], mode: str) -> Tuple[List[Change], Dict[str, str]]:
    """Return (changes, rename_map batch_name -> contract_name)."""
    batch_cols = set(batch.columns)
    contract = {c.name: c for c in columns}
    changes: List[Change] = []
    rename_map: Dict[str, str] = {}

    missing = {n: c for n, c in contract.items() if n not in batch_cols}
    unexpected = {n for n in batch_cols if n not in contract}

    # Declared renames.
    for name, col in list(missing.items()):
        if col.renamed_from and col.renamed_from in unexpected:
            rename_map[col.renamed_from] = name
            changes.append(Change("COLUMN_RENAME_MAPPED", name, NON_BREAKING, INFO,
                                  f"Batch column '{col.renamed_from}' mapped to contract column '{name}' "
                                  "through the declared renamed_from compatibility mapping.",
                                  column_spec(col), {"name": col.renamed_from}))
            del missing[name]
            unexpected.discard(col.renamed_from)

    # Undeclared rename candidates (similar name, castable values).
    observed = {n: {"data_type": None} for n in unexpected}
    for old_name, new_name, score in find_rename_pairs(
            {n: {"data_type": c.data_type} for n, c in missing.items()}, observed):
        col = missing[old_name]
        castable = not cast_series(batch.raw[new_name].dropna(), col.data_type)[1].any()
        if not castable:
            continue
        cls = BREAKING if col.required else policy(mode, backward=NON_BREAKING, strict=BREAKING)
        changes.append(Change(
            "RENAME_CANDIDATE", old_name, cls, CRITICAL if cls == BREAKING else WARNING,
            f"Contract column '{old_name}' is missing and batch column '{new_name}' looks like a rename "
            f"(name similarity {score}). No renamed_from mapping is declared, so the column is treated as "
            f"missing. If this rename is intended, publish a new contract version with renamed_from='{new_name}'.",
            column_spec(col), {"name": new_name, "inferred_type": infer_type(batch.raw[new_name])}))
        del missing[old_name]
        unexpected.discard(new_name)

    for name, col in missing.items():
        if col.required:
            ctype = "PRIMARY_KEY_COLUMN_MISSING" if col.is_primary_key else "MISSING_REQUIRED_COLUMN"
            changes.append(Change(ctype, name, BREAKING, CRITICAL,
                                  f"Required column '{name}' is missing from the batch. Consumers depend on it; "
                                  "the batch cannot be published.", column_spec(col), None))
        else:
            fill = f"default {col.default_value!r} applied" if col.default_value is not None else "values set to NULL"
            changes.append(Change("MISSING_OPTIONAL_COLUMN", name, NON_BREAKING, INFO,
                                  f"Optional column '{name}' is absent; {fill}.", column_spec(col), None))

    for name in sorted(unexpected):
        cls = policy(mode, backward=NON_BREAKING, strict=BREAKING)
        changes.append(Change(
            "UNEXPECTED_COLUMN", name, cls, WARNING if cls == NON_BREAKING else CRITICAL,
            f"Column '{name}' is not in the contract. "
            + ("Under STRICT policy unexpected columns are breaking."
               if cls == BREAKING else "It is ignored (not published) but preserved in the original file."),
            None, {"name": name, "inferred_type": infer_type(batch.raw[name])}))
    return changes, rename_map


def detect_value_drift(batch: ParsedBatch, tr: TransformResult, columns: List[Any], mode: str) -> List[Change]:
    changes: List[Change] = []
    usable = ~batch.malformed
    rows = int(usable.sum())
    if rows == 0:
        return changes
    for col in columns:
        if col.name not in tr.present_columns:
            continue
        typed = tr.data[col.name][usable]
        bad = tr.invalid[col.name][usable]
        nulls = typed.isna() & ~bad
        non_null = rows - int(nulls.sum())
        observed = {"rows": rows, "null_count": int(nulls.sum()), "invalid_count": int(bad.sum())}

        if non_null and bad.sum() / non_null > TYPE_MISMATCH_RATIO:
            src = next((k for k, v in tr.summary["columns_renamed"].items() if v == col.name), col.name)
            observed["inferred_type"] = infer_type(batch.raw[src][usable])
            changes.append(Change(
                "TYPE_MISMATCH", col.name, BREAKING, CRITICAL,
                f"Column '{col.name}' is declared {col.data_type} but {int(bad.sum())} of {non_null} non-null "
                f"values cannot be converted (observed type: {observed['inferred_type']}). This is a column-level "
                "type change, not isolated bad records.", column_spec(col), observed))
            continue

        if not col.nullable and nulls.any():
            if int(nulls.sum()) == rows:
                changes.append(Change(
                    "NULLABILITY_VIOLATION", col.name, BREAKING, CRITICAL,
                    f"Non-nullable column '{col.name}' is NULL in every row: the producer appears to have "
                    "stopped populating it.", column_spec(col), observed))
            else:
                changes.append(Change(
                    "NULL_VALUES_IN_NON_NULLABLE", col.name, NON_BREAKING, WARNING,
                    f"Non-nullable column '{col.name}' is NULL in {int(nulls.sum())} of {rows} rows; those rows "
                    "are quarantined.", column_spec(col), observed))

        if col.allowed_values:
            allowed = set(col.allowed_values) if col.data_type == "string" else {
                v for v in cast_series(pd.Series([str(a) for a in col.allowed_values], dtype=object),
                                       col.data_type)[0].dropna()}
            outside = typed[typed.notna() & ~bad & ~typed.isin(allowed)]
            if not outside.empty:
                new_values = sorted({str(v) for v in outside})[:MAX_REPORTED_VALUES]
                cls = policy(mode, backward=NON_BREAKING, strict=BREAKING)
                changes.append(Change(
                    "ALLOWED_VALUES_DRIFT", col.name, cls, WARNING if cls == NON_BREAKING else CRITICAL,
                    f"Column '{col.name}' contains {len(outside)} value(s) outside the allowed set "
                    f"{col.allowed_values}: {new_values}. "
                    + ("Under STRICT policy this blocks the batch." if cls == BREAKING
                       else "Affected rows are quarantined; if these values are legitimate, add them in a new "
                            "contract version."),
                    column_spec(col), {**observed, "unexpected_values": new_values}))
    return changes
