#!/usr/bin/env python3
"""Legacy daily data processor.

Known issue: this version still assumes the old input schema.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


COMPLETED_STATUSES = {"completed"}
PENDING_STATUSES = {"pending"}
IGNORED_STATUSES = {"failed"}


def load_payload(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def normalize_status(record: dict) -> str:
    return record["status"].strip().lower()


def normalize_category(record: dict) -> str:
    return record["category"].strip().lower()


def normalize_owner(record: dict) -> str:
    return record["owner"].strip()


def parse_amount_cents(record: dict) -> int:
    raw = record["amount_cents"]
    return int(raw)


def build_summary(payload: dict) -> dict:
    records = payload["records"]
    category_totals: dict[str, int] = {}
    owner_sequence: list[str] = []
    completed_count = 0
    pending_count = 0
    completed_total_cents = 0

    for record in records:
        status = normalize_status(record)
        if status in IGNORED_STATUSES:
            continue

        category = normalize_category(record)
        owner = normalize_owner(record)
        amount_cents = parse_amount_cents(record)

        if owner and owner not in owner_sequence:
            owner_sequence.append(owner)

        if status in COMPLETED_STATUSES:
            completed_count += 1
            completed_total_cents += amount_cents
            category_totals[category] = category_totals.get(category, 0) + amount_cents
        elif status in PENDING_STATUSES:
            pending_count += 1

    return {
        "report_date": payload["date"],
        "completed_count": completed_count,
        "pending_count": pending_count,
        "completed_total_cents": completed_total_cents,
        "category_totals_cents": category_totals,
        "owner_sequence": owner_sequence,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    payload = load_payload(args.input)
    summary = build_summary(payload)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w") as f:
        json.dump(summary, f, indent=2, sort_keys=True)


if __name__ == "__main__":
    main()
