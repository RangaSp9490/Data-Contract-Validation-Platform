"""Record-level validation (deterministic Python + pandas; the LLM is never involved).

Each check is vectorised: it produces a boolean mask over all rows and appends one
error row per offending record. A record can collect any number of errors; it is
rejected if it has at least one.

Error codes
  MALFORMED_ROW            row could not be parsed (wrong field count / not an object)
  NULL_NOT_ALLOWED         NULL in a non-nullable column
  INVALID_TYPE             value cannot be converted to the declared type
  INVALID_TIMESTAMP        value cannot be parsed as a timestamp/date
  INVALID_ALLOWED_VALUE    value not in allowed_values
  VALUE_BELOW_MIN / VALUE_ABOVE_MAX   numeric boundary violated (e.g. negative quantity)
  VALUE_TOO_LONG           string longer than max_length
  PATTERN_MISMATCH         string does not match the declared regex
  DUPLICATE_PRIMARY_KEY    PK already seen earlier in this batch (first occurrence is kept)
  DUPLICATE_BUSINESS_EVENT unique business key already seen earlier in this batch
  FOREIGN_KEY_VIOLATION    referenced key not found in the published parent dataset
  BUSINESS_RULE_VIOLATION  declarative cross-field rule failed
  FUTURE_TIMESTAMP         timestamp later than now + tolerance
"""
import operator
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Set

import numpy as np
import pandas as pd

from backend.services.ingestion_service import ParsedBatch
from backend.services.transformation_service import (TIME_TYPES, TransformResult,
                                                     cast_series)

ERROR_CODES: Dict[str, str] = {
    "MALFORMED_ROW": "Row could not be parsed",
    "NULL_NOT_ALLOWED": "NULL value in a non-nullable column",
    "INVALID_TYPE": "Value does not match the declared data type",
    "INVALID_TIMESTAMP": "Value is not a valid timestamp/date",
    "INVALID_ALLOWED_VALUE": "Value is not one of the allowed values",
    "VALUE_BELOW_MIN": "Numeric value below the minimum",
    "VALUE_ABOVE_MAX": "Numeric value above the maximum",
    "VALUE_TOO_LONG": "Text longer than the maximum length",
    "PATTERN_MISMATCH": "Text does not match the required format",
    "DUPLICATE_PRIMARY_KEY": "Primary key duplicated within the batch",
    "DUPLICATE_BUSINESS_EVENT": "Business event duplicated within the batch",
    "FOREIGN_KEY_VIOLATION": "Referenced record does not exist",
    "BUSINESS_RULE_VIOLATION": "Business rule failed",
    "FUTURE_TIMESTAMP": "Timestamp is in the future",
}

OPS: Dict[str, Callable] = {">=": operator.ge, ">": operator.gt, "<=": operator.le,
                            "<": operator.lt, "==": operator.eq, "!=": operator.ne}

# fk_lookup(ref_contract_id, candidate_values) -> subset of values that exist (published)
FkLookup = Callable[[int, Set[str]], Set[str]]

ERROR_COLUMNS = ["row", "error_code", "column_name", "message", "observed_value"]


@dataclass
class ValidationOutcome:
    errors: pd.DataFrame          # one row per error, ERROR_COLUMNS
    valid_mask: pd.Series         # True = record passed every check

    @property
    def rejected_rows(self) -> np.ndarray:
        return np.sort(self.errors["row"].unique()) if not self.errors.empty else np.array([], dtype=int)


class _Collector:
    def __init__(self, index: pd.Index, raw: pd.DataFrame, malformed: pd.Series):
        self.index, self.raw, self.malformed = index, raw, malformed
        self.frames: List[pd.DataFrame] = []

    def add(self, mask: pd.Series, code: str, column: Optional[str], message: Any,
            observed: Optional[pd.Series] = None, include_malformed: bool = False) -> None:
        mask = mask.fillna(False).astype(bool)
        if not include_malformed:
            mask &= ~self.malformed
        if not mask.any():
            return
        idx = mask.index[mask.to_numpy()]
        if observed is None and column is not None and column in self.raw.columns:
            observed = self.raw[column]
        obs = observed.loc[idx].map(lambda v: None if v is None else str(v)[:255]).to_numpy() \
            if observed is not None else None
        msg = message.loc[idx].to_numpy() if isinstance(message, pd.Series) else message
        self.frames.append(pd.DataFrame({"row": idx.to_numpy(), "error_code": code, "column_name": column,
                                         "message": msg, "observed_value": obs}))

    def result(self) -> ValidationOutcome:
        errors = (pd.concat(self.frames, ignore_index=True) if self.frames
                  else pd.DataFrame(columns=ERROR_COLUMNS))
        errors = errors.sort_values(["row"], kind="stable").reset_index(drop=True)
        valid = pd.Series(True, index=self.index)
        valid[errors["row"].unique()] = False
        return ValidationOutcome(errors=errors, valid_mask=valid)


def _key_series(serial: pd.DataFrame, cols: List[str]) -> pd.Series:
    key = serial[cols[0]].astype(str)
    for c in cols[1:]:
        key = key + "|" + serial[c].astype(str)
    return key.where(serial[cols].notna().all(axis=1), None)


def _first_occurrence_message(key: pd.Series, label: str) -> pd.Series:
    firsts = key.dropna().drop_duplicates(keep="first")
    first_row = key.map(pd.Series(firsts.index + 1, index=firsts.values))
    return f"Duplicate {label} '" + key.astype(str) + "'; first occurrence at row " + \
        first_row.astype("Int64").astype(str) + " was kept"


def _allowed_typed(col: Any) -> set:
    if col.data_type == "string":
        return set(col.allowed_values)
    raw = pd.Series([str(v) for v in col.allowed_values], dtype=object)
    return set(cast_series(raw, col.data_type)[0].dropna())


def validate_records(batch: ParsedBatch, tr: TransformResult, version: Any,
                     fk_lookup: Optional[FkLookup] = None, now: Optional[pd.Timestamp] = None) -> ValidationOutcome:
    now = now if now is not None else pd.Timestamp.now(tz="UTC")
    raw = batch.raw.rename(columns=tr.summary.get("columns_renamed", {}))
    c = _Collector(raw.index, raw, batch.malformed)
    data, columns = tr.data, version.columns
    serial = tr.serialized(columns)

    # Malformed rows: report once, skip every other check for them.
    if batch.malformed.any():
        detail = pd.Series({i: f"Row could not be parsed: {d}" for i, d in batch.malformed_detail.items()})
        c.add(batch.malformed, "MALFORMED_ROW", None, detail.reindex(raw.index),
              observed=pd.Series({i: str(v) for i, v in batch.malformed_raw.items()}).reindex(raw.index),
              include_malformed=True)

    # ---- column checks
    for col in columns:
        typed, bad = data[col.name], tr.invalid[col.name]
        if bad.any():
            code = "INVALID_TIMESTAMP" if col.data_type in TIME_TYPES else "INVALID_TYPE"
            c.add(bad, code, col.name, f"'{col.name}' value cannot be converted to {col.data_type}")
        nulls = typed.isna() & ~bad
        if not col.nullable:
            c.add(nulls, "NULL_NOT_ALLOWED", col.name, f"'{col.name}' is required to have a value (non-nullable)")
        ok = typed.notna() & ~bad
        if not ok.any():
            continue
        if col.allowed_values:
            c.add(ok & ~typed.isin(_allowed_typed(col)), "INVALID_ALLOWED_VALUE", col.name,
                  f"'{col.name}' must be one of {col.allowed_values}")
        if col.data_type in ("integer", "float", "decimal"):
            num = typed.astype("Float64")
            if col.min_value is not None:
                c.add(ok & (num < col.min_value), "VALUE_BELOW_MIN", col.name,
                      f"'{col.name}' must be >= {col.min_value:g}")
            if col.max_value is not None:
                c.add(ok & (num > col.max_value), "VALUE_ABOVE_MAX", col.name,
                      f"'{col.name}' must be <= {col.max_value:g}")
        if col.data_type == "string":
            text = typed.where(ok, "").astype(str)
            if col.max_length:
                c.add(ok & (text.str.len() > col.max_length), "VALUE_TOO_LONG", col.name,
                      f"'{col.name}' must be at most {col.max_length} characters")
            if col.pattern:
                c.add(ok & ~text.str.fullmatch(col.pattern), "PATTERN_MISMATCH", col.name,
                      f"'{col.name}' must match pattern {col.pattern}")

    # ---- duplicates within the batch (first occurrence wins, later ones are rejected)
    eligible = ~batch.malformed
    pk = version.primary_key
    if pk:
        key = _key_series(serial, pk).where(eligible, None)
        dup = key.notna() & key.duplicated(keep="first")
        if dup.any():
            c.add(dup, "DUPLICATE_PRIMARY_KEY", ",".join(pk), _first_occurrence_message(key, "primary key"),
                  observed=key)
    for group in version.unique_constraints or []:
        key = _key_series(serial, group).where(eligible, None)
        dup = key.notna() & key.duplicated(keep="first")
        if dup.any():
            c.add(dup, "DUPLICATE_BUSINESS_EVENT", ",".join(group),
                  _first_occurrence_message(key, f"business event ({', '.join(group)})"), observed=key)

    # ---- referential integrity against published parent data
    if fk_lookup is not None:
        for fk in version.foreign_keys:
            values = serial[fk.column_name]
            present = values.notna() & eligible
            if not present.any():
                continue
            existing = fk_lookup(fk.ref_contract_id, set(values[present].astype(str)))
            missing = present & ~values.astype(str).isin(existing)
            ref = getattr(fk.ref_contract, "dataset_name", fk.ref_contract_id)
            c.add(missing, "FOREIGN_KEY_VIOLATION", fk.column_name,
                  f"'{fk.column_name}' has no matching published record in {ref}.{fk.ref_column}")

    # ---- declarative business rules
    by_name = {col.name: col for col in columns}
    for rule in version.business_rules or []:
        kind, name = rule["type"], rule["name"]
        if kind == "compare_columns":
            left, right = data[rule["left"]], data[rule["right"]]
            both = left.notna() & right.notna() & ~tr.invalid[rule["left"]] & ~tr.invalid[rule["right"]]
            if both.any():
                # Compare only where both sides are valid; build a plain numpy bool mask so
                # nullable dtypes can never leak into the boolean logic.
                passed = np.ones(len(raw.index), dtype=bool)
                passed[both.to_numpy()] = np.asarray(
                    OPS[rule["op"]](left[both], right[both]).to_numpy(), dtype=bool)
                passed = pd.Series(passed, index=raw.index)
                c.add(both & ~passed, "BUSINESS_RULE_VIOLATION", rule["left"],
                      f"Rule '{name}' failed: {rule['left']} {rule['op']} {rule['right']}",
                      observed=serial[rule["left"]].astype(str) + f" {rule['op']} " + serial[rule["right"]].astype(str))
        elif kind == "product_equals":
            cols = [rule["target"], *rule["factors"]]
            ok = pd.concat([data[x].notna() & ~tr.invalid[x] for x in cols], axis=1).all(axis=1)
            if ok.any():
                product = data[rule["factors"][0]].astype("Float64")
                for f in rule["factors"][1:]:
                    product = product * data[f].astype("Float64")
                diff = (data[rule["target"]].astype("Float64") - product).abs()
                c.add(ok & (diff > rule.get("tolerance", 0.01) + 1e-9), "BUSINESS_RULE_VIOLATION", rule["target"],
                      f"Rule '{name}' failed: {rule['target']} != {' * '.join(rule['factors'])}",
                      observed=serial[rule["target"]].astype(str) + " vs expected " + product.round(2).astype(str))
        elif kind == "not_in_future":
            col = rule["column"]
            if by_name[col].data_type in TIME_TYPES:
                limit = now + pd.Timedelta(minutes=rule.get("tolerance_minutes", 5))
                ts = data[col]
                c.add(ts.notna() & (ts > limit), "FUTURE_TIMESTAMP", col,
                      f"Rule '{name}' failed: '{col}' is later than now (+{rule.get('tolerance_minutes', 5)} min)")
    return c.result()
