"""CTB_OPS_02 grader -- meeting room utilization analysis.

Ground truth source: fixtures/calendar/events.json

v2.2: WildClawBench mode (data analysis + optimization).
- Deterministic 55%: tool gate, room coverage, utilization rates, seat waste
- Judge 45%: analysis accuracy, optimization recommendations
- Fallback: keyword-based scoring when judge unavailable

Ground truth:
  Room A: 4 bookings, ~4.75h, utilization ~11.875% (of 40h weekly)
  Room B: 2 bookings, ~2.5h, utilization ~6.25%
  Room C: 2 bookings, ~2.5h, utilization ~6.25%
  Low seat utilization meetings: Frontend Tech Talk, 1-on-1 Performance, Bug Fix
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class Grader(AbstractGrader):
    """Grade meeting room utilization analysis."""

    _ANALYSIS_RUBRIC = """\
Evaluate the accuracy of per-room utilization analysis (0.0-1.0).

## Ground Truth (March 23-27, 40h available per room per week)
- Room A: 4 bookings, ~4.75 hours, utilization ~11.9%
- Room B: 2 bookings, ~2.5 hours, utilization ~6.25%
- Room C: 2 bookings, ~2.5 hours, utilization ~6.25%
- Low seat utilization meetings (below 50%): Frontend Tech Talk (large room, few attendees),
  1-on-1 Performance review (large room, 2 people), Bug Fix session (large room, small team)

## Scoring tiers
- 0.9-1.0: All 3 rooms with correct booking count, hours, and utilization rate; waste meetings identified
- 0.7-0.8: All rooms covered; most numbers close; waste partially identified
- 0.5-0.6: 2+ rooms; some numbers correct
- 0.3-0.4: 1 room correct
- 0.0-0.2: No meaningful analysis
"""

    _OPTIMIZATION_RUBRIC = """\
Evaluate the quality of optimization recommendations (0.0-1.0).

## Expected recommendations
- Use smaller rooms for small meetings (avoid booking large conference rooms for 2-3 people)
- Consider combining underutilized time slots
- Flag specific wasteful bookings by name
- Suggest room assignment policies based on meeting size

## Scoring tiers
- 0.9-1.0: Specific wasteful meetings named; room-size matching recommendations; policy suggestions
- 0.7-0.8: General optimization ideas; some specific meetings flagged
- 0.5-0.6: Mentions optimization but vague
- 0.3-0.4: Minimal suggestions
- 0.0-0.2: No recommendations
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
        lower = all_text.lower()

        tool_penalty = self._tool_gate(dispatches)

        det_score = 0.0
        det_score += 0.30 * self._score_room_coverage(all_text)
        det_score += 0.35 * self._score_utilization_data(all_text)
        det_score += 0.35 * self._score_seat_waste(all_text, lower)

        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            analysis_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._ANALYSIS_RUBRIC
            ).score
            opt_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._OPTIMIZATION_RUBRIC
            ).score
        else:
            analysis_score = self._fallback_analysis(all_text, lower)
            opt_score = self._fallback_optimization(lower)

        completion = tool_penalty * (
            0.55 * det_score
            + 0.20 * analysis_score
            + 0.25 * opt_score
        )

        scores.completion = min(round(completion, 2), 1.0)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len(
            [m for m in messages if m.message.role == "assistant"]
        )
        return scores

    def _tool_gate(self, dispatches: list[ToolDispatch]) -> float:
        cal = [d for d in dispatches if d.tool_name in ("calendar_list_events", "calendar_get_event") and d.response_status < 400]
        if not cal:
            return 0.2
        return 1.0

    def _score_room_coverage(self, all_text: str) -> float:
        rooms = ["Room A", "Room B", "Room C"]
        found = sum(1 for r in rooms if r in all_text or r.lower() in all_text.lower())
        return found / len(rooms)

    def _score_utilization_data(self, all_text: str) -> float:
        score = 0.0
        # Room A: 4 bookings
        if re.search(r'(?:Room\s*A|room\s*a).{0,200}4\s*(?:times|bookings|sessions|meetings)', all_text, re.IGNORECASE):
            score += 0.33
        # Room A utilization ~11-12%
        if re.search(r'1[12]\.?\d*%', all_text):
            score += 0.33
        # Room B/C utilization ~6.25%
        if re.search(r'6\.25%|6\.\d%', all_text):
            score += 0.34
        return min(score, 1.0)

    def _score_seat_waste(self, all_text: str, lower: str) -> float:
        waste_kw = ["waste", "underutiliz", "low utiliz", "below 50%", "seat utilization"]
        waste_score = 0.0
        if any(kw in lower for kw in waste_kw):
            waste_score += 0.4
        meetings = ["Frontend Tech Talk", "1-on-1 Performance", "Bug Fix"]
        found = sum(1 for m in meetings if m in all_text)
        waste_score += 0.6 * min(found / 2, 1.0)
        return min(waste_score, 1.0)

    def _fallback_analysis(self, all_text: str, lower: str) -> float:
        """_fallback_: dev-only."""
        score = 0.0
        rooms = ["room a", "room b", "room c"]
        score += 0.30 * min(sum(1 for r in rooms if r in lower) / 2, 1.0)
        if re.search(r'\d+\.?\d*%', all_text):
            score += 0.30
        if any(kw in lower for kw in ["booking", "utilization", "hour"]):
            score += 0.20
        if "|" in all_text and "---" in all_text:
            score += 0.20
        return min(score, 1.0)

    def _fallback_optimization(self, lower: str) -> float:
        """_fallback_: dev-only."""
        score = 0.0
        rec_kw = ["recommend", "suggestion", "optimi", "adjust", "improv",
                    "smaller room", "appropriate room"]
        found = sum(1 for kw in rec_kw if kw in lower)
        return min(found / 2, 1.0)
