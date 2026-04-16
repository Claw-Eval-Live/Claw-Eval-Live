"""CTB_PRODAPP_01 grader -- calendar conflict resolution.

Ground truth source: task.yaml reference_solution is empty for this task.
Rubric ground truth derived from fixture data:
  - fixtures/calendar/events.json
  - fixtures/todo/tasks.json
  - fixtures/notes/meetings.json

v2.2: WildClawBench mode (operation + analysis).
- Deterministic 55%: tool gate, conflict identification, resolution direction, schedule output
- Judge 45%: conflict analysis quality, resolution plan quality
- Fallback: keyword-based scoring when judge unavailable

Ground truth (from fixtures):
  Conflict 1: Product Planning vs Tech Sharing (Na Li) -- same time slot
  Conflict 2: Client A vs Security Audit (Qiang Wang) -- same time slot
  Resolution: Client meetings take priority; Tech Sharing should be rescheduled
  Wang's Security Audit is urgent due to vulnerability deadline
  Output: adjusted conflict-free schedule with specific time slots
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class Grader(AbstractGrader):
    """Grade calendar conflict resolution with adjusted schedule."""

    # -- Judge rubrics --

    _CONFLICT_ANALYSIS_RUBRIC = """\
Evaluate the quality of calendar conflict identification and analysis (0.0-1.0).

## Ground Truth
Two conflict pairs exist in the calendar:
1. Product Planning Meeting vs Tech Sharing Session -- overlapping time for Na Li
2. Client A Meeting vs Security Audit -- overlapping time for Qiang Wang

The agent should:
- Identify both conflict pairs with specific event names
- Explain WHY they conflict (overlapping time slots, same attendee)
- Reference todo deadlines and notes scheduling rules as context

## Scoring tiers
- 0.9-1.0: Both conflicts identified with event names, time details, and attendee context
- 0.7-0.8: Both conflicts identified with reasonable detail
- 0.5-0.6: One conflict identified with detail, or both mentioned vaguely
- 0.3-0.4: Partial identification; missing key details
- 0.0-0.2: No meaningful conflict identification
"""

    _RESOLUTION_PLAN_RUBRIC = """\
Evaluate the quality of conflict resolution plan and adjusted schedule (0.0-1.0).

## Ground Truth
- Client A meeting should be kept (external client, higher priority)
- Tech Sharing should be moved/postponed (internal, flexible)
- Security Audit should be prioritized for Qiang Wang (urgent vulnerability fix)
- Final schedule should be conflict-free with specific time slots

## Scoring tiers
- 0.9-1.0: Correct priority reasoning for both conflicts; specific rescheduled times; conflict-free final schedule
- 0.7-0.8: Correct resolution direction; rescheduled times present; mostly conflict-free
- 0.5-0.6: Partially correct priority; vague time suggestions
- 0.3-0.4: Mentions resolution but incorrect priorities or no specific times
- 0.0-0.2: No meaningful resolution plan
"""

    # -- Main grading --

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
        final_text = self._get_final_assistant_text(messages)
        all_text = self._get_all_assistant_text(messages)
        lowered = all_text.lower()

        # 1. Tool gate
        tool_penalty = self._tool_gate(dispatches)

        # 2. Deterministic checks (55%)
        det_score = 0.0
        det_score += 0.25 * self._score_conflict_identification(all_text, lowered)
        det_score += 0.25 * self._score_resolution_direction(all_text, lowered)
        det_score += 0.25 * self._score_schedule_output(all_text)
        det_score += 0.25 * self._score_data_retrieval(dispatches)

        # 3. Judge scoring (45%)
        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            conflict_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._CONFLICT_ANALYSIS_RUBRIC
            ).score
            resolution_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._RESOLUTION_PLAN_RUBRIC
            ).score
        else:
            conflict_score = self._fallback_conflict(all_text, lowered)
            resolution_score = self._fallback_resolution(all_text, lowered)

        # 4. Combine
        completion = tool_penalty * (
            0.55 * det_score
            + 0.20 * conflict_score
            + 0.25 * resolution_score
        )

        scores.completion = min(round(completion, 2), 1.0)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len(
            [m for m in messages if m.message.role == "assistant"]
        )
        return scores

    # -- Deterministic helpers --

    @staticmethod
    def _has_bounded(text: str, num: str) -> bool:
        return bool(re.search(rf'(?<!\d){re.escape(num)}(?!\d)', text))

    def _tool_gate(self, dispatches: list[ToolDispatch]) -> float:
        """Penalty multiplier: did the agent read calendar, todo, AND notes?"""
        cal_calls = [d for d in dispatches
                     if d.tool_name in ("calendar_list_events", "calendar_get_event")
                     and d.response_status < 400]
        todo_calls = [d for d in dispatches
                      if d.tool_name == "todo_list_tasks"
                      and d.response_status < 400]
        notes_calls = [d for d in dispatches
                       if d.tool_name in ("notes_list", "notes_get")
                       and d.response_status < 400]
        api_count = sum(1 for c in [cal_calls, todo_calls, notes_calls] if c)
        if api_count == 0:
            return 0.2
        if api_count <= 1:
            return 0.5
        if api_count == 2:
            return 0.8
        return 1.0

    def _score_conflict_identification(self, text: str, lowered: str) -> float:
        """Check both conflict pairs are identified."""
        score = 0.0
        # Conflict 1: Product Planning vs Tech Sharing
        c1_events = ["Product Planning", "Tech Sharing"]
        c1_alt = ["\u4ea7\u54c1\u89c4\u5212", "\u6280\u672f\u5206\u4eab"]
        c1_found = sum(1 for e in c1_events if e.lower() in lowered) + \
                   sum(1 for e in c1_alt if e in text)
        if c1_found >= 2:
            score += 0.5
        elif c1_found >= 1:
            score += 0.2

        # Conflict 2: Client A vs Security Audit
        c2_events = ["Client A", "Security Audit"]
        c2_alt = ["\u5ba2\u6237A", "\u5b89\u5168\u5ba1\u8ba1"]
        c2_found = sum(1 for e in c2_events if e.lower() in lowered) + \
                   sum(1 for e in c2_alt if e in text)
        if c2_found >= 2:
            score += 0.5
        elif c2_found >= 1:
            score += 0.2

        return min(score, 1.0)

    def _score_resolution_direction(self, text: str, lowered: str) -> float:
        """Check resolution priorities are correct."""
        score = 0.0
        # Client meeting should be kept
        client_keep = any(kw in lowered for kw in [
            "keep", "priority", "cannot move", "do not move",
            "\u4fdd\u7559", "\u4f18\u5148", "\u4e0d\u53ef\u79fb\u52a8"
        ]) and any(kw in lowered for kw in ["client", "\u5ba2\u6237"])
        if client_keep:
            score += 0.35

        # Tech Sharing should be moved
        tech_move = any(kw in lowered for kw in [
            "postpone", "move", "adjust", "reschedule", "defer",
            "\u5ef6\u540e", "\u79fb\u52a8", "\u8c03\u6574", "\u63a8\u8fdf", "\u6539\u671f"
        ]) and any(kw in lowered for kw in ["tech sharing", "\u6280\u672f\u5206\u4eab"])
        if tech_move:
            score += 0.35

        # Security urgency for Wang
        sec_urgent = any(kw in lowered for kw in [
            "security", "vulnerability", "critical", "urgent",
            "\u5b89\u5168", "\u6f0f\u6d1e", "\u7d27\u6025"
        ]) and any(kw in lowered for kw in ["wang", "\u738b\u5f3a"])
        if sec_urgent:
            score += 0.30

        return min(score, 1.0)

    def _score_schedule_output(self, text: str) -> float:
        """Check final conflict-free schedule is presented."""
        score = 0.0
        lowered = text.lower()
        # Schedule keywords
        if any(kw in lowered for kw in [
            "adjusted", "conflict-free", "final schedule", "new schedule",
            "proposed schedule",
            "\u8c03\u6574\u540e", "\u65e0\u51b2\u7a81", "\u6700\u7ec8\u65e5\u7a0b",
            "\u65b0\u65e5\u7a0b", "\u5efa\u8bae\u65e5\u7a0b"
        ]):
            score += 0.5

        # Has specific times
        time_refs = re.findall(r"\d{1,2}:\d{2}", text)
        if len(time_refs) >= 4:
            score += 0.5
        elif len(time_refs) >= 2:
            score += 0.25

        return min(score, 1.0)

    def _score_data_retrieval(self, dispatches: list[ToolDispatch]) -> float:
        """Check all three data sources were read."""
        cal_ok = any(d.tool_name in ("calendar_list_events", "calendar_get_event")
                     and d.response_status < 400 for d in dispatches)
        todo_ok = any(d.tool_name == "todo_list_tasks"
                      and d.response_status < 400 for d in dispatches)
        notes_ok = any(d.tool_name in ("notes_list", "notes_get")
                       and d.response_status < 400 for d in dispatches)
        return sum([cal_ok, todo_ok, notes_ok]) / 3.0

    # -- Fallback scorers --

    def _fallback_conflict(self, text: str, lowered: str) -> float:
        """_fallback_: dev-only keyword scoring for conflict analysis."""
        score = 0.0
        if any(kw in lowered for kw in ["conflict", "overlap", "\u51b2\u7a81", "\u91cd\u53e0"]):
            score += 0.25
        events = ["Product Planning", "Tech Sharing", "Client A", "Security Audit",
                  "\u4ea7\u54c1\u89c4\u5212", "\u6280\u672f\u5206\u4eab",
                  "\u5ba2\u6237A", "\u5b89\u5168\u5ba1\u8ba1"]
        found = sum(1 for e in events if e.lower() in lowered or e in text)
        score += 0.50 * min(found / 4, 1.0)
        if any(n in lowered for n in ["na li", "li na", "\u674e\u5a1c"]):
            score += 0.10
        if any(n in lowered for n in ["wang", "\u738b\u5f3a"]):
            score += 0.10
        return min(score, 1.0)

    def _fallback_resolution(self, text: str, lowered: str) -> float:
        """_fallback_: dev-only keyword scoring for resolution plan."""
        score = 0.0
        if any(kw in lowered for kw in ["keep", "priority", "\u4fdd\u7559", "\u4f18\u5148"]):
            score += 0.20
        if any(kw in lowered for kw in ["move", "reschedule", "postpone",
                                         "\u8c03\u6574", "\u63a8\u8fdf"]):
            score += 0.20
        time_refs = re.findall(r"\d{1,2}:\d{2}", text)
        if len(time_refs) >= 4:
            score += 0.30
        elif len(time_refs) >= 2:
            score += 0.15
        if any(kw in lowered for kw in ["adjusted", "conflict-free", "\u65e0\u51b2\u7a81"]):
            score += 0.15
        if any(kw in lowered for kw in ["urgent", "security", "\u7d27\u6025", "\u5b89\u5168"]):
            score += 0.15
        return min(score, 1.0)
