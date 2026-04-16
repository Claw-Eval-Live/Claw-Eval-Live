"""CTB_W01 grader — diagnose root cause from logs and repair runtime config."""

from __future__ import annotations

import json
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class LogDiagnosisGrader(AbstractGrader):
    """Grade the ingest log diagnosis task using deterministic verification."""

    VERIFY_CMD = "cmd:python /workspace/fixtures/verify_ingest.py"
    RUN_CMD = "cmd:bash /workspace/project/scripts/run_ingest.sh"

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

        verify = self._parse_verify_json(env_snapshot)
        rerun_ok = self._rerun_ok(env_snapshot)

        completion = 0.0
        if rerun_ok:
            completion += 0.35
        if verify.get("output_ok", False):
            completion += 0.30
        if verify.get("config_ok", False):
            completion += 0.15
        if verify.get("diagnosis_ok", False):
            completion += 0.20

        if not rerun_ok:
            completion = min(completion, 0.45)
        if not verify.get("config_ok", False):
            completion = min(completion, 0.55)

        scores.completion = round(min(completion, 1.0), 2)
        scores.robustness = self.compute_robustness(dispatches)
        scores.communication = self._score_communication(self._get_all_assistant_text(messages))
        scores.efficiency_turns = len(
            [m for m in messages if m.message.role == "assistant"]
        )
        return scores

    def _parse_verify_json(self, env_snapshot: dict | None) -> dict[str, Any]:
        if not env_snapshot:
            return {}
        entry = env_snapshot.get(self.VERIFY_CMD, {})
        stdout = entry.get("stdout", "")
        for line in stdout.strip().splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue
        return {}

    def _rerun_ok(self, env_snapshot: dict | None) -> bool:
        if not env_snapshot:
            return False
        entry = env_snapshot.get(self.RUN_CMD, {})
        stdout = entry.get("stdout", "")
        return "INGEST_OK" in stdout

    def _score_communication(self, text: str) -> float:
        keywords = [
            "root cause",
            "symptom",
            "region",
            "batch",
            "fallback",
            "log",
            "verify",
            "runtime.env",
        ]
        found = sum(1 for kw in keywords if kw.lower() in text.lower())
        keyword_score = min(found / 5, 1.0)
        return round(keyword_score, 2)
