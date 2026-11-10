# Design Decisions

## 1. Architecture

**Thin routers, logic in services.** Routers only parse HTTP input and call a service.
Validation, compatibility and transformation are plain functions over pandas frames and
contract objects. That makes them unit-testable without HTTP or a database (see
`tests/test_compatibility.py` and the unit tests in `test_data_validation.py`), and impossible to
mix up with LLM concerns.

**Modular monolith, no distributed infrastructure.** One FastAPI process, MySQL, and
pandas for vectorised processing. The benchmark shows about 3,500 records/s end-to-end, which
is ample for this workload; Spark or Kafka would add operational cost without benefit at this
scale.

**Everything is read as text first.** Parsers produce two frames: the exact original values
(for quarantine and traceability) and a stripped text frame with NULL tokens normalised
(`""`, `null`, `none`, `nan`). Types are then established explicitly by the contract, not
guessed by pandas. A `"007"` id stays `"007"`, and `"2.5"` in an integer column is an error, not a
silent truncation.

**Two-phase intake.** The HTTP request does only the fast checks (extension, size, checksum,
store original) and returns `202` with a run id. Parsing, validation and publishing happen in
`pipeline_service.process_run()` (a background task; `sync=true` runs it inline for tests and
scripts). Problems found during processing (bad encoding, malformed JSON, empty batch) fail
the **run** with a reason, so they appear in pipeline metrics instead of disappearing as 4xx
responses.

**Errors never leak internals.** Domain errors map to 400/404/409/413 with clear messages. Any
other exception returns `500 {"detail": "Internal server error", "error_id": …}`; the stack
trace is only in the structured log under that `error_id`. Connection strings are never
returned (`/api/health` shows `host:port/db` only).

## 2. Schema changes: two different comparisons

| | Batch vs contract (`schema_validator`) | Contract N vs N+1 (`compatibility_service`) |
|---|---|---|
| When | Every pipeline run | When a new contract version is proposed |
| Stored as | `schema_changes.scope = BATCH` | `schema_changes.scope = CONTRACT` |
| Effect of BREAKING | Run FAILED, **nothing published** | 409 unless `acknowledge_breaking=true` |

**Schema-level vs record-level.** A few bad values in a column are a *record* problem: those
rows are quarantined. A column only counts as a schema change when it no longer matches as a
whole:
- `TYPE_MISMATCH`: more than 50% of non-null values fail to cast
- `NULLABILITY_VIOLATION`: a non-nullable column is NULL in every row

This avoids blocking a 100k batch because of one typo, while still catching a producer that
switched `quantity` to text.

**Missing vs unexpected vs renamed.** These are separate checks:
- A contract column absent from the batch is `MISSING_REQUIRED_COLUMN` (breaking) or
  `MISSING_OPTIONAL_COLUMN` (non-breaking; the default is applied).
- A batch column not in the contract is `UNEXPECTED_COLUMN`.
- A missing column plus an unexpected column with a similar name (edit similarity ≥ 0.6, or one
  name containing the other) and castable values is reported **once** as `RENAME_CANDIDATE`,
  instead of as two unrelated problems.
- A declared `renamed_from` mapping turns the rename into `COLUMN_RENAME_MAPPED` (non-breaking),
  and old producers keep working.

## 3. Compatibility policy

The policy of the **old** version applies, because that is what consumers subscribed to.

| Change | BACKWARD (default) | STRICT |
|---|---|---|
| Add optional / nullable / defaulted column | NON_BREAKING | NON_BREAKING |
| Add required non-null column without default | BREAKING | BREAKING |
| Remove required column | BREAKING | BREAKING |
| Remove optional column | NON_BREAKING (warning) | BREAKING |
| Rename with `renamed_from` | NON_BREAKING | NON_BREAKING |
| Rename without mapping (required column) | BREAKING | BREAKING |
| Type widening (int→float/decimal, float→decimal, date→timestamp) | NON_BREAKING | NON_BREAKING |
| Any other type change | BREAKING | BREAKING |
| Nullable → non-nullable | BREAKING | BREAKING |
| Non-nullable → nullable | NON_BREAKING (warning) | BREAKING |
| Optional → required | BREAKING | BREAKING |
| Primary key changed | BREAKING | BREAKING |
| Enum value **added** | NON_BREAKING | **BREAKING** (exhaustive consumers) |
| Enum value removed / enum introduced | BREAKING | BREAKING |
| Range or max_length tightened | BREAKING | BREAKING |
| Range or max_length relaxed | NON_BREAKING | NON_BREAKING |
| Regex pattern changed | BREAKING (equivalence can't be proven) | BREAKING |
| Batch has unexpected column | NON_BREAKING (ignored, not published) | BREAKING |
| Batch has values outside the enum | NON_BREAKING (rows quarantined) | BREAKING (batch blocked) |

The rule is conservative: when compatibility cannot be proven, the change is classified as breaking.

## 4. Record validation and transformation

- **Deterministic and rule-based.** Thirteen error codes, each implemented as a vectorised
  boolean mask. A record collects **all** of its errors, not only the first.
- **Transformations never repair invalid data.**
  - Trimming, declared case normalisation (`normalize`), ISO-8601 standardisation and documented
    defaults are applied.
  - A value that can't be converted is flagged, not coerced: `"2.5"` is never truncated to 2,
    and `"2024-02-30"` is never rolled over.
  - Case normalisation of enum values happens only where the contract explicitly permits it.
  - Counts of every transformation are stored in `transformation_summary`.
- **Business rules are declarative JSON** (`compare_columns`, `product_equals`,
  `not_in_future`), validated by Pydantic when a contract is created. There is no `eval`, so a
  contract cannot inject code.
- **Duplicates within a batch:** the first occurrence (file order) is kept and later ones are
  rejected. Each rejection message names the row that was kept.
- **Referential integrity** is checked against the parent's *published* records, in chunked
  `IN` lookups, inside the same transaction as publishing.

## 5. Idempotency and recovery

| Layer | Mechanism |
|---|---|
| Same file again | `idempotency_key = sha256(file checksum | contract_version_id)`. If a run with that key is COMPLETED / COMPLETED_WITH_ERRORS / RUNNING, the new run is recorded as `SKIPPED_DUPLICATE` with `duplicate_of_run_id` |
| Same record again | `record_key = sha256(contract_id | primary key)` with `UNIQUE(contract_id, record_key)`. Publishing is `INSERT … ON DUPLICATE KEY UPDATE`, so a record can never be duplicated, even with `force=true` or with overlapping files |
| Identical re-delivery | `record_hash` comparison means no write (`unchanged_count`) |
| Late / out-of-order data | The update branch only applies if the incoming `event_ts ≥ stored event_ts` (also enforced in SQL, which protects against concurrent runs). Older events are counted as `stale_skipped`. *New* keys arriving late are published and counted as `late_arriving` |

**Transactions and run states**
- T1: `PENDING → RUNNING` is committed, so progress is visible.
- T2: schema changes, quarantined rows, published rows and the final status are committed
  **together**.
- Any exception in T2 rolls everything back, and the run is marked `FAILED` in a separate
  transaction with an `error_id`. A run therefore can never be "successful" with half its
  rows published. `test_partial_failure_rolls_back_and_retry_succeeds` injects a crash after
  half of the rows were written and proves zero rows remain.

**Behaviours**
- **Retry:** `POST /api/runs/{run_id}/retry` creates attempt N+1 for the same stored file and
  contract version. FAILED runs don't count as processed, so a retry needs no `force`.
- **Partial failure:** nothing partial is persisted, so the whole batch is retried.
- **Restart:** the original file is stored by checksum, so any run can be replayed after a
  restart. A run interrupted by a process crash stays `RUNNING` (no reaper — see limitations).
  Retry it; that is safe because nothing was committed.
- **Backfill:** upload historical files, or retry with `force=true`. Upserts guarded by event
  time make backfills safe: old data cannot overwrite newer data.
- **Duplicate batch:** recorded as `SKIPPED_DUPLICATE` (visible in metrics), not silently
  ignored.

## 6. Quarantine design

- **Two tables.**
  - `rejected_records` holds one row per rejected input row: the exact original values, the
    1-based source row number, the run, the file and the contract version.
  - `rejection_errors` holds one row per error, with an indexed `error_code`.
  - This normalises the one-to-many relationship and makes "filter by error type" an index
    lookup instead of a JSON scan. `rejected_records.error_codes` is a small denormalised list
    kept for display.
- **Unparseable rows** (wrong field count) are quarantined with their raw fields
  (`_raw_fields`) instead of failing the whole file. Structurally broken files (unterminated
  quote, invalid JSON) fail the run.
- **Export** streams CSV in 1,000-row pages (constant memory), with its own DB session.
- **Traceability:** run → source_files (checksum, stored original) → row number → original
  record → every reason and observed value.

## 7. AI (LLM) boundaries

- Pass/fail, counts, classifications and the risk level are computed by Python. They are
  returned as `facts` next to the AI text and are never taken from the model.
- The model receives a compact JSON summary only. It has no tools and no SQL. Producer values
  are labelled as untrusted in the system prompt. During testing, a planted "ignore previous
  instructions" value was ignored, but the design does not depend on that.
- Output must parse as JSON and validate against `AIInsight` (lengths, list sizes). Anything
  else, as well as timeouts and connection errors, falls back to a deterministic explanation
  and is labelled `provider: "mock"` with a `fallback_reason`.
- Provider-agnostic: any OpenAI-compatible endpoint (Ollama, vLLM, LiteLLM, company gateway).

## 8. Trade-offs and limitations

| Decision | Benefit | Cost |
|---|---|---|
| Generic `valid_records` with a JSON payload | Any contract can be published without runtime DDL; versions coexist; lineage columns for free | Not a typed warehouse table; JSON queries are slower; no DB-level column constraints on payload fields |
| pandas in-process | Simple, fast enough (3.5k rec/s), easy to test | Memory-bound (whole file in RAM); single node |
| BackgroundTasks instead of a queue | No extra infrastructure | No durable queue; a crash leaves a run RUNNING |
| Custom vectorised validator instead of Pandera | Our own error codes, multiple errors per row with observed values, cross-run FK lookups, quarantine-friendly output | Re-implements some checks Pandera offers |
| `create_all` instead of Alembic | Zero setup | No migration history |
| Conservative compatibility (e.g. pattern change = breaking) | Never under-reports risk | Some safe changes need explicit acknowledgement |
| First occurrence wins for in-batch duplicates | Deterministic, explainable | The "right" record may be a later one; the producer must fix it |
| Timestamps via pandas `datetime64[ns]` | Fast vectorised parsing | Range limited to 1677–2262 |
