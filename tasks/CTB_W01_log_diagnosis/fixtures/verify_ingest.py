#!/usr/bin/env python3
"""Deterministic verification for CTB_W01_log_diagnosis."""

from __future__ import annotations

import json
from pathlib import Path


RUNTIME_ENV = Path("/workspace/project/config/runtime.env")
OUTPUT_PATH = Path("/workspace/output/ingest/daily_summary.json")
DIAGNOSIS_PATH = Path("/workspace/DIAGNOSIS.md")

EXPECTED_OUTPUT = {
    "window": "2026-03-18T00:00Z/2026-03-18T06:00Z",
    "ready_count": 3,
    "delayed_count": 1,
    "ready_bytes_total": 2650,
    "sources": ["billing", "ops", "analytics"],
}


def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip()
    return env


def main() -> None:
    env = load_env(RUNTIME_ENV)
    config_ok = (
        env.get("INGEST_REGION") == "us-east-1"
        and env.get("BATCH_SIZE") == "200"
        and env.get("REPORT_DIR") == "/workspace/output/ingest"
        and env.get("EMIT_FORMAT") == "json"
    )

    output_ok = False
    if OUTPUT_PATH.exists():
        try:
            output_ok = json.loads(OUTPUT_PATH.read_text()) == EXPECTED_OUTPUT
        except Exception:  # noqa: BLE001
            output_ok = False

    diagnosis_ok = False
    if DIAGNOSIS_PATH.exists():
        text = DIAGNOSIS_PATH.read_text().lower()
        must_have = [
            "root cause",
            "根因",
            "us-eats-1",
            "us-east-1",
            "batch",
            "表象",
            "fallback",
            "验证",
        ]
        diagnosis_ok = sum(1 for item in must_have if item.lower() in text) >= 4 and len(text) >= 120

    result = {
        "config_ok": config_ok,
        "output_ok": output_ok,
        "diagnosis_ok": diagnosis_ok,
    }
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
