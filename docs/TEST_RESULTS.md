# Test Results

Executed 2026-11-10 11:50 on Windows 11, Python 3.10.5, MySQL 8.0.46 (isolated database `data_contract_test`), LLM in mock mode.

Command:

```powershell
.venv\Scripts\python.exe -m pytest tests -v
```

**Result: 82 passed in 64.67s (0:01:04)**

```text
============================= test session starts =============================
platform win32 -- Python 3.10.5, pytest-9.2.0, pluggy-1.7.0 -- C:\Users\V.R.LAKSHMI PRASANNA\Videos\yottaflex-session-pipeline\data contract validation platform\.venv\Scripts\python.exe
cachedir: .pytest_cache
rootdir: C:\Users\V.R.LAKSHMI PRASANNA\Videos\yottaflex-session-pipeline\data contract validation platform
plugins: anyio-4.16.0
collecting ... collected 82 items

tests/test_ai.py::test_mock_summary_uses_authoritative_facts PASSED      [  1%]
tests/test_ai.py::test_explain_rejections_recommends_fixes PASSED        [  2%]
tests/test_ai.py::test_invalid_llm_output_falls_back_to_mock PASSED      [  3%]
tests/test_ai.py::test_unreachable_llm_falls_back PASSED                 [  4%]
tests/test_ai.py::test_llm_text_cannot_change_status PASSED              [  6%]
tests/test_ai.py::test_contract_version_explanation PASSED               [  7%]
tests/test_ai.py::test_missing_run_id_is_400 PASSED                      [  8%]
tests/test_ai.py::test_status_never_exposes_key PASSED                   [  9%]
tests/test_compatibility.py::test_no_changes PASSED                      [ 10%]
tests/test_compatibility.py::test_add_optional_nullable_column_is_non_breaking PASSED [ 12%]
tests/test_compatibility.py::test_add_column_with_default_is_non_breaking PASSED [ 13%]
tests/test_compatibility.py::test_add_required_column_without_default_is_breaking PASSED [ 14%]
tests/test_compatibility.py::test_remove_required_column_is_breaking PASSED [ 15%]
tests/test_compatibility.py::test_remove_optional_column_depends_on_policy PASSED [ 17%]
tests/test_compatibility.py::test_incompatible_type_change_is_breaking PASSED [ 18%]
tests/test_compatibility.py::test_type_widening_is_non_breaking PASSED   [ 19%]
tests/test_compatibility.py::test_nullable_to_non_nullable_is_breaking PASSED [ 20%]
tests/test_compatibility.py::test_primary_key_change_is_breaking PASSED  [ 21%]
tests/test_compatibility.py::test_unmapped_rename_of_required_column_is_breaking PASSED [ 23%]
tests/test_compatibility.py::test_mapped_rename_is_non_breaking PASSED   [ 24%]
tests/test_compatibility.py::test_enum_value_added_depends_on_policy PASSED [ 25%]
tests/test_compatibility.py::test_enum_value_removed_is_breaking PASSED  [ 26%]
tests/test_compatibility.py::test_range_tightened_is_breaking_and_relaxed_is_not PASSED [ 28%]
tests/test_compatibility.py::test_breaking_change_through_api_records_schema_change PASSED [ 29%]
tests/test_contracts.py::test_create_and_get_contract PASSED             [ 30%]
tests/test_contracts.py::test_duplicate_dataset_rejected PASSED          [ 31%]
tests/test_contracts.py::test_contract_requires_primary_key PASSED       [ 32%]
tests/test_contracts.py::test_rule_referencing_unknown_column_rejected PASSED [ 34%]
tests/test_contracts.py::test_foreign_key_to_unknown_dataset_rejected PASSED [ 35%]
tests/test_contracts.py::test_foreign_key_resolved PASSED                [ 36%]
tests/test_contracts.py::test_non_breaking_version_is_created_and_v1_preserved PASSED [ 37%]
tests/test_contracts.py::test_breaking_version_requires_acknowledgement PASSED [ 39%]
tests/test_contracts.py::test_compatibility_check_is_dry_run PASSED      [ 40%]
tests/test_contracts.py::test_unknown_contract_404 PASSED                [ 41%]
tests/test_data_validation.py::test_null_in_non_nullable_field PASSED    [ 42%]
tests/test_data_validation.py::test_invalid_data_type PASSED             [ 43%]
tests/test_data_validation.py::test_invalid_timestamp PASSED             [ 45%]
tests/test_data_validation.py::test_invalid_allowed_value PASSED         [ 46%]
tests/test_data_validation.py::test_allowed_value_case_normalised_when_permitted PASSED [ 47%]
tests/test_data_validation.py::test_duplicate_primary_key_keeps_first PASSED [ 48%]
tests/test_data_validation.py::test_duplicate_business_event PASSED      [ 50%]
tests/test_data_validation.py::test_invalid_foreign_key PASSED           [ 51%]
tests/test_data_validation.py::test_negative_quantity_and_multiple_errors_per_record PASSED [ 52%]
tests/test_data_validation.py::test_domain_total_must_equal_quantity_times_price PASSED [ 53%]
tests/test_data_validation.py::test_domain_cannot_ship_before_order PASSED [ 54%]
tests/test_data_validation.py::test_domain_order_in_future_rejected PASSED [ 56%]
tests/test_data_validation.py::test_timestamp_formats_standardised_to_iso PASSED [ 57%]
tests/test_data_validation.py::test_rejection_filters_by_error_type PASSED [ 58%]
tests/test_data_validation.py::test_compare_rule_does_not_flag_passing_rows PASSED [ 59%]
tests/test_data_validation.py::test_integer_and_boolean_casting PASSED   [ 60%]
tests/test_generated_data.py::test_generator_is_deterministic PASSED     [ 62%]
tests/test_generated_data.py::test_pipeline_rejects_exactly_the_injected_rows PASSED [ 63%]
tests/test_generated_data.py::test_scenario_files PASSED                 [ 64%]
tests/test_idempotency.py::test_reprocessing_same_batch_is_skipped PASSED [ 65%]
tests/test_idempotency.py::test_forced_reprocess_does_not_duplicate PASSED [ 67%]
tests/test_idempotency.py::test_overlapping_batches_update_in_place PASSED [ 68%]
tests/test_idempotency.py::test_partial_failure_rolls_back_and_retry_succeeds PASSED [ 69%]
tests/test_idempotency.py::test_late_arriving_older_event_does_not_overwrite_newer PASSED [ 70%]
tests/test_idempotency.py::test_out_of_order_events_across_batches_newest_wins PASSED [ 71%]
tests/test_pipeline.py::test_happy_path_customers PASSED                 [ 73%]
tests/test_pipeline.py::test_happy_path_orders_with_fk PASSED            [ 74%]
tests/test_pipeline.py::test_json_upload PASSED                          [ 75%]
tests/test_pipeline.py::test_empty_input_fails_run PASSED                [ 76%]
tests/test_pipeline.py::test_zero_byte_file_rejected PASSED              [ 78%]
tests/test_pipeline.py::test_unsupported_extension_rejected PASSED       [ 79%]
tests/test_pipeline.py::test_malformed_csv_rows_are_quarantined PASSED   [ 80%]
tests/test_pipeline.py::test_malformed_csv_quoting_fails_run PASSED      [ 81%]
tests/test_pipeline.py::test_malformed_json_fails_run PASSED             [ 82%]
tests/test_pipeline.py::test_run_listing_and_report PASSED               [ 84%]
tests/test_pipeline.py::test_metrics_come_from_database PASSED           [ 85%]
tests/test_pipeline.py::test_async_upload_returns_pending_then_completes PASSED [ 86%]
tests/test_pipeline.py::test_export_rejected_csv PASSED                  [ 87%]
tests/test_schema_validation.py::test_missing_required_column_is_breaking_and_blocks_publish PASSED [ 89%]
tests/test_schema_validation.py::test_missing_primary_key_column PASSED  [ 90%]
tests/test_schema_validation.py::test_unexpected_column_is_not_confused_with_missing PASSED [ 91%]
tests/test_schema_validation.py::test_unexpected_column_breaking_under_strict_policy PASSED [ 92%]
tests/test_schema_validation.py::test_missing_optional_column_is_non_breaking_and_default_applied PASSED [ 93%]
tests/test_schema_validation.py::test_rename_candidate_detected PASSED   [ 95%]
tests/test_schema_validation.py::test_declared_rename_mapping_is_accepted PASSED [ 96%]
tests/test_schema_validation.py::test_column_level_type_change_is_breaking PASSED [ 97%]
tests/test_schema_validation.py::test_isolated_bad_value_is_record_level_not_schema PASSED [ 98%]
tests/test_schema_validation.py::test_all_null_non_nullable_column_is_breaking PASSED [100%]

============================= slowest 5 durations =============================
4.74s setup    tests/test_idempotency.py::test_out_of_order_events_across_batches_newest_wins
4.69s setup    tests/test_data_validation.py::test_invalid_timestamp
4.65s setup    tests/test_pipeline.py::test_happy_path_customers
4.50s setup    tests/test_schema_validation.py::test_all_null_non_nullable_column_is_breaking
2.53s setup    tests/test_data_validation.py::test_duplicate_business_event
======================== 82 passed in 64.67s (0:01:04) ========================
```
