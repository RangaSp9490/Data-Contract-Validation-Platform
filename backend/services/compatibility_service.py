"""Breaking / non-breaking classification for contract evolution (version N -> N+1).

All rules are explicit and deterministic. The policy that applies is the OLD
version's compatibility_mode, because that is what existing consumers signed up for.

  BACKWARD  additive / relaxing changes are safe for existing consumers.
  STRICT    consumers have a fixed schema: added enum values, relaxed nullability
            and removal of optional columns are also breaking.

Change types produced here:
  COLUMN_ADDED_OPTIONAL      NON_BREAKING  new column is optional, nullable or has a default
  COLUMN_ADDED_REQUIRED      BREAKING      new required non-null column without default
  COLUMN_REMOVED             BREAKING      required column removed  (optional: policy dependent)
  COLUMN_RENAMED             NON_BREAKING  rename declared through `renamed_from` mapping
  COLUMN_RENAMED_UNMAPPED    BREAKING      looks like a rename but no mapping was declared
  TYPE_WIDENED               NON_BREAKING  integer->float/decimal, date->timestamp
  TYPE_CHANGED               BREAKING      any other type change
  NULLABILITY_TIGHTENED      BREAKING      nullable -> non-nullable (existing data may contain NULL)
  NULLABILITY_RELAXED        policy        non-nullable -> nullable
  REQUIRED_ADDED / REQUIRED_RELAXED
  PRIMARY_KEY_CHANGED        BREAKING
  ALLOWED_VALUES_ADDED       policy        enum gained values
  ALLOWED_VALUES_REMOVED     BREAKING      enum lost values
  ALLOWED_VALUES_RESTRICTED  BREAKING      enum constraint introduced on a free column
  ALLOWED_VALUES_UNRESTRICTED policy       enum constraint dropped
  RANGE_TIGHTENED / RANGE_RELAXED, MAX_LENGTH_CHANGED, PATTERN_CHANGED, DEFAULT_CHANGED
"""
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple

BREAKING = "BREAKING"
NON_BREAKING = "NON_BREAKING"
INFO, WARNING, CRITICAL = "INFO", "WARNING", "CRITICAL"

SAFE_WIDENINGS = {("integer", "float"), ("integer", "decimal"), ("float", "decimal"), ("date", "timestamp")}
RENAME_SIMILARITY = 0.6

COLUMN_FIELDS = (
    "data_type", "required", "nullable", "is_primary_key", "allowed_values", "default_value",
    "min_value", "max_value", "max_length", "pattern", "normalize", "renamed_from",
)


@dataclass
class Change:
    change_type: str
    column_name: Optional[str]
    classification: str
    severity: str
    explanation: str
    old_schema: Optional[Dict[str, Any]] = None
    new_schema: Optional[Dict[str, Any]] = None

    @property
    def is_breaking(self) -> bool:
        return self.classification == BREAKING

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Snapshot:
    """Comparable view of a contract version (built from ORM rows or Pydantic specs)."""
    compatibility_mode: str
    primary_key: List[str]
    columns: Dict[str, Dict[str, Any]] = field(default_factory=dict)


def snapshot_from_columns(columns: List[Any], compatibility_mode: str) -> Snapshot:
    cols = {c.name: {f: getattr(c, f) for f in COLUMN_FIELDS} for c in columns}
    pk = [c.name for c in columns if c.is_primary_key]
    return Snapshot(compatibility_mode=compatibility_mode, primary_key=pk, columns=cols)


def policy(mode: str, *, backward: str, strict: str) -> str:
    return strict if mode == "STRICT" else backward


def name_similarity(a: str, b: str) -> float:
    """Edit-similarity, boosted when one name contains the other (email -> email_address)."""
    a, b = a.lower(), b.lower()
    score = SequenceMatcher(None, a, b).ratio()
    shorter, longer = sorted((a, b), key=len)
    if len(shorter) >= 3 and shorter in longer:
        score = max(score, 0.75)
    return score


def find_rename_pairs(
    removed: Dict[str, Dict[str, Any]], added: Dict[str, Dict[str, Any]],
    type_of=lambda spec: spec.get("data_type"),
) -> List[Tuple[str, str, float]]:
    """Greedy pairing of removed/added columns with compatible type and similar name."""
    candidates = []
    for old_name, old_spec in removed.items():
        for new_name, new_spec in added.items():
            old_t, new_t = type_of(old_spec), type_of(new_spec)
            if old_t is not None and new_t is not None and old_t != new_t and (old_t, new_t) not in SAFE_WIDENINGS:
                continue
            score = name_similarity(old_name, new_name)
            if score >= RENAME_SIMILARITY:
                candidates.append((score, old_name, new_name))
    pairs, used_old, used_new = [], set(), set()
    for score, old_name, new_name in sorted(candidates, reverse=True):
        if old_name not in used_old and new_name not in used_new:
            pairs.append((old_name, new_name, round(score, 2)))
            used_old.add(old_name)
            used_new.add(new_name)
    return pairs


def _num_cmp_tightened(old: Optional[float], new: Optional[float], is_min: bool) -> Optional[bool]:
    """True = tightened, False = relaxed, None = unchanged."""
    if old == new:
        return None
    if old is None:
        return True
    if new is None:
        return False
    return new > old if is_min else new < old


def _compare_column(name: str, old: Dict[str, Any], new: Dict[str, Any], mode: str) -> List[Change]:
    out: List[Change] = []

    def add(ctype, cls, sev, msg, keys):
        out.append(Change(ctype, name, cls, sev, msg,
                          {k: old.get(k) for k in keys}, {k: new.get(k) for k in keys}))

    ot, nt = old["data_type"], new["data_type"]
    if ot != nt:
        if (ot, nt) in SAFE_WIDENINGS:
            add("TYPE_WIDENED", NON_BREAKING, WARNING,
                f"'{name}' widened from {ot} to {nt}; every existing value is still representable.", ["data_type"])
        else:
            add("TYPE_CHANGED", BREAKING, CRITICAL,
                f"'{name}' changed type from {ot} to {nt}; consumers parsing {ot} will fail.", ["data_type"])

    if old["nullable"] and not new["nullable"]:
        add("NULLABILITY_TIGHTENED", BREAKING, CRITICAL,
            f"'{name}' changed from nullable to non-nullable; existing data and producers may still contain NULL.",
            ["nullable"])
    elif not old["nullable"] and new["nullable"]:
        add("NULLABILITY_RELAXED", policy(mode, backward=NON_BREAKING, strict=BREAKING), WARNING,
            f"'{name}' may now be NULL; consumers that assumed a value must handle NULL.", ["nullable"])

    if not old["required"] and new["required"]:
        add("REQUIRED_ADDED", BREAKING, CRITICAL,
            f"'{name}' became required; producers that omit it will be rejected.", ["required"])
    elif old["required"] and not new["required"]:
        add("REQUIRED_RELAXED", policy(mode, backward=NON_BREAKING, strict=BREAKING), INFO,
            f"'{name}' is no longer required; batches may omit it.", ["required"])

    oav, nav = old.get("allowed_values"), new.get("allowed_values")
    if oav is None and nav is not None:
        add("ALLOWED_VALUES_RESTRICTED", BREAKING, CRITICAL,
            f"'{name}' is now restricted to {nav}; previously any value was accepted.", ["allowed_values"])
    elif oav is not None and nav is None:
        add("ALLOWED_VALUES_UNRESTRICTED", policy(mode, backward=NON_BREAKING, strict=BREAKING), WARNING,
            f"'{name}' no longer restricts its values; consumers may receive unexpected values.", ["allowed_values"])
    elif oav is not None and nav is not None:
        old_set, new_set = {str(v) for v in oav}, {str(v) for v in nav}
        removed, added = sorted(old_set - new_set), sorted(new_set - old_set)
        if removed:
            add("ALLOWED_VALUES_REMOVED", BREAKING, CRITICAL,
                f"'{name}' no longer allows {removed}; existing data and producers using them break.",
                ["allowed_values"])
        if added:
            add("ALLOWED_VALUES_ADDED", policy(mode, backward=NON_BREAKING, strict=BREAKING), WARNING,
                f"'{name}' now also allows {added}. "
                + ("Under STRICT policy consumers with exhaustive handling may break."
                   if mode == "STRICT" else "Additive under BACKWARD policy."), ["allowed_values"])

    tight_min = _num_cmp_tightened(old.get("min_value"), new.get("min_value"), is_min=True)
    tight_max = _num_cmp_tightened(old.get("max_value"), new.get("max_value"), is_min=False)
    if tight_min or tight_max:
        add("RANGE_TIGHTENED", BREAKING, CRITICAL,
            f"'{name}' numeric range was tightened; previously valid values may now be rejected.",
            ["min_value", "max_value"])
    elif tight_min is False or tight_max is False:
        add("RANGE_RELAXED", NON_BREAKING, INFO, f"'{name}' numeric range was relaxed.", ["min_value", "max_value"])

    oml, nml = old.get("max_length"), new.get("max_length")
    if oml != nml:
        tightened = nml is not None and (oml is None or nml < oml)
        add("MAX_LENGTH_CHANGED", BREAKING if tightened else NON_BREAKING, CRITICAL if tightened else INFO,
            f"'{name}' max_length changed from {oml} to {nml}.", ["max_length"])

    if old.get("pattern") != new.get("pattern"):
        add("PATTERN_CHANGED", BREAKING, WARNING,
            f"'{name}' pattern changed; compatibility of regular expressions cannot be proven, treated as breaking.",
            ["pattern"])

    if old.get("default_value") != new.get("default_value"):
        add("DEFAULT_CHANGED", NON_BREAKING, INFO,
            f"'{name}' default changed from {old.get('default_value')!r} to {new.get('default_value')!r}.",
            ["default_value"])
    return out


def compare_versions(old: Snapshot, new: Snapshot) -> List[Change]:
    mode = old.compatibility_mode
    changes: List[Change] = []

    if old.primary_key != new.primary_key:
        changes.append(Change(
            "PRIMARY_KEY_CHANGED", None, BREAKING, CRITICAL,
            f"Primary key changed from {old.primary_key} to {new.primary_key}; record identity and "
            "deduplication change for every consumer.",
            {"primary_key": old.primary_key}, {"primary_key": new.primary_key}))

    removed = {n: s for n, s in old.columns.items() if n not in new.columns}
    added = {n: s for n, s in new.columns.items() if n not in old.columns}

    # 1. Declared renames (renamed_from mapping) -> compatible.
    for new_name, spec in list(added.items()):
        src = spec.get("renamed_from")
        if src and src in removed:
            changes.append(Change(
                "COLUMN_RENAMED", new_name, NON_BREAKING, WARNING,
                f"'{src}' renamed to '{new_name}' with a compatibility mapping; batches using the old "
                "name are still accepted and mapped.",
                {"name": src, **removed[src]}, {"name": new_name, **spec}))
            changes.extend(_compare_column(new_name, removed[src], spec, mode))
            del removed[src], added[new_name]

    # 2. Undeclared renames -> breaking for required columns.
    for old_name, new_name, score in find_rename_pairs(removed, added):
        was_required = removed[old_name]["required"]
        changes.append(Change(
            "COLUMN_RENAMED_UNMAPPED", new_name,
            BREAKING if was_required else policy(mode, backward=NON_BREAKING, strict=BREAKING),
            CRITICAL if was_required else WARNING,
            f"'{old_name}' appears to be renamed to '{new_name}' (name similarity {score}) without a "
            f"renamed_from mapping. Add renamed_from='{old_name}' to make the rename compatible.",
            {"name": old_name, **removed[old_name]}, {"name": new_name, **added[new_name]}))
        del removed[old_name], added[new_name]

    for name, spec in removed.items():
        if spec["required"]:
            changes.append(Change("COLUMN_REMOVED", name, BREAKING, CRITICAL,
                                  f"Required column '{name}' was removed; consumers reading it will break.",
                                  {"name": name, **spec}, None))
        else:
            changes.append(Change("COLUMN_REMOVED", name, policy(mode, backward=NON_BREAKING, strict=BREAKING),
                                  WARNING, f"Optional column '{name}' was removed.", {"name": name, **spec}, None))

    for name, spec in added.items():
        if spec["required"] and not spec["nullable"] and spec.get("default_value") is None:
            changes.append(Change("COLUMN_ADDED_REQUIRED", name, BREAKING, CRITICAL,
                                  f"New required non-nullable column '{name}' has no default; existing "
                                  "producers cannot satisfy it.", None, {"name": name, **spec}))
        else:
            changes.append(Change("COLUMN_ADDED_OPTIONAL", name, NON_BREAKING, INFO,
                                  f"New column '{name}' is optional/nullable or has a default; existing "
                                  "producers and consumers keep working.", None, {"name": name, **spec}))

    for name in sorted(set(old.columns) & set(new.columns)):
        changes.extend(_compare_column(name, old.columns[name], new.columns[name], mode))
    return changes


def summarize(changes: List[Change]) -> Dict[str, Any]:
    breaking = sum(c.is_breaking for c in changes)
    return {"compatible": breaking == 0, "breaking_count": breaking,
            "non_breaking_count": len(changes) - breaking}
