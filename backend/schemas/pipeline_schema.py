from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict

from backend.schemas.contract_schema import ChangeOut


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    run_id: str
    status: str
    contract_id: int
    contract_name: Optional[str] = None
    dataset_name: Optional[str] = None
    contract_version: Optional[int] = None
    contract_version_id: int
    source_file_name: str
    source_checksum: Optional[str] = None
    source_system: Optional[str]
    attempt: int
    force_reprocess: bool
    duplicate_of_run_id: Optional[str]
    created_at: datetime
    started_at: Optional[datetime]
    ended_at: Optional[datetime]
    duration_ms: Optional[int]
    input_count: int
    valid_count: int
    rejected_count: int
    published_count: int
    inserted_count: int
    updated_count: int
    unchanged_count: int
    stale_skipped_count: int
    late_arriving_count: int
    schema_change_count: int
    breaking_change_count: int
    non_breaking_change_count: int
    success_rate: Optional[float] = None
    failure_reason: Optional[str]
    transformation_summary: Optional[Dict[str, Any]]


class RunList(BaseModel):
    total: int
    items: List[RunOut]


class ErrorCount(BaseModel):
    error_code: str
    column_name: Optional[str]
    count: int
    description: Optional[str] = None


class RejectionErrorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    error_code: str
    column_name: Optional[str]
    message: str
    observed_value: Optional[str]


class RejectedOut(BaseModel):
    id: int
    run_id: Optional[str]
    source_file: Optional[str]
    source_row_number: int
    original_record: Dict[str, Any]
    error_codes: List[str]
    error_count: int
    rejected_at: datetime
    errors: List[RejectionErrorOut]


class RejectedList(BaseModel):
    total: int
    items: List[RejectedOut]


class RunReport(BaseModel):
    run: RunOut
    schema_changes: List[ChangeOut]
    error_summary: List[ErrorCount]
    sample_rejections: List[RejectedOut]


class ValidRecordOut(BaseModel):
    primary_key_value: str
    payload: Dict[str, Any]
    event_ts: Optional[datetime]
    contract_version_id: int
    source_row_number: int
    first_run_id: Optional[str]
    last_run_id: Optional[str]
    updated_at: datetime


class ValidRecordList(BaseModel):
    total: int
    items: List[ValidRecordOut]


class MetricsSummary(BaseModel):
    total_runs: int
    runs_by_status: Dict[str, int]
    failed_runs: int
    total_records_processed: int
    valid_records: int
    rejected_records: int
    published_records: int
    validation_success_pct: Optional[float]
    avg_duration_ms: Optional[float]
    total_duration_ms: int
    schema_changes: int
    breaking_changes: int
    non_breaking_changes: int
    published_rows_current: int
    contracts: int
    top_error_codes: List[ErrorCount]
    recent_runs: List[RunOut]
    daily: List[Dict[str, Any]] = []              # per-day aggregates (UTC dates)
    filters: Dict[str, Any] = {}
