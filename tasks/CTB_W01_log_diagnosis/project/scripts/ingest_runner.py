#!/usr/bin/env python3
"""Simple ingest runner used by CTB_W01."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


EXPECTED_REGION = "us-east-1"
EXPECTED_BATCH_SIZE = 200
EXPECTED_REPORT_DIR = "/workspace/output/ingest"
EXPECTED_FORMAT = "json"


def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"Bad env line: {line}")
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip()
    return env


def validate_config(env: dict[str, str]) -> None:
    region = env.get("INGEST_REGION")
    if region != EXPECTED_REGION:
        raise RuntimeError(f"dns lookup failed for ingest.{region}.internal")

    try:
        batch_size = int(env.get("BATCH_SIZE", ""))
    except ValueError as exc:
        raise RuntimeError("batch size must be integer") from exc
    if batch_size != EXPECTED_BATCH_SIZE:
        raise RuntimeError(f"batch size {batch_size} rejected by downstream limit")

    if env.get("REPORT_DIR") != EXPECTED_REPORT_DIR:
        raise RuntimeError("report dir must be /workspace/output/ingest")
    if env.get("EMIT_FORMAT") != EXPECTED_FORMAT:
        raise RuntimeError("emit format must be json")


def build_summary(payload: dict) -> dict:
    ready = 0
    delayed = 0
    bytes_total = 0
    sources: list[str] = []
    for record in payload["records"]:
        status = record["status"]
        if status == "ready":
            ready += 1
            bytes_total += int(record["bytes"])
        elif status == "delayed":
            delayed += 1
        source = record["source"]
        if source not in sources:
            sources.append(source)
    return {
        "window": payload["window"],
        "ready_count": ready,
        "delayed_count": delayed,
        "ready_bytes_total": bytes_total,
        "sources": sources,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--input", required=True)
    args = parser.parse_args()

    env = load_env(Path(args.config))
    validate_config(env)

    payload = json.loads(Path(args.input).read_text())
    summary = build_summary(payload)

    report_dir = Path(env["REPORT_DIR"])
    report_dir.mkdir(parents=True, exist_ok=True)
    output_path = report_dir / "daily_summary.json"
    output_path.write_text(json.dumps(summary, indent=2, sort_keys=True))

    print(f"INGEST_OK {output_path}")


if __name__ == "__main__":
    main()
