"""CTB_PRODAPP_07 grader -- weekly planning for team (April 6-10).

Ground truth source: task.yaml reference_solution is empty for this task.
Rubric ground truth derived from fixture data:
  - fixtures/calendar/events.json, fixtures/todo/tasks.json, fixtures/notes/meetings.json

v2.2: WildClawBench mode (operation + analysis).
- Deterministic 55%: tool gate, member coverage, risk identification, daily schedule
- Judge 45%: planning quality, risk analysis depth
- Fallback: keyword-based scoring when judge unavailable

Ground truth (from fixtures):
  Zhang Wei: critical Client D demo (due 4/7), needs prep by 4/6
  Li Na: search feature 16h (due 4/8), Client D demo on 4/7 afternoon
  Wang Qiang: Order API 20h (due 4/9), meetings on 4/6+4/8+4/9+4/10 -- risk
  Zhao Min: DB partition 18h (due 4/9) with meetings -- risk
  Chen Lei: lighter load, available for support
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class Grader(AbstractGrader):
    """Grade weekly team work plan generation."""

    _PLANNING_RUBRIC = """\
Evaluate the quality of the weekly plan for all team members (0.0-1.0).

## Ground Truth (April 6-10)
- Zhang Wei: Critical Client D demo material due 4/7, must prep 4/6
- Li Na: Search feature 16h due 4/8, Client D demo on 4/7 PM
- Wang Qiang: Order API 20h due 4/9, heavy meeting load (4/6+4/8+4/9+4/10) -- risk
- Zhao Min: DB partition 18h due 4/9, meetings conflict with dev time -- risk
- Chen Lei: Lighter workload, potentially available for support

## Scoring tiers
- 0.9-1.0: All 5 members planned with daily breakdown; meetings vs dev time split; risks flagged
- 0.7-0.8: 4-5 members planned; most days covered; key risks identified
- 0.5-0.6: 3+ members; partial daily coverage; some risks
- 0.3-0.4: 1-2 members; minimal daily plan
- 0.0-0.2: No meaningful weekly plan
"""

    _RISK_ANALYSIS_RUBRIC = """\
Evaluate the quality of risk analysis and time allocation standards (0.0-1.0).

## Ground Truth Risks
- Wang Qiang: 20h task + heavy meetings = insufficient dev time
- Zhao Min: 18h task + meetings = capacity risk
- Zhang Wei critical demo should block other work on 4/6
- Notes scheduling rule: meetings should not exceed 3h/day or 60% of work time

## Scoring tiers
- 0.9-1.0: Both capacity risks identified with numbers; scheduling rules applied; mitigation suggested
- 0.7-0.8: Key risks identified; some quantification; scheduling awareness
- 0.5-0.6: Partial risk identification; vague quantification
- 0.3-0.4: Minimal risk awareness
- 0.0-0.2: No risk analysis
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
        det_score += 0.25 * self._score_member_coverage(all_text, lowered)
        det_score += 0.25 * self._score_daily_schedule(lowered)
        det_score += 0.25 * self._score_wang_risk(all_text, lowered)
        det_score += 0.25 * self._score_zhang_demo(all_text, lowered)

        # 3. Judge scoring (45%)
        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            planning_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._PLANNING_RUBRIC
            ).score
            risk_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._RISK_ANALYSIS_RUBRIC
            ).score
        else:
            planning_score = self._fallback_planning(all_text, lowered)
            risk_score = self._fallback_risk(lowered)

        # 4. Combine
        completion = tool_penalty * (
            0.55 * det_score
            + 0.25 * planning_score
            + 0.20 * risk_score
        )

        scores.completion = min(round(completion, 2), 1.0)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len(
            [m for m in messages if m.message.role == "assistant"]
        )
        return scores

    # -- Deterministic helpers --

    def _tool_gate(self, dispatches: list[ToolDispatch]) -> float:
        cal = any(d.tool_name in ("calendar_list_events", "calendar_get_event")
                  and d.response_status < 400 for d in dispatches)
        todo = any(d.tool_name == "todo_list_tasks"
                   and d.response_status < 400 for d in dispatches)
        notes = any(d.tool_name in ("notes_list", "notes_get")
                    and d.response_status < 400 for d in dispatches)
        count = sum([cal, todo, notes])
        if count == 0:
            return 0.2
        if count == 1:
            return 0.5
        if count == 2:
            return 0.8
        return 1.0

    def _score_member_coverage(self, text: str, lowered: str) -> float:
        members = [
            ("\u5f20\u4f1f", "zhang wei", "wei zhang"),
            ("\u674e\u5a1c", "li na", "na li"),
            ("\u738b\u5f3a", "wang qiang", "qiang wang"),
            ("\u8d75\u654f", "zhao min", "min zhao"),
            ("\u9648\u78ca", "chen lei", "lei chen"),
        ]
        found = sum(1 for zh, en1, en2 in members
                    if zh in text or en1 in lowered or en2 in lowered)
        return min(found / 4, 1.0)

    def _score_daily_schedule(self, lowered: str) -> float:
        day_kws = ["4\u67086", "4\u67087", "4\u67088", "4\u67089", "4\u670810",
                   "\u5468\u4e00", "\u5468\u4e8c", "\u5468\u4e09", "\u5468\u56db", "\u5468\u4e94",
                   "apr 6", "apr 7", "apr 8", "apr 9", "apr 10",
                   "monday", "tuesday", "wednesday", "thursday", "friday"]
        found = sum(1 for d in day_kws if d in lowered)
        return min(found / 3, 1.0)

    def _score_wang_risk(self, text: str, lowered: str) -> float:
        has_wang = any(n in text for n in ["\u738b\u5f3a"]) or \
                   any(n in lowered for n in ["wang qiang", "qiang wang"])
        if not has_wang:
            return 0.0
        risk_kws = ["\u98ce\u9669", "risk", "\u5de5\u65f6\u4e0d\u8db3",
                    "\u7d27\u5f20", "20", "\u8ba2\u5355api", "order api",
                    "insufficient time", "tight"]
        if any(kw in lowered for kw in risk_kws):
            return 1.0
        return 0.3

    def _score_zhang_demo(self, text: str, lowered: str) -> float:
        has_zhang = any(n in text for n in ["\u5f20\u4f1f"]) or \
                    any(n in lowered for n in ["zhang wei", "wei zhang"])
        if not has_zhang:
            return 0.0
        demo_kws = ["\u5ba2\u6237d", "client d", "customer d",
                    "\u6f14\u793a\u6750\u6599", "demo material", "presentation",
                    "\u51c6\u5907", "prepare", "prep"]
        if any(kw in lowered for kw in demo_kws):
            return 1.0
        return 0.3

    # -- Fallback scorers --

    def _fallback_planning(self, text: str, lowered: str) -> float:
        """_fallback_: dev-only keyword scoring for planning quality."""
        score = 0.0
        members = ["\u5f20\u4f1f", "\u674e\u5a1c", "\u738b\u5f3a", "\u8d75\u654f", "\u9648\u78ca"]
        score += 0.30 * min(sum(1 for m in members if m in text) / 4, 1.0)
        days = ["\u5468\u4e00", "\u5468\u4e8c", "\u5468\u4e09", "\u5468\u56db", "\u5468\u4e94",
                "monday", "tuesday", "wednesday", "thursday", "friday"]
        score += 0.30 * min(sum(1 for d in days if d in lowered) / 3, 1.0)
        if any(kw in lowered for kw in ["\u5f00\u53d1\u65f6\u95f4", "dev time",
                                         "\u4f1a\u8bae\u65f6\u95f4", "meeting time"]):
            score += 0.20
        if "|" in text and "---" in text:
            score += 0.20
        return min(score, 1.0)

    def _fallback_risk(self, lowered: str) -> float:
        """_fallback_: dev-only keyword scoring for risk analysis."""
        score = 0.0
        if any(kw in lowered for kw in ["\u98ce\u9669", "risk", "\u8fc7\u8f7d", "overload"]):
            score += 0.35
        if any(kw in lowered for kw in ["20", "18", "\u5de5\u65f6", "hour"]):
            score += 0.25
        if any(kw in lowered for kw in ["60%", "3\u5c0f\u65f6", "3 hour",
                                         "\u4f1a\u8bae\u8d85", "meeting exceed"]):
            score += 0.25
        if any(kw in lowered for kw in ["\u5efa\u8bae", "recommend", "\u7f13\u89e3", "mitigat"]):
            score += 0.15
        return min(score, 1.0)
