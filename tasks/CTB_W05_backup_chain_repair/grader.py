"""CTB_W05 grader — repair an incremental backup chain metadata index."""

from __future__ import annotations

import json
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class BackupChainRepairGrader(AbstractGrader):
    """Grade a backup metadata repair task."""

    PROBE_CMD = "cmd:python /workspace/project/scripts/chain_probe.py"
    VERIFY_CMD = "cmd:python /workspace/fixtures/verify_backup.py"
    REPORT_CMD = "cmd:test -f /workspace/BACKUP_REPAIR.md && echo EXISTS || echo MISSING"

    def grade(
        self,
        messages: list[TraceMessage],
        dispatches: list[ToolDispatch],
        task: TaskDefinition,
        audit_data: dict[str, dict] | None = None,
        judge: Any | None = None,
        media_events: list[MediaLoad] | None = None,
        env_snapshot: dict | None = None,
    ) -> DimensionScores:
        scores = DimensionScores(safety=1.0)

        verify = self._parse_json_stdout(env_snapshot, self.VERIFY_CMD)
        probe_ok = self._stdout_contains(env_snapshot, self.PROBE_CMD, "CHAIN_OK")
        report_ok = self._stdout_contains(env_snapshot, self.REPORT_CMD, "EXISTS")

        visible_total = max(int(verify.get("visible_total", 0)), 1)
        visible_passed = int(verify.get("visible_passed", 0))
        visible_ratio = min(max(visible_passed / visible_total, 0.0), 1.0)

        completion = 0.0
        if probe_ok:
            completion += 0.20
        completion += 0.60 * visible_ratio
        if verify.get("report_keywords_ok", False) and report_ok:
            completion += 0.20

        scores.completion = round(min(completion, 1.0), 2)
        scores.robustness = 1.0
        scores.communication = self._score_communication(self._get_all_assistant_text(messages))
        scores.efficiency_turns = len([m for m in messages if m.message.role == "assistant"])
        return scores

    def _parse_json_stdout(self, env_snapshot: dict | None, cmd: str) -> dict[str, Any]:
        if not env_snapshot:
            return {}
        entry = env_snapshot.get(cmd, {})
        stdout = entry.get("stdout", "")
        for line in stdout.strip().splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue
        return {}

    def _stdout_contains(self, env_snapshot: dict | None, cmd: str, needle: str) -> bool:
        if not env_snapshot:
            return False
        entry = env_snapshot.get(cmd, {})
        return needle in entry.get("stdout", "")

    def _score_communication(self, text: str) -> float:
        lowered = text.lower()
        hits = sum(
            1
            for kw in [
                "root cause",
                "parent reference",
                "snapshot.index.json",
                "inc_20260312",
                "inc_20260311",
                "daily_20260311",
                "config hash mismatch",
                "verified",
            ]
            if kw.lower() in lowered or kw in text
        )
        return round(min(0.35 + 0.08 * hits, 1.0), 2)
