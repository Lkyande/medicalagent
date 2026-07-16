---
name: statistics-reporting
description: Use when defining medical statistics, validating report scope, or generating approved CSV, XLSX, PDF, or ZIP report tasks from imported datasets.
---

# Statistics Reporting

- Convert the request into a structured plan with dataset, metrics, filters, grouping, date range, and formats.
- Require explicit user confirmation before execution.
- Use only approved fields and operators. Never generate or execute arbitrary SQL.
- Apply authorization, minimum cohort size, and re-identification checks before export.
- Escape spreadsheet cells beginning with `=`, `+`, `-`, or `@` in CSV exports.
- Report missing data, exclusions, metric definitions, limitations, and generation time.
- Return Artifact metadata rather than embedding generated files as base64 or data URLs.
