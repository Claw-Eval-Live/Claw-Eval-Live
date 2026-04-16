"""CTB_W06 grader — repair a stale local full-stack dev configuration."""

from __future__ import annotations

import json
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class FullstackDevRepairGrader(AbstractGrader):
    """Grade the full-stack local dev repair task using deterministic verification."""

    VERIFY_CMD = "cmd:python /workspace/fixtures/verify_dev_stack.py"
    RUN_CMD = "cmd:python /workspace/project/scripts/check_dev_stack.py"

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
        if verify.get("backend_ok", False):
            completion += 0.18
        if verify.get("frontend_ok", False):
            completion += 0.12
        if verify.get("proxy_ok", False):
            completion += 0.18
        if verify.get("status_ok", False):
            completion += 0.07
        if verify.get("doc_ok", False):
            completion += 0.10

        if not rerun_ok:
            completion = min(completion, 0.45)
        if not verify.get("backend_ok", False):
            completion = min(completion, 0.55)
        if not verify.get("proxy_ok", False):
            completion = min(completion, 0.65)

        scores.completion = round(min(completion, 1.0), 2)
        scores.robustness = self.compute_robustness(dispatches)
        scores.communication = self._score_communication(self._get_all_assistant_text(messages))
        scores.efficiency_turns = len([m for m in messages if m.message.role == "assistant"])
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
        return "DEV_STACK_OK" in stdout

    def _score_communication(self, text: str) -> float:
        keywords = [
            "root cause",
            "9001",
            "9101",
            "api/v2",
            "/api",
            "local",
            "prod",
            "proxy",
            "verified",
            "verify",
        ]
        found = sum(1 for kw in keywords if kw.lower() in text.lower())
        keyword_score = min(found / 6, 1.0)
        return round(keyword_score, 2)
