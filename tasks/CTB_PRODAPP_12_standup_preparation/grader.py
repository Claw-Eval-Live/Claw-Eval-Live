"""CTB_PRODAPP_12 grader -- standup preparation.

v2.2: WildClawBench mode (operation + analysis).
- Deterministic 55%: tool gate, member coverage, per-member accuracy, calendar refs
- Judge 45%: standup briefing quality, blocker analysis
- Fallback: keyword-based scoring when judge unavailable

Ground truth (from fixtures):
  Team: Wu Gang, Zheng Xue, Han Bing
  Wu Gang: Payment interface integration, SDK compatibility issue (v3.1/v3.2)
  Zheng Xue: Order list refactor complete, image upload requirement change (+4h)
  Han Bing: Deployment pipeline, permission blocker (needs ops ticket)
  Meetings: Payment tech review (14:00), Product requirement clarification (16:00)
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage

TEAM = ["\u5434\u521a", "\u90d1\u96ea", "\u97e9\u51b0"]


class Grader(AbstractGrader):
    """Grade standup preparation briefing."""

    _STANDUP_RUBRIC = """\
Evaluate the quality of the standup meeting summary (0.0-1.0).

## Ground Truth
- Wu Gang: Working on payment interface; blocker is SDK compatibility (v3.1 vs v3.2)
- Zheng Xue: Order list refactor completed; image upload has requirement change (+4h extra)
- Han Bing: Deployment pipeline work; blocked by permissions (needs ops ticket)
- Key meetings today: Payment tech review (14:00), Product requirement clarification (16:00)

## Scoring tiers
- 0.9-1.0: All 3 members with yesterday/today/blockers; meetings referenced; actionable
- 0.7-0.8: All members covered; most details correct; meetings mentioned
- 0.5-0.6: 2 members covered; partial accuracy
- 0.3-0.4: 1 member; minimal details
- 0.0-0.2: No meaningful standup summary
"""

    _BLOCKER_RUBRIC = """\
Evaluate the quality of blocker identification and discussion topics (0.0-1.0).

## Ground Truth Blockers
- Wu Gang: SDK compatibility issue between v3.1 and v3.2 (third-party dependency)
- Han Bing: Permission issue -- needs ops team to grant access, filed ticket
- Zheng Xue: Requirement change on image upload adds 4h unplanned work

## Scoring tiers
- 0.9-1.0: All blockers identified with root cause; resolution paths suggested
- 0.7-0.8: 2-3 blockers; reasonable context
- 0.5-0.6: 1-2 blockers; partial detail
- 0.3-0.4: Mentions blockers generically
- 0.0-0.2: No blocker analysis
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
        det_score += 0.15 * self._score_data_retrieval(dispatches, audit_data)
        det_score += 0.15 * self._score_member_coverage(all_text)
        det_score += 0.40 * self._score_per_member(all_text)
        det_score += 0.15 * self._score_calendar_refs(all_text)
        det_score += 0.15 * self._score_structure(lowered)

        # 3. Judge scoring (45%)
        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            standup_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._STANDUP_RUBRIC
            ).score
            blocker_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._BLOCKER_RUBRIC
            ).score
        else:
            standup_score = self._fallback_standup(all_text)
            blocker_score = self._fallback_blocker(all_text, lowered)

        # 4. Combine
        completion = tool_penalty * (
            0.55 * det_score
            + 0.25 * standup_score
            + 0.20 * blocker_score
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

    def _score_member_coverage(self, text: str) -> float:
        return sum(1 for m in TEAM if m in text) / len(TEAM)

    def _score_per_member(self, text: str) -> float:
        score = 0.0
        lowered = text.lower()
        # Wu Gang: payment + SDK
        if "\u5434\u521a" in text or "wu gang" in lowered:
            wg = 0.0
            if any(kw in text for kw in ["\u652f\u4ed8", "\u63a5\u53e3", "\u5bf9\u63a5"]) or \
               "payment" in lowered:
                wg += 0.4
            if any(kw in text for kw in ["SDK", "\u517c\u5bb9", "v3.1", "v3.2"]) or \
               "sdk" in lowered:
                wg += 0.6
            score += 0.33 * min(wg, 1.0)

        # Zheng Xue: order refactor + image upload change
        if "\u90d1\u96ea" in text or "zheng xue" in lowered:
            zx = 0.0
            if any(kw in text for kw in ["\u8ba2\u5355", "\u5217\u8868", "\u91cd\u6784",
                                          "\u5b8c\u6210"]) or "order" in lowered:
                zx += 0.3
            if any(kw in text for kw in ["\u56fe\u7247", "\u4e0a\u4f20",
                                          "\u9700\u6c42\u53d8\u66f4"]) or \
               any(kw in lowered for kw in ["image", "upload", "requirement change"]):
                zx += 0.7
            score += 0.33 * min(zx, 1.0)

        # Han Bing: deployment + permission blocker
        if "\u97e9\u51b0" in text or "han bing" in lowered:
            hb = 0.0
            if any(kw in text for kw in ["\u90e8\u7f72", "\u6d41\u6c34\u7ebf"]) or \
               "deploy" in lowered:
                hb += 0.3
            if any(kw in text for kw in ["\u6743\u9650", "\u963b\u585e"]) or \
               any(kw in lowered for kw in ["permission", "blocked", "ops"]):
                hb += 0.7
            score += 0.34 * min(hb, 1.0)

        return min(score, 1.0)

    def _score_calendar_refs(self, text: str) -> float:
        score = 0.0
        lowered = text.lower()
        if any(kw in text for kw in ["\u6280\u672f\u8bc4\u5ba1",
                                      "\u652f\u4ed8\u65b9\u6848\u6280\u672f\u8bc4\u5ba1"]) or \
           "tech review" in lowered:
            score += 0.4
        if any(kw in text for kw in ["\u9700\u6c42\u6f84\u6e05",
                                      "\u4ea7\u54c1\u9700\u6c42\u6f84\u6e05"]) or \
           "requirement clarification" in lowered:
            score += 0.3
        if "14:00" in text or "16:00" in text:
            score += 0.3
        return min(score, 1.0)

    def _score_structure(self, lowered: str) -> float:
        score = 0.0
        if any(kw in lowered for kw in ["\u8ba8\u8bba", "discuss", "topic", "\u8bae\u9898"]):
            score += 0.5
        if "standup_items" in lowered or "discussion_topics" in lowered:
            score += 0.5
        return min(score, 1.0)

    # -- Fallback scorers --

    def _fallback_standup(self, text: str) -> float:
        """_fallback_: dev-only scoring."""
        score = 0.0
        score += 0.25 * (sum(1 for m in TEAM if m in text) / len(TEAM))
        if "SDK" in text or "\u517c\u5bb9" in text:
            score += 0.20
        if "\u56fe\u7247" in text or "\u9700\u6c42\u53d8\u66f4" in text:
            score += 0.20
        if "\u6743\u9650" in text or "\u963b\u585e" in text:
            score += 0.20
        if "14:00" in text or "16:00" in text:
            score += 0.15
        return min(score, 1.0)

    def _fallback_blocker(self, text: str, lowered: str) -> float:
        """_fallback_: dev-only scoring."""
        score = 0.0
        if any(kw in lowered for kw in ["blocked", "blocker", "\u963b\u585e"]):
            score += 0.30
        if "SDK" in text or "\u517c\u5bb9" in text:
            score += 0.25
        if "\u6743\u9650" in text or "permission" in lowered:
            score += 0.25
        if "\u9700\u6c42\u53d8\u66f4" in text or "requirement change" in lowered:
            score += 0.20
        return min(score, 1.0)
