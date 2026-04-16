# Output Contract

The script must keep its CLI interface unchanged:

```bash
python /workspace/project/data_processor.py --input <input_json> --output <output_json>
```

Input JSON structure:

```json
{
  "date": "2026-03-01",
  "records": [...]
}
```

The output JSON must be an object containing **only** these fields:

```json
{
  "report_date": "2026-03-01",
  "completed_count": 0,
  "pending_count": 0,
  "completed_total_cents": 0,
  "category_totals_cents": {
    "compute": 0
  },
  "owner_sequence": ["alice"]
}
```

Rules:

1. `report_date` comes directly from the input `date` field.
2. Status field compatibility:
   - Either `status` or `state` is accepted.
3. After normalizing the status value, apply the following rules:
   - `completed` / `paid` / `settled` -> count toward `completed_count`
   - `pending` / `queued` / `retry` -> count toward `pending_count`
   - `failed` / `cancelled` -> ignore the record entirely
4. Amount field compatibility:
   - `amount_cents`
   - `amount`
5. When the field is `amount`, the input value may be:
   - `"12.50"`
   - `"$12.50"`
   - `12.5`
   Convert to an integer number of cents.
6. `category` compatibility:
   - `category`
   - `category_name`
   Normalize with `strip + lowercase`.
7. `owner` is normalized with `strip + lowercase`.
8. `owner_sequence` records only the owners that appear in non-ignored records, deduplicated in **first-occurrence order**.
9. `category_totals_cents` aggregates amounts only for records with a completed status, grouped by the normalized category.
10. Do not add any extra fields to the output JSON.
