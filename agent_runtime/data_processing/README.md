# Data Processing Agent

The agent receives actual selected rows through `selected_rows` and applies deterministic processing rules.
It does not send raw rows to an LLM.

Set `DATA_ANONYMIZATION_KEY` before processing direct identifiers such as `customer_id`, `email`, or `phone`.
Optional `column_policies` can declare `data_type`, `missing_strategy`, and `sensitivity` per column.

The output contains CSV/API/visualization/report artifacts together with `processing_explanation` and
`quality_report`. CSV content is UTF-8 with BOM and is returned as Base64.
