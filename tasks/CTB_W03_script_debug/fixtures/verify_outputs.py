#!/usr/bin/env python3
"""Hidden verification for CTB_W03_script_debug."""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path


SCRIPT_PATH = Path("/workspace/project/data_processor.py")
OUTPUT_DIR = Path("/workspace/output")
CHANGELOG_PATH = Path("/workspace/CHANGELOG.md")

VISIBLE_EXPECTED = {
    "day_01": {
        "report_date": "2026-03-01",
        "completed_count": 2,
        "pending_count": 1,
        "completed_total_cents": 1500,
        "category_totals_cents": {
            "compute": 1500,
        },
        "owner_sequence": ["alice", "bob"],
    },
    "day_02": {
        "report_date": "2026-03-02",
        "completed_count": 2,
        "pending_count": 1,
        "completed_total_cents": 700,
        "category_totals_cents": {
            "compute": 200,
            "support": 500,
        },
        "owner_sequence": ["nina", "li"],
    },
    "day_03": {
        "report_date": "2026-03-03",
        "completed_count": 2,
        "pending_count": 1,
        "completed_total_cents": 549,
        "category_totals_cents": {
            "compute": 450,
            "storage": 99,
        },
        "owner_sequence": ["zoe", "ken"],
    },
}

HIDDEN_PAYLOAD = {
    "date": "2026-03-04",
    "records": [
        {
            "id": "tx-4001",
            "state": "paid",
            "amount": "3.40",
            "category_name": "Compute",
            "owner": " Erin ",
        },
        {
            "id": "tx-4002",
            "status": "queued",
            "amount_cents": "200",
            "category": "storage ",
            "owner": "erin",
        },
        {
            "id": "tx-4003",
            "status": "completed",
            "amount_cents": 650,
            "category": "storage",
            "owner": "Kai",
        },
        {
            "id": "tx-4004",
            "state": "failed",
            "amount": "10.00",
            "category": "network",
            "owner": "nobody",
        },
    ],
}

HIDDEN_EXPECTED = {
    "report_date": "2026-03-04",
    "completed_count": 2,
    "pending_count": 1,
    "completed_total_cents": 990,
    "category_totals_cents": {
        "compute": 340,
        "storage": 650,
    },
    "owner_sequence": ["erin", "kai"],
}


def _load_json(path: Path) -> dict:
    with path.open() as f:
        return json.load(f)


def _visible_case_result(day_name: str) -> tuple[bool, str]:
    output_path = OUTPUT_DIR / f"{day_name}_summary.json"
    if not output_path.exists():
        return False, "missing_output"
    try:
        actual = _load_json(output_path)
    except Exception as exc:  # noqa: BLE001
        return False, f"invalid_json:{exc}"
    expected = VISIBLE_EXPECTED[day_name]
    if actual != expected:
        return False, f"mismatch:{actual}"
    return True, "ok"


def _run_hidden_case() -> tuple[bool, str]:
    if not SCRIPT_PATH.exists():
        return False, "script_missing"
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_dir_path = Path(tmp_dir)
        input_path = tmp_dir_path / "hidden_input.json"
        output_path = tmp_dir_path / "hidden_output.json"
        input_path.write_text(json.dumps(HIDDEN_PAYLOAD))
        proc = subprocess.run(
            [
                "python",
                str(SCRIPT_PATH),
                "--input",
                str(input_path),
                "--output",
                str(output_path),
            ],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            return False, f"returncode:{proc.returncode}:{proc.stderr.strip()[:200]}"
        try:
            actual = _load_json(output_path)
        except Exception as exc:  # noqa: BLE001
            return False, f"hidden_invalid_json:{exc}"
        if actual != HIDDEN_EXPECTED:
            return False, f"hidden_mismatch:{actual}"
    return True, "ok"


def _check_changelog() -> tuple[bool, str]:
    if not CHANGELOG_PATH.exists():
        return False, "missing_changelog"
    text = CHANGELOG_PATH.read_text().strip()
    if len(text) < 80:
        return False, "too_short"
    keywords = [
        "根因",
        "修复",
        "验证",
        "root cause",
        "fix",
        "validate",
        "status",
        "amount",
        "category",
    ]
    hit_count = sum(1 for keyword in keywords if keyword.lower() in text.lower())
    if hit_count < 3:
        return False, "keyword_miss"
    return True, "ok"


def main() -> None:
    visible_passed = 0
    details: dict[str, str] = {}
    for day_name in ["day_01", "day_02", "day_03"]:
        passed, note = _visible_case_result(day_name)
        details[day_name] = note
        if passed:
            visible_passed += 1

    hidden_passed, hidden_note = _run_hidden_case()
    changelog_ok, changelog_note = _check_changelog()

    result = {
        "script_exists": SCRIPT_PATH.exists(),
        "visible_passed": visible_passed,
        "visible_total": 3,
        "visible_exact_match": visible_passed == 3,
        "hidden_passed": hidden_passed,
        "changelog_ok": changelog_ok,
        "details": {
            **details,
            "hidden": hidden_note,
            "changelog": changelog_note,
        },
    }
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
