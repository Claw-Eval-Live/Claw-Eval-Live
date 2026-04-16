"""CTB_PRODAPP_10 grader -- project timeline adjustment.

Ground truth source: task.yaml reference_solution is empty for this task.
Rubric ground truth derived from fixture data:
  - fixtures/todo/tasks.json, fixtures/calendar/events.json

v2.2: WildClawBench mode (operation + analysis).
- Deterministic 55%: tool gate, delay recognition, cascading adjustments, milestone conflict
- Judge 45%: timeline analysis quality, critical path assessment
- Fallback: keyword-based scoring when judge unavailable

Ground truth (from fixtures):
  2-day delay in backend API propagates:
  - Frontend page: 4/10 -> ~4/12
  - Integration test: 4/14 -> ~4/16
  - UAT: 4/18 -> ~4/20
  - Milestone meeting 4/15 may need adjustment
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class Grader(AbstractGrader):
    """Grade project timeline adjustment analysis."""

    _TIMELINE_RUBRIC = """\
Evaluate the accuracy of timeline delay analysis and adjusted dates (0.0-1.0).

## Ground Truth
A 2-day delay in backend API task cascades to:
- Frontend page: original 4/10 -> adjusted ~4/12
- Integration test: original 4/14 -> adjusted ~4/16
- UAT acceptance: original 4/18 -> adjusted ~4/20
- Milestone review meeting on 4/15 may need to be rescheduled

## Scoring tiers
- 0.9-1.0: All 3 cascading adjustments with correct new dates; milestone conflict noted
- 0.7-0.8: 2-3 adjustments correct; milestone awareness
- 0.5-0.6: Delay recognized; 1-2 adjustments; partial cascade
- 0.3-0.4: Delay mentioned but no concrete adjusted dates
- 0.0-0.2: No timeline analysis
"""

    _CRITICAL_PATH_RUBRIC = """\
Evaluate the quality of critical path analysis and recommendations (0.0-1.0).

## Expected analysis
- Identify the dependency chain: backend API -> frontend -> integration test -> UAT
- Explain 2-day delay propagation mechanism
- Suggest mitigation (parallel work, scope reduction, resource reallocation)
- Note impact on milestone meeting schedule

## Scoring tiers
- 0.9-1.0: Full dependency chain identified; delay mechanism explained; actionable mitigation
- 0.7-0.8: Key dependencies shown; some mitigation suggestions
- 0.5-0.6: Partial chain; generic suggestions
- 0.3-0.4: Mentions delay but no chain analysis
- 0.0-0.2: No critical path analysis
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
        det_score += 0.20 * self._score_delay_recognition(lowered)
        det_score += 0.25 * self._score_frontend_adjustment(lowered)
        det_score += 0.20 * self._score_integration_uat(lowered)
        det_score += 0.20 * self._score_milestone_conflict(lowered)
        det_score += 0.15 * self._score_data_retrieval(dispatches)

        # 3. Judge scoring (45%)
        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            timeline_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._TIMELINE_RUBRIC
            ).score
            critical_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._CRITICAL_PATH_RUBRIC
            ).score
        else:
            timeline_score = self._fallback_timeline(lowered)
            critical_score = self._fallback_critical(lowered)

        # 4. Combine
        completion = tool_penalty * (
            0.55 * det_score
            + 0.25 * timeline_score
            + 0.20 * critical_score
        )

        scores.completion = min(round(completion, 2), 1.0)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len(
            [m for m in messages if m.message.role == "assistant"]
        )
        return scores

    # -- Deterministic helpers --

    def _tool_gate(self, dispatches: list[ToolDispatch]) -> float:
        todo = any(d.tool_name in ("todo_list_tasks", "todo_update_task")
                   and d.response_status < 400 for d in dispatches)
        cal = any(d.tool_name in ("calendar_list_events", "calendar_get_event")
                  and d.response_status < 400 for d in dispatches)
        if not todo and not cal:
            return 0.2
        if not todo or not cal:
            return 0.5
        return 1.0

    def _score_data_retrieval(self, dispatches: list[ToolDispatch]) -> float:
        todo = any(d.tool_name in ("todo_list_tasks", "todo_update_task")
                   and d.response_status < 400 for d in dispatches)
        cal = any(d.tool_name in ("calendar_list_events", "calendar_get_event")
                  and d.response_status < 400 for d in dispatches)
        return sum([todo, cal]) / 2.0

    def _score_delay_recognition(self, lowered: str) -> float:
        delay_kws = ["2\u5929", "2\u65e5", "\u4e24\u5929", "\u5ef6\u671f",
                     "delay", "\u63a8\u8fdf", "2 day", "two day", "2-day",
                     "behind schedule", "slipped"]
        return 1.0 if any(kw in lowered for kw in delay_kws) else 0.0

    def _score_frontend_adjustment(self, lowered: str) -> float:
        frontend_kws = ["04-12", "4\u670812", "4-12", "\u524d\u7aef\u9875\u9762",
                        "april 12", "apr 12", "front-end", "frontend page"]
        return 1.0 if any(kw in lowered for kw in frontend_kws) else 0.0

    def _score_integration_uat(self, lowered: str) -> float:
        score = 0.0
        int_kws = ["04-16", "4\u670816", "4-16", "\u8054\u8c03",
                    "april 16", "apr 16", "integration test"]
        if any(kw in lowered for kw in int_kws):
            score += 0.5
        uat_kws = ["04-20", "4\u670820", "4-20", "\u9a8c\u6536",
                    "april 20", "apr 20", "uat", "user acceptance"]
        if any(kw in lowered for kw in uat_kws):
            score += 0.5
        return score

    def _score_milestone_conflict(self, lowered: str) -> float:
        ms_kws = ["\u91cc\u7a0b\u7891", "milestone", "4\u670815", "04-15",
                  "\u68c0\u67e5", "\u98ce\u9669", "risk",
                  "april 15", "apr 15", "review meeting", "checkpoint"]
        return 1.0 if any(kw in lowered for kw in ms_kws) else 0.0

    # -- Fallback scorers --

    def _fallback_timeline(self, lowered: str) -> float:
        """_fallback_: dev-only scoring."""
        score = 0.0
        if any(kw in lowered for kw in ["delay", "\u5ef6\u671f", "\u63a8\u8fdf"]):
            score += 0.25
        dates = ["4/12", "4/16", "4/20", "04-12", "04-16", "04-20"]
        score += 0.50 * min(sum(1 for d in dates if d in lowered) / 2, 1.0)
        if any(kw in lowered for kw in ["milestone", "\u91cc\u7a0b\u7891"]):
            score += 0.25
        return min(score, 1.0)

    def _fallback_critical(self, lowered: str) -> float:
        """_fallback_: dev-only scoring."""
        score = 0.0
        if any(kw in lowered for kw in ["critical path", "\u5173\u952e\u8def\u5f84",
                                         "dependency", "\u4f9d\u8d56"]):
            score += 0.40
        if any(kw in lowered for kw in ["\u7f13\u89e3", "mitigat", "\u5efa\u8bae",
                                         "recommend"]):
            score += 0.30
        if any(kw in lowered for kw in ["\u5e76\u884c", "parallel", "\u8d44\u6e90",
                                         "resource"]):
            score += 0.30
        return min(score, 1.0)
