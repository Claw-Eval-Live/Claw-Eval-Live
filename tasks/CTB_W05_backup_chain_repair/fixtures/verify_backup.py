#!/usr/bin/env python3
"""Deterministic verification for CTB_W05_backup_chain_repair."""

from __future__ import annotations

import json
from pathlib import Path


INDEX_PATH = Path("/workspace/project/backups/snapshot.index.json")
RESTORE_PROBE_PATH = Path("/workspace/output/backup_restore_probe.json")
REPORT_PATH = Path("/workspace/BACKUP_REPAIR.md")


def main() -> None:
    checks: dict[str, bool] = {}

    try:
        index = json.loads(INDEX_PATH.read_text())
    except Exception:  # noqa: BLE001
        index = {}

    snapshots = {snap.get("snapshot_id"): snap for snap in index.get("snapshots", [])}
    latest = index.get("latest_snapshot_id")

    checks["latest_snapshot_ok"] = latest == "inc_20260312"
    checks["parent_link_ok"] = (snapshots.get("inc_20260312") or {}).get("parent") == "inc_20260311"
    checks["middle_link_ok"] = (snapshots.get("inc_20260311") or {}).get("parent") == "base_20260310"
    checks["base_root_ok"] = (snapshots.get("base_20260310") or {}).get("parent") is None
    checks["manifest_paths_ok"] = all(
        isinstance((snapshots.get(sid) or {}).get("manifest"), str)
        for sid in ["base_20260310", "inc_20260311", "inc_20260312"]
    )

    restore_ok = False
    if RESTORE_PROBE_PATH.exists():
        try:
            probe = json.loads(RESTORE_PROBE_PATH.read_text())
            restore_ok = probe == {
                "latest_snapshot_id": "inc_20260312",
                "restored_files": [
                    "app/config.yml",
                    "data/customers.csv",
                    "exports/latest_summary.txt",
                    "reports/daily_20260311.csv",
                    "reports/daily_20260312.csv",
                ],
                "config_revision": "v2",
                "status": "healthy",
            }
        except Exception:  # noqa: BLE001
            restore_ok = False
    checks["restore_probe_ok"] = restore_ok

    report_keywords_ok = False
    if REPORT_PATH.exists():
        text = REPORT_PATH.read_text().lower()
        must_have = [
            "root cause",
            "根因",
            "snapshot.index.json",
            "inc_20260312",
            "inc_20260311",
            "daily_20260311",
            "hash mismatch",
            "表象",
            "验证",
        ]
        report_keywords_ok = sum(1 for item in must_have if item.lower() in text) >= 5 and len(text) >= 140

    visible_passed = sum(1 for ok in checks.values() if ok)
    result = {
        "visible_total": len(checks),
        "visible_passed": visible_passed,
        "report_keywords_ok": report_keywords_ok,
        "checks": checks,
    }
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
