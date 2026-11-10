# Data Contract Validation Platform

A working data-engineering platform. Producers deliver CSV/JSON batches, and the platform:

- checks each batch against a **versioned data contract**
- **detects schema changes** and classifies them as **breaking or non-breaking**
- validates every record
- **quarantines** invalid records together with their reasons
- **publishes** valid records idempotently
- records measurable metrics for every pipeline run

Stack: FastAPI · SQLAlchemy 2 · Pydantic 2 · pandas · MySQL 8 (PyMySQL) · vanilla HTML/CSS/JS ·
pytest · optional LLM (any OpenAI-compatible API, tested with Ollama `llama3.2:3b`).

---

## 1. Business problem

Downstream consumers (analytics, finance, ML) break when producers silently change data:

- a column disappears
- a type changes
- a new enum value appears
- duplicates or orphan records slip in

A **data contract** makes the producer's promise explicit and machine-checkable:

- columns and types
- nullability
- allowed values
- keys and uniqueness
- foreign keys
- business rules
- a compatibility policy

The platform enforces that promise at the boundary. Bad batches are blocked, bad rows are
quarantined with traceable reasons, and good rows are published exactly once.

## 2. Architecture

```mermaid
flowchart LR
    P[Producer CSV / JSON] -->|POST /api/ingestion/upload| ING[Ingestion<br/>ext/size check, SHA-256,<br/>store original]
    ING --> RUN[(pipeline_runs<br/>PENDING)]
    RUN --> IDEM{Same checksum +<br/>contract version<br/>already succeeded?}
    IDEM -->|yes| SKIP[SKIPPED_DUPLICATE]
    IDEM -->|no| PARSE[Parse → raw text frame<br/>+ exact original]
    PARSE -->|unreadable / empty| FAIL[FAILED]
    PARSE --> SCHEMA[Schema validation<br/>missing / unexpected / renamed /<br/>type / nullability / enum drift]
    SCHEMA --> CLASS{Any BREAKING<br/>change?}
    CLASS -->|yes| FAIL
    CLASS -->|no| TRANS[Deterministic transformation<br/>trim, cast, ISO-8601, defaults]
    TRANS --> VAL[Record validation<br/>vectorised rules, FK lookup]
    VAL -->|invalid rows| Q[(rejected_records +<br/>rejection_errors)]
    VAL -->|valid rows| PUB[Idempotent upsert<br/>record_key + event-time guard]
    PUB --> VR[(valid_records)]
    Q --> DONE[COMPLETED /<br/>COMPLETED_WITH_ERRORS]
    VR --> DONE
    DONE --> UI[Dashboard / API / AI explanations]
```

Quarantine, publishing and the final run status are committed in **one transaction**.

```
backend/
  main.py              FastAPI app, error handlers (no stack traces leak), static UI at /ui
  config.py            settings from .env (pydantic-settings / python-dotenv)
  database.py          engine, sessions, init_db()
  models/              SQLAlchemy tables (contract, pipeline_run, records, schema_change)
  schemas/             Pydantic request/response models (contract rules validated here)
  routers/             thin HTTP layer: contracts, ingestion, pipeline, validation, ai
  services/
    ingestion_service       upload checks, checksum, parsing (CSV/JSON → text frames)
    schema_validator        batch schema vs contract (structural + column-level drift)
    compatibility_service   contract version N vs N+1: BREAKING / NON_BREAKING rules
    transformation_service  deterministic casting & normalisation
    data_validator          record-level checks (13 error codes), vectorised with pandas
    quarantine_service      store / query / export rejected records
    publishing_service      idempotent upsert into valid_records
    pipeline_service        orchestration, state machine, transactions
    run_service             read models, reports, dashboard metrics
    llm_service             advisory explanations (OpenAI-compatible or mock)
frontend/              7 pages: dashboard, contracts, ingestion, validation, rejected, runs, AI
scripts/               init_db, generate_data, seed_demo, benchmark
tests/                 pytest suite (82 tests) against an isolated MySQL test database
data/samples/          sample contracts, generated sample batches, manifest, sample outputs
docs/                  PERFORMANCE_REPORT.md, TEST_RESULTS.md
DATA_DICTIONARY.md     every table and column
DESIGN_DECISIONS.md    architecture, compatibility policy, idempotency, quarantine, trade-offs
```

## 3. Setup (Windows / PowerShell)

Prerequisites: Python 3.10+ and MySQL 8. Ollama is optional.

```powershell
cd "data contract validation platform"
py -3.10 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

> **Corporate proxy or antivirus (e.g. Avast Web Shield) causing `CERTIFICATE_VERIFY_FAILED`?**
> Don't disable verification. Export the Windows trusted roots and pass them to pip:
> ```powershell
> .venv\Scripts\python.exe -c "import ssl;open('roots.pem','w').write(''.join(ssl.DER_cert_to_PEM_cert(c) for s in ('ROOT','CA') for c,e,t in ssl.enum_certificates(s) if e=='x509_asn'))"
> .venv\Scripts\python.exe -m pip install --cert roots.pem -r requirements.txt
> ```

Create the databases. Use your own MySQL user and port:

```sql
CREATE DATABASE data_contract_db   CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE DATABASE data_contract_test CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

Copy `.env.example` to `.env` and fill it in (see section 4), then create the tables:

```powershell
.venv\Scripts\python.exe scripts\init_db.py          # main database
.venv\Scripts\python.exe scripts\init_db.py --test   # test database
```

The app also creates any missing tables at startup.

## 4. Environment variables

| Variable | Example | Purpose |
|---|---|---|
| `DATABASE_URL` | `mysql+pymysql://user:pass@127.0.0.1:3306/data_contract_db` | Main database |
| `TEST_DATABASE_URL` | `mysql+pymysql://user:pass@127.0.0.1:3306/data_contract_test` | Used only by pytest and the benchmark. The database name **must contain `test`**; tests empty its tables |
| `LLM_PROVIDER` | `openai_compatible` or `mock` | `mock` = deterministic explanations, no network |
| `LLM_BASE_URL` | `http://localhost:11434/v1` (Ollama) | Any OpenAI-compatible `/chat/completions` endpoint |
| `LLM_API_KEY` | *(empty for Ollama)* | Sent as a Bearer token. Never logged or returned by the API |
| `LLM_MODEL` | `llama3.2:3b` | Model name |
| `LLM_TIMEOUT_SECONDS` | `120` | CPU-only local models need about 45 s |
| `MAX_UPLOAD_MB` | `50` | Upload size limit (HTTP 413 above it) |
| `UPLOAD_DIR` | `data/uploads` | Original files, stored by checksum |
| `LOG_LEVEL` | `INFO` | JSON-lines structured logs |

**Configuring the company LLM later:** set `LLM_PROVIDER=openai_compatible`, `LLM_BASE_URL`
(the URL that ends before `/chat/completions`), `LLM_API_KEY` and `LLM_MODEL`, then restart.
`GET /api/ai/status` shows whether the LLM is reachable. If it isn't, or it returns invalid output,
the app falls back to the mock automatically and labels the response accordingly.

## 5. Run

```powershell
.venv\Scripts\python.exe -m uvicorn backend.main:app --reload
```

- Dashboard: <http://127.0.0.1:8000/> (redirects to `/ui/index.html`)
- Interactive API docs (Swagger): <http://127.0.0.1:8000/docs>

Load the full demo through the API, in a second terminal while the server runs:

```powershell
.venv\Scripts\python.exe scripts\seed_demo.py
```

## 6. Sample workflow (what the demo shows)

| # | Upload | Result | Why |
|---|---|---|---|
| 1 | `customers.csv` (58 rows) | COMPLETED_WITH_ERRORS: 50 published, 8 quarantined | 8 injected bad customers (email, country, null name, duplicate PK) |
| 2 | `orders.csv` (100 rows, shuffled) | COMPLETED_WITH_ERRORS: 90 published, 10 quarantined | One injected defect of each type: NULL, dup PK, bad type, bad enum, negative qty, bad FK, bad timestamp, total ≠ qty×price, ship before order, duplicate event |
| 3 | `orders.json` | COMPLETED: 0 published, 50 unchanged | Same records already published, so idempotent |
| 4 | `orders_unexpected_column.csv` | COMPLETED | Extra column = **NON_BREAKING** under BACKWARD policy (ignored, not published) |
| 5 | `orders_missing_column.csv` | **FAILED**, nothing published | Required `currency` missing = **BREAKING** |
| 6 | `orders_renamed_column.csv` | **FAILED** | `unit_price` → `unit_price_amount` without `renamed_from` = **BREAKING** rename candidate |
| 7 | `orders_late.csv` | COMPLETED: 10 inserted, 10 stale skipped | Older events never overwrite newer published data; late *new* records are accepted |
| 8 | `orders.csv` again | SKIPPED_DUPLICATE | Same checksum + contract version already processed |
| 9 | Orders contract v2 (+ optional `gift_wrap`) | Created, NON_BREAKING | Contract evolution; v1 is kept unchanged |

Sample outputs (run reports, a quarantine CSV export, a metrics snapshot) are in
`data/samples/outputs/`.

### Real-world files (not generated)

| File | What it shows |
|---|---|
| `sales_raw.csv` (53,200 rows) | Original export. Headers contain spaces (`Order Id`) and dates are US style (`6/17/2017`), so it does not fit a contract as-is. |
| `sales_clean.csv` | `sales_raw.csv` after `scripts\prepare_sales_csv.py` (headers `Order_Id`, ISO dates). Data problems are deliberately left in. |
| `contracts/sales_contract.json` | 19-column contract for `sales_clean.csv` (also offered as a template in the UI). |
| `superstore_orders_malformed.csv` | A file that is not valid CSV (inch marks like `72"H` inside quoted values). The upload is refused with *Malformed CSV*, which is correct. |

Uploading `sales_clean.csv` to the `sales` contract gives **COMPLETED_WITH_ERRORS: 53,159 published,
41 quarantined**: 23 empty `Sales`, 8 empty `Order_Date`, 5 misspelled categories
(`Camping and Hiking`), 4 duplicate `Row_Id`s, 3 zip codes in `Customer_State`
(a few rows have two problems).

```powershell
.venv\Scripts\python.exe scripts\prepare_sales_csv.py data\samples\sales_raw.csv data\samples\sales_clean.csv
```

## 7. Testing

```powershell
.venv\Scripts\python.exe -m pytest tests -v
```

**82 tests, all passing** (see [docs/TEST_RESULTS.md](docs/TEST_RESULTS.md)).

The tests run against the isolated `TEST_DATABASE_URL` database, and tables are truncated
before each test. The LLM is forced to mock mode. Coverage of the required cases:

| Required case | Test |
|---|---|
| Happy path | `test_pipeline.py::test_happy_path_customers`, `test_happy_path_orders_with_fk` |
| Empty input | `test_pipeline.py::test_empty_input_fails_run`, `test_zero_byte_file_rejected` |
| Malformed CSV | `test_malformed_csv_rows_are_quarantined`, `test_malformed_csv_quoting_fails_run`, `test_malformed_json_fails_run` |
| Missing required column | `test_schema_validation.py::test_missing_required_column_is_breaking_and_blocks_publish` |
| Unexpected column | `test_unexpected_column_is_not_confused_with_missing`, `..._breaking_under_strict_policy` |
| Invalid data type | `test_data_validation.py::test_invalid_data_type`, `test_schema_validation.py::test_column_level_type_change_is_breaking` |
| NULL in non-nullable | `test_null_in_non_nullable_field`, `test_all_null_non_nullable_column_is_breaking` |
| Duplicate primary key | `test_duplicate_primary_key_keeps_first` |
| Invalid allowed value | `test_invalid_allowed_value` |
| Invalid foreign key | `test_invalid_foreign_key` |
| Breaking schema change | `test_compatibility.py` (11 rules), `test_breaking_version_requires_acknowledgement` |
| Non-breaking schema change | `test_add_optional_nullable_column_is_non_breaking`, `test_mapped_rename_is_non_breaking`, … |
| Partial failure and retry | `test_idempotency.py::test_partial_failure_rolls_back_and_retry_succeeds` |
| Reprocessing same batch | `test_reprocessing_same_batch_is_skipped`, `test_forced_reprocess_does_not_duplicate` |
| Late-arriving data | `test_late_arriving_older_event_does_not_overwrite_newer`, `test_out_of_order_events_across_batches_newest_wins` |
| Domain edge cases | `test_domain_total_must_equal_quantity_times_price`, `test_domain_cannot_ship_before_order`, `test_domain_order_in_future_rejected` |
| Generator ↔ pipeline | `test_generated_data.py`: rejected rows equal the injected rows, row for row |

## 8. Sample data and benchmark

```powershell
# 100 orders + 58 customers + scenario files -> data/samples (seed 42)
.venv\Scripts\python.exe scripts\generate_data.py
# 100k orders -> data/generated
.venv\Scripts\python.exe scripts\generate_data.py --orders 100000 --customers 5000 --out data/generated
# throughput for 1k / 10k / 100k (uses the TEST database)
.venv\Scripts\python.exe scripts\benchmark.py
```

`manifest.json` lists every deliberately invalid row by row number and anomaly type. The
production rules know nothing about it.

**Benchmark (laptop, local MySQL):** 100,000 records in 28 s, about **3,560 records/s**. The
95,000 valid and 5,000 rejected rows match the manifest exactly. See
[docs/PERFORMANCE_REPORT.md](docs/PERFORMANCE_REPORT.md).

## 9. API overview

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | DB connectivity, LLM provider |
| GET / POST | `/api/contracts` | List / create contract (creates v1) |
| GET | `/api/contracts/{id}?version=` | Contract + version detail (columns, rules, FKs, changes) |
| GET | `/api/contracts/{id}/versions/{v}` | One version |
| POST | `/api/contracts/{id}/compatibility-check` | Dry-run classification of a proposed version |
| POST | `/api/contracts/{id}/versions` | Create version N+1. Returns 409 for breaking changes unless `acknowledge_breaking=true` |
| GET | `/api/contracts/templates` | Sample contract JSON for the UI |
| POST | `/api/ingestion/upload` | Multipart: `file`, `contract_id`, `version?`, `source_system?`, `force?`, `sync?` |
| GET | `/api/runs` · `/api/runs/{run_id}` | Run history / one run (`status`, `contract_id` filters) |
| POST | `/api/runs/{run_id}/retry?force=` | New attempt for the same file and version |
| GET | `/api/runs/{run_id}/report` | Schema changes, error summary, sample rejections |
| GET | `/api/schema-changes` | Filter by `classification`, `scope` (BATCH/CONTRACT), `contract_id` |
| GET | `/api/rejected` | Quarantine, filter by `run_id`, `error_code`, `contract_id` (paginated) |
| GET | `/api/rejected/{id}` | One rejected record with all reasons and the original row |
| GET | `/api/rejected/export` | CSV export (same filters, streamed) |
| GET | `/api/valid-records?contract_id=` | Published target data |
| GET | `/api/error-codes` | Error code catalogue |
| GET | `/api/metrics/summary` | Dashboard metrics |
| GET | `/api/ai/status` · POST `/api/ai/explain` | LLM status / advisory explanation (`summarize_run`, `explain_rejections`, `explain_schema_changes`, `suggest_fixes`, `explain_contract_version`) |

Full request and response schemas are at `/docs` (OpenAPI).

## 10. Known limitations

- **Generic target table.** Published data lives in one `valid_records` table, with the payload
  stored as JSON and keyed per contract. It is not one typed table per dataset (see
  DESIGN_DECISIONS.md).
- **Duplicate business events are detected within a batch only.** A different `order_id` with
  an identical (customer, product, order_ts) in a *later* batch is not detected.
- **Foreign keys** must reference the parent contract's single-column primary key, and parent
  data must be published first. Self-references are not supported.
- **Timestamps** must lie between 1677 and 2262 (pandas nanosecond range); values outside it
  are rejected as `INVALID_TIMESTAMP`. Slash dates are interpreted **day-first**. Values without
  a timezone are treated as UTC.
- **Processing is in-process** (FastAPI BackgroundTasks). A server restart during a run leaves
  it in `RUNNING`; it can be re-submitted or retried, and nothing partial was published. There
  is no automatic stale-run reaper.
- **Concurrency guard.** An identical batch submitted while the first is still running is
  skipped, but two different batches touching the same keys rely on the DB unique key and the
  event-time guard, not on a queue.
- **Schema migrations** use `create_all` (no Alembic).
- **Authentication and authorisation** are not implemented.
- A 3B local model writes generic and occasionally imprecise text. This is acceptable because
  the AI is advisory only.

## 11. Future improvements

- Typed per-contract target tables (generated DDL) or a lakehouse sink (Parquet/Iceberg).
- A durable job queue (e.g. Celery/RQ) with a stale-run reaper and per-dataset ordering.
- Alembic migrations, auth (OAuth2/API keys) and per-producer permissions.
- Streaming / chunked parsing for multi-GB files, and `LOAD DATA` staging for faster publishing.
- Contract registry features: approval workflow for breaking versions, consumer subscriptions
  and notifications.
- Cross-batch business-event deduplication using a persisted event-key index.
