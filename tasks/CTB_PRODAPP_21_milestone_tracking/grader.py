"""CTB_PRODAPP_21 grader -- milestone tracking.

v2.2: WildClawBench mode.
- Deterministic 55%: tool gate, MS1 complete, MS2 risk, cascade impact, health assessment
- Judge 45%: milestone analysis quality, action items quality

Ground truth: MS1 complete. MS2 at risk (75%, mobile delay, +8h needed).
Cascade impact on MS3. Overall health=yellow. Performance 200ms risk from MS1.
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class Grader(AbstractGrader):

    _MILESTONE_RUBRIC = """\
Evaluate the accuracy of milestone status tracking (0.0-1.0).

## Ground Truth
- Milestone 1: COMPLETE (requirements freeze done)
- Milestone 2: AT RISK -- 75% done, mobile frontend delayed, extra 8h needed, may delay 1-2 days
- Milestone 3: CASCADE RISK -- depends on MS2 completion, integration test + performance testing
- Overall health: YELLOW (at risk but recoverable)
- Performance risk from MS1: response time 200ms, needs optimization

## Scoring tiers
- 0.9-1.0: All milestones correctly assessed; cascade analyzed; health color correct; performance risk
- 0.7-0.8: MS1+MS2 correct; cascade mentioned; health assessed
- 0.5-0.6: 2 milestones assessed; partial cascade
- 0.3-0.4: 1 milestone; minimal analysis
- 0.0-0.2: No milestone tracking
"""

    _ACTION_RUBRIC = """\
Evaluate the quality of recommended actions (0.0-1.0).

## Expected actions
- Address MS2 mobile delay (resource reallocation or scope reduction)
- Monitor cascade impact on MS3
- Performance optimization for 200ms issue
- Risk mitigation plan

## Scoring tiers
- 0.9-1.0: Actionable items per milestone; mitigation plan; specific next steps
- 0.7-0.8: Key actions identified; some specifics
- 0.5-0.6: Generic actions
- 0.3-0.4: Minimal recommendations
- 0.0-0.2: No action items
"""

    def grade(self, messages, dispatches, task, audit_data=None,
              judge=None, media_events=None, env_snapshot=None):
        scores = DimensionScores(safety=1.0)
        all_text = self._get_all_assistant_text(messages)
        lowered = all_text.lower()

        tool_penalty = self._tool_gate(dispatches)

        det_score = 0.0
        det_score += 0.20 * self._score_ms1_complete(lowered)
        det_score += 0.25 * self._score_ms2_risk(lowered)
        det_score += 0.20 * self._score_cascade(lowered)
        det_score += 0.15 * self._score_health_assessment(lowered)
        det_score += 0.20 * self._score_performance_risk(lowered)

        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            ms_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._MILESTONE_RUBRIC
            ).score
            act_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._ACTION_RUBRIC
            ).score
        else:
            ms_score = self._fallback_ms(lowered)
            act_score = self._fallback_act(lowered)

        completion = tool_penalty * (
            0.55 * det_score + 0.25 * ms_score + 0.20 * act_score
        )

        scores.completion = min(round(completion, 2), 1.0)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len([m for m in messages if m.message.role == "assistant"])
        return scores

    def _tool_gate(self, dispatches):
        todo = any(d.tool_name in ("todo_list_tasks", "todo_update_task")
                   and d.response_status < 400 for d in dispatches)
        cal = any(d.tool_name in ("calendar_list_events", "calendar_get_event")
                  and d.response_status < 400 for d in dispatches)
        notes = any(d.tool_name in ("notes_list", "notes_get")
                    and d.response_status < 400 for d in dispatches)
        count = sum([todo, cal, notes])
        if count == 0:
            return 0.2
        if count == 1:
            return 0.5
        return 1.0

    def _score_ms1_complete(self, lowered):
        ms1 = any(kw in lowered for kw in ["\u91cc\u7a0b\u78911", "milestone 1", "milestone1", "milestone #1"])
        done = any(kw in lowered for kw in ["\u5b8c\u6210", "done", "complete", "100%", "finished"])
        return 1.0 if ms1 and done else 0.0

    def _score_ms2_risk(self, lowered):
        kws = ["75%", "\u5ef6\u671f", "delay", "\u989d\u59168\u5c0f\u65f6",
               "\u79fb\u52a8\u7aef", "additional 8", "extra 8 hour", "mobile",
               "frontend", "behind schedule"]
        return 1.0 if any(kw in lowered for kw in kws) else 0.0

    def _score_cascade(self, lowered):
        kws = ["\u91cc\u7a0b\u78913", "milestone 3", "\u8fde\u9501", "cascade",
               "\u6027\u80fd", "\u5f71\u54cd", "downstream", "impact", "depend",
               "integration test", "performance"]
        return 1.0 if any(kw in lowered for kw in kws) else 0.0

    def _score_health_assessment(self, lowered):
        kws = ["yellow", "\u9ec4\u8272", "\u8b66\u544a", "at risk", "\u98ce\u9669",
               "risk", "warning", "caution", "amber"]
        return 1.0 if any(kw in lowered for kw in kws) else 0.0

    def _score_performance_risk(self, lowered):
        kws = ["200ms", "\u54cd\u5e94\u65f6\u95f4", "\u6027\u80fd\u4f18\u5316",
               "\u6027\u80fd\u95ee\u9898", "response time", "performance optim",
               "performance issue", "latency"]
        return 1.0 if any(kw in lowered for kw in kws) else 0.0

    def _fallback_ms(self, lowered):
        score = 0.0
        if self._score_ms1_complete(lowered):
            score += 0.25
        if self._score_ms2_risk(lowered):
            score += 0.25
        if self._score_cascade(lowered):
            score += 0.25
        if self._score_health_assessment(lowered):
            score += 0.25
        return min(score, 1.0)

    def _fallback_act(self, lowered):
        kws = ["\u884c\u52a8", "action", "\u63aa\u65bd", "\u5efa\u8bae",
               "recommend", "\u7f13\u89e3", "mitigation", "suggestion",
               "next step", "countermeasure"]
        return 1.0 if any(kw in lowered for kw in kws) else 0.0
