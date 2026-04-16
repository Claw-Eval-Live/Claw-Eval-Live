"""CTB_W03 grader — repair a legacy processor and regenerate daily summaries."""

from __future__ import annotations

import json
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class ScriptDebugGrader(AbstractGrader):
    """Grade the repaired data processor task using sandbox verification."""

    VERIFY_CMD = "cmd:python /workspace/fixtures/verify_outputs.py"
    SYNTAX_CMD = (
        "cmd:python -m py_compile /workspace/project/data_processor.py "
        "2>&1 && echo SYNTAX_OK || echo SYNTAX_ERR"
    )

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
        syntax_ok = self._syntax_ok(env_snapshot)

        visible_total = max(int(verify.get("visible_total", 0)), 1)
        visible_passed = int(verify.get("visible_passed", 0))
        visible_ratio = min(max(visible_passed / visible_total, 0.0), 1.0)

        completion = 0.0
        if verify.get("script_exists", False):
            completion += 0.05
        if syntax_ok:
            completion += 0.10
        completion += 0.55 * visible_ratio
        if verify.get("hidden_passed", False):
            completion += 0.15
        if verify.get("changelog_ok", False):
            completion += 0.15

        if not syntax_ok:
            completion = min(completion, 0.45)
        if visible_passed == 0:
            completion = min(completion, 0.35)

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

    def _syntax_ok(self, env_snapshot: dict | None) -> bool:
        if not env_snapshot:
            return False
        entry = env_snapshot.get(self.SYNTAX_CMD, {})
        stdout = entry.get("stdout", "")
        return "SYNTAX_OK" in stdout

    def _score_communication(self, text: str) -> float:
        text_lower = text.lower()
        keywords = [
            "root cause",
            "fix",
            "verify",
            "status",
            "state",
            "amount",
            "category",
            "owner",
            "dedup",
            "contract",
            "summary",
        ]
        found = sum(1 for kw in keywords if kw in text_lower or kw in text)
        keyword_score = min(found / 5, 1.0)
        return round(keyword_score, 2)
