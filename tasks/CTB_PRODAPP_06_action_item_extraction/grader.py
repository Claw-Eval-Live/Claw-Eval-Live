"""CTB_PRODAPP_06 grader -- action item extraction from meeting notes.

Ground truth source: task.yaml reference_solution is empty for this task.
Rubric ground truth derived from fixture data:
  - fixtures/notes/meetings.json
  - fixtures/todo/tasks.json
  - fixtures/calendar/events.json

v2.2: WildClawBench mode (operation + analysis).
- Deterministic 55%: tool gate, action items extracted, missing items identified
- Judge 45%: extraction completeness, gap analysis quality
- Fallback: keyword-based scoring when judge unavailable

Ground truth (from fixtures):
  8 action items across 2 meetings:
  Already in todo: Login module optimization (Li Na), Microservice gateway upgrade (Wang Qiang)
  Missing from todo: Read-write split plan (Zhao Min), E2E test framework (Chen Lei),
    Product roadmap (Zhang Wei), API gateway rate-limit POC (Wang Qiang),
    DB sharding design (Zhao Min), Stress test environment (Chen Lei)
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class Grader(AbstractGrader):
    """Grade action item extraction from meeting notes."""

    _EXTRACTION_RUBRIC = """\
Evaluate the completeness of action item extraction from meeting notes (0.0-1.0).

## Ground Truth
Sprint 12 meeting items (5): login module optimization, gateway upgrade, read-write split,
E2E test framework, product roadmap
Architecture review items (3): API gateway rate-limit POC, DB sharding design, stress test environment

## Scoring tiers
- 0.9-1.0: All 8 action items extracted with correct meeting attribution
- 0.7-0.8: 6-7 items extracted; correct meeting attribution for most
- 0.5-0.6: 4-5 items; partial attribution
- 0.3-0.4: 2-3 items; minimal context
- 0.0-0.2: Fewer than 2 items
"""

    _GAP_ANALYSIS_RUBRIC = """\
Evaluate the quality of todo gap analysis and creation recommendations (0.0-1.0).

## Ground Truth
- Already tracked in todo: login module (Li Na), gateway upgrade (Wang Qiang)
- Missing from todo (must be created):
  - Read-write split plan (Zhao Min)
  - E2E test framework (Chen Lei)
  - Product roadmap (Zhang Wei)
  - API gateway rate-limit POC (Wang Qiang)
  - DB sharding design (Zhao Min)
  - Stress test environment (Chen Lei)

## Scoring tiers
- 0.9-1.0: All 6 missing items identified; assignees and due dates provided; creation list
- 0.7-0.8: 4-5 missing items; most assignees correct; some due dates
- 0.5-0.6: 2-3 missing items; partial details
- 0.3-0.4: 1-2 missing items; minimal details
- 0.0-0.2: No meaningful gap analysis
"""

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
        all_text = self._get_all_assistant_text(messages)
        lowered = all_text.lower()

        # 1. Tool gate
        tool_penalty = self._tool_gate(dispatches)

        # 2. Deterministic checks (55%)
        det_score = 0.0
        det_score += 0.30 * self._score_sprint_items(lowered)
        det_score += 0.25 * self._score_arch_items(lowered)
        det_score += 0.25 * self._score_missing_identified(lowered)
        det_score += 0.20 * self._score_existing_tracked(lowered)

        # 3. Judge scoring (45%)
        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            extraction_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._EXTRACTION_RUBRIC
            ).score
            gap_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._GAP_ANALYSIS_RUBRIC
            ).score
        else:
            extraction_score = self._fallback_extraction(lowered)
            gap_score = self._fallback_gap(lowered)

        # 4. Combine
        completion = tool_penalty * (
            0.55 * det_score
            + 0.20 * extraction_score
            + 0.25 * gap_score
        )

        scores.completion = min(round(completion, 2), 1.0)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len(
            [m for m in messages if m.message.role == "assistant"]
        )
        return scores

    # -- Deterministic helpers --

    def _tool_gate(self, dispatches: list[ToolDispatch]) -> float:
        notes_calls = [d for d in dispatches
                       if d.tool_name in ("notes_list", "notes_get")
                       and d.response_status < 400]
        todo_calls = [d for d in dispatches
                      if d.tool_name == "todo_list_tasks" and d.response_status < 400]
        cal_calls = [d for d in dispatches
                     if d.tool_name in ("calendar_list_events", "calendar_get_event")
                     and d.response_status < 400]
        if not notes_calls and not todo_calls:
            return 0.2
        if not notes_calls or not todo_calls:
            return 0.5
        return 1.0

    def _score_sprint_items(self, lowered: str) -> float:
        """Sprint 12 meeting action items."""
        items = [
            ["\u767b\u5f55\u6a21\u5757", "login module"],
            ["\u7f51\u5173\u5347\u7ea7", "gateway upgrade"],
            ["\u8bfb\u5199\u5206\u79bb", "read-write split", "read write split"],
            ["e2e", "\u81ea\u52a8\u5316\u6d4b\u8bd5", "automated test", "end-to-end"],
            ["\u8def\u7ebf\u56fe", "roadmap", "product roadmap"],
        ]
        found = sum(1 for item in items if any(kw in lowered for kw in item))
        return min(found / 4, 1.0)

    def _score_arch_items(self, lowered: str) -> float:
        """Architecture review meeting items."""
        items = [
            ["\u9650\u6d41", "rate limit", "throttl"],
            ["\u5206\u5e93\u5206\u8868", "sharding", "database shard"],
            ["\u538b\u6d4b\u73af\u5883", "load test", "stress test", "performance test environment"],
        ]
        found = sum(1 for item in items if any(kw in lowered for kw in item))
        return min(found / 2, 1.0)

    def _score_missing_identified(self, lowered: str) -> float:
        """Check that missing items are flagged as not tracked."""
        missing_kws = ["\u65b0\u5efa", "\u7f3a\u5931", "missing", "\u672a\u5f55\u5165",
                       "not tracked", "\u9700\u8981\u521b\u5efa", "not found",
                       "untracked", "need to create", "not yet entered", "not in todo"]
        has_missing_flag = any(kw in lowered for kw in missing_kws)

        # Key missing items
        missing_items = [
            ["\u8bfb\u5199\u5206\u79bb", "read-write split", "read write split"],
            ["e2e", "\u81ea\u52a8\u5316\u6d4b\u8bd5\u6846\u67b6", "test framework"],
            ["\u8def\u7ebf\u56fe", "roadmap"],
        ]
        items_found = sum(1 for item in missing_items if any(kw in lowered for kw in item))

        if has_missing_flag and items_found >= 2:
            return 1.0
        if has_missing_flag and items_found >= 1:
            return 0.7
        if items_found >= 2:
            return 0.5
        if items_found >= 1:
            return 0.3
        return 0.0

    def _score_existing_tracked(self, lowered: str) -> float:
        """Check items already in todo are identified as such."""
        existing_kws = ["\u5df2\u5b58\u5728", "\u5df2\u5f55\u5165", "already tracked",
                        "\u5df2\u6709", "already exist", "already in", "existing task",
                        "already created", "duplicate"]
        return 1.0 if any(kw in lowered for kw in existing_kws) else 0.0

    # -- Fallback scorers --

    def _fallback_extraction(self, lowered: str) -> float:
        """_fallback_: dev-only keyword scoring for extraction."""
        all_items = ["\u767b\u5f55\u6a21\u5757", "login module",
                     "\u7f51\u5173\u5347\u7ea7", "gateway upgrade",
                     "\u8bfb\u5199\u5206\u79bb", "read-write split",
                     "e2e", "\u8def\u7ebf\u56fe", "roadmap",
                     "\u9650\u6d41", "rate limit",
                     "\u5206\u5e93\u5206\u8868", "sharding",
                     "\u538b\u6d4b", "stress test"]
        found = sum(1 for kw in all_items if kw in lowered)
        return min(found / 6, 1.0)

    def _fallback_gap(self, lowered: str) -> float:
        """_fallback_: dev-only keyword scoring for gap analysis."""
        score = 0.0
        if any(kw in lowered for kw in ["missing", "\u7f3a\u5931", "not tracked",
                                         "\u672a\u5f55\u5165"]):
            score += 0.30
        if any(kw in lowered for kw in ["\u521b\u5efa", "create", "add task", "\u65b0\u5efa"]):
            score += 0.25
        if any(kw in lowered for kw in ["\u622a\u6b62", "due", "deadline",
                                         "4\u6708", "april"]):
            score += 0.20
        if any(kw in lowered for kw in ["\u8d75\u654f", "\u9648\u78ca", "\u5f20\u4f1f",
                                         "zhao", "chen", "zhang"]):
            score += 0.25
        return min(score, 1.0)
