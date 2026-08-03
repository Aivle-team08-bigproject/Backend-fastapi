# Data Processing Agent

The LLM planning agent receives the approved selection plan and creates a validated `ProcessingPlan`.
It never receives actual selected rows. After the plan is validated, the query layer loads rows and the
deterministic executor applies only allowlisted operations in the planned order.

Set `DATA_ANONYMIZATION_KEY` before processing direct identifiers such as `customer_id`, `email`, or `phone`.
Direct calls without a `processing_plan` retain the legacy deterministic defaults for compatibility.

The output contains CSV/API/visualization/report artifacts together with `processing_explanation` and
`quality_report`. CSV content is UTF-8 with BOM and is returned as Base64.
