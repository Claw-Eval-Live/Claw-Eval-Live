"""CTB_PRODAPP_08 grader -- sprint retro preparation.

Ground truth source: task.yaml reference_solution is empty for this task.
Rubric ground truth derived from fixture data:
  - fixtures/calendar/events.json, fixtures/todo/tasks.json, fixtures/notes/meetings.json

v2.2: WildClawBench mode (operation + analysis).
- Deterministic 55%: tool gate, task completion stats, overdue flags, improvement tracking
- Judge 45%: retro document quality, discussion topics quality
- Fallback: keyword-based scoring when judge unavailable

Ground truth (from fixtures):
  Tasks: 3/6 completed (50%), index optimization overdue (was due 3/28),
  message queue not started (18h), Sprint 11 improvements to track:
  standup 15-min timer, Planning Poker estimation, independent test environment (Chen Lei)
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class Grader(AbstractGrader):
    """Grade sprint retrospective preparation document."""

    _RETRO_DOCUMENT_RUBRIC = """\
Evaluate the quality of the sprint retrospective preparation document (0.0-1.0).

## Ground Truth
- Sprint stats: 3/6 tasks completed (50%)
- Overdue: Index optimization (due 3/28, not completed)
- Risk: Message queue task not started (18h estimate)
- Sprint 11 improvement items tracked: standup 15-min timer, Planning Poker, test environment
- Document should follow retro template: data review, what went well, needs improvement

## Scoring tiers
- 0.9-1.0: Accurate stats; overdue items flagged; improvement tracking with status; structured template
- 0.7-0.8: Stats mostly correct; key risks identified; improvement items mentioned
- 0.5-0.6: Partial stats; some risks; template partially followed
- 0.3-0.4: Minimal stats; vague content
- 0.0-0.2: No meaningful retro document
"""

    _DISCUSSION_RUBRIC = """\
Evaluate the quality of suggested discussion topics for the retrospective (0.0-1.0).

## Expected topics
- Why was index optimization delayed? Root cause and prevention
- Message queue task risk -- should scope be reduced?
- Sprint 11 improvement item follow-up results
- Sprint 12 specific improvements to propose

## Scoring tiers
- 0.9-1.0: All key topics covered with specific context; actionable
- 0.7-0.8: Most topics covered; some specifics
- 0.5-0.6: Partial topic coverage; generic
- 0.3-0.4: Minimal topics
- 0.0-0.2: No discussion topics
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
        clean = all_text.replace(",", "").replace("\uff0c", "")
        lowered = all_text.lower()

        # 1. Tool gate
        tool_penalty = self._tool_gate(dispatches)

        # 2. Deterministic checks (55%)
        det_score = 0.0
        det_score += 0.20 * self._score_data_retrieval(dispatches, audit_data)
        det_score += 0.25 * self._score_completion_stats(clean, all_text)
        det_score += 0.30 * self._score_overdue_risk(all_text)
        det_score += 0.25 * self._score_improvement_tracking(all_text)

        # 3. Judge scoring (45%)
        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            retro_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._RETRO_DOCUMENT_RUBRIC
            ).score
            discussion_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._DISCUSSION_RUBRIC
            ).score
        else:
            retro_score = self._fallback_retro(all_text, clean)
            discussion_score = self._fallback_discussion(all_text)

        # 4. Combine
        completion = tool_penalty * (
            0.55 * det_score
            + 0.25 * retro_score
            + 0.20 * discussion_score
        )

        scores.completion = min(round(completion, 2), 1.0)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len(
            [m for m in messages if m.message.role == "assistant"]
        )
        return scores

    # -- Deterministic helpers --

    def _tool_gate(self, dispatches: list[ToolDispatch]) -> float:
        todo = any(d.tool_name == "todo_list_tasks" and d.response_status < 400
                   for d in dispatches)
        cal = any(d.tool_name in ("calendar_list_events", "calendar_get_event")
                  and d.response_status < 400 for d in dispatches)
        notes = any(d.tool_name in ("notes_list", "notes_get")
                    and d.response_status < 400 for d in dispatches)
        count = sum([todo, cal, notes])
        if count == 0:
            return 0.2
        if count == 1:
            return 0.5
        if count == 2:
            return 0.8
        return 1.0

    def _score_data_retrieval(self, dispatches: list[ToolDispatch],
                              audit_data: dict | None) -> float:
        todo_ok = any(d.tool_name == "todo_list_tasks" and d.response_status < 400
                      for d in dispatches)
        cal_ok = any(d.tool_name == "calendar_list_events" and d.response_status < 400
                     for d in dispatches)
        notes_ok = any(d.tool_name in ("notes_list", "notes_get")
                       and d.response_status < 400 for d in dispatches)
        return sum([todo_ok, cal_ok, notes_ok]) / 3.0

    @staticmethod
    def _has_bounded(text: str, num: str) -> bool:
        return bool(re.search(rf'(?<!\d){re.escape(num)}(?!\d)', text))

    def _score_completion_stats(self, clean: str, text: str) -> float:
        score = 0.0
        if re.search(r"3\s*/\s*6", clean) or "50%" in clean:
            score += 0.6
        elif any(kw in text for kw in ["3\u5b8c\u6210", "\u5b8c\u6210\u4e863"]):
            score += 0.4
        if "\u5b8c\u6210" in text and ("\u672a\u5b8c\u6210" in text or "\u8fdb\u884c\u4e2d" in text):
            score += 0.4
        elif "completed" in text.lower() and ("in progress" in text.lower() or "incomplete" in text.lower()):
            score += 0.4
        return min(score, 1.0)

    def _score_overdue_risk(self, text: str) -> float:
        score = 0.0
        lowered = text.lower()
        # Index optimization overdue
        if "\u7d22\u5f15\u4f18\u5316" in text or "index optim" in lowered:
            if any(kw in text for kw in ["\u903e\u671f", "\u672a\u5b8c\u6210", "\u5ef6\u671f",
                                          "\u8d85\u671f", "3/28", "3\u670828"]) or \
               any(kw in lowered for kw in ["overdue", "delayed", "behind"]):
                score += 0.5
        # Message queue not started risk
        if "\u6d88\u606f\u961f\u5217" in text or "message queue" in lowered:
            if any(kw in text for kw in ["\u98ce\u9669", "\u672a\u5f00\u59cb", "18"]) or \
               any(kw in lowered for kw in ["risk", "not started", "pending"]):
                score += 0.5
        return min(score, 1.0)

    def _score_improvement_tracking(self, text: str) -> float:
        tracked = 0
        lowered = text.lower()
        if "\u7ad9\u4f1a" in text and any(kw in text for kw in ["15\u5206\u949f", "\u8ba1\u65f6"]):
            tracked += 1
        elif "standup" in lowered and "15" in text:
            tracked += 1
        if any(kw in lowered for kw in ["planning poker", "\u4f30\u65f6", "estimation"]):
            tracked += 1
        if "\u6d4b\u8bd5\u73af\u5883" in text or "test environment" in lowered:
            if any(kw in text for kw in ["\u72ec\u7acb", "\u642d\u5efa", "\u9648\u78ca"]) or \
               any(kw in lowered for kw in ["independent", "chen lei", "setup"]):
                tracked += 1
        return tracked / 3.0

    # -- Fallback scorers --

    def _fallback_retro(self, text: str, clean: str) -> float:
        """_fallback_: dev-only keyword scoring for retro document."""
        score = 0.0
        sections = ["\u6570\u636e\u56de\u987e", "\u505a\u5f97\u597d",
                    "\u9700\u6539\u8fdb", "\u6539\u8fdb", "\u4eae\u70b9",
                    "data review", "went well", "improve"]
        score += 0.30 * min(sum(1 for s in sections if s in text or s in text.lower()) / 3, 1.0)
        if re.search(r"3\s*/\s*6", clean) or "50%" in clean:
            score += 0.30
        if "\u903e\u671f" in text or "overdue" in text.lower():
            score += 0.20
        if "Sprint 12" in text:
            score += 0.20
        return min(score, 1.0)

    def _fallback_discussion(self, text: str) -> float:
        """_fallback_: dev-only keyword scoring for discussion topics."""
        score = 0.0
        if any(kw in text for kw in ["\u8ba8\u8bba\u8bae\u9898", "\u5efa\u8bae\u8ba8\u8bba"]) or \
           any(kw in text.lower() for kw in ["discussion topic", "suggested topic"]):
            score += 0.40
        if any(kw in text for kw in ["\u6539\u8fdb", "\u5efa\u8bae", "\u884c\u52a8"]) or \
           any(kw in text.lower() for kw in ["improvement", "action item", "suggestion"]):
            score += 0.30
        if "Sprint 12" in text:
            score += 0.30
        return min(score, 1.0)
