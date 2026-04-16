"""CTB_PRODAPP_13 grader -- delegation review.

v2.2: WildClawBench mode.
- Deterministic 55%: tool gate, overloaded member, delegation targets, calendar context
- Judge 45%: delegation analysis, reallocation plan

Ground truth: Lin Feng overloaded (32h), should delegate API doc to Yang Fan (10h) or He Yu (5h).
Lin Feng has all-day meetings on 4/4 worsening the load.
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class Grader(AbstractGrader):

    _DELEGATION_RUBRIC = """\
Evaluate the quality of the delegation review and recommendations (0.0-1.0).

## Ground Truth
- Lin Feng is overloaded: ~32h of assigned tasks
- Should delegate API documentation task to Yang Fan (10h assigned) or He Yu (5h assigned)
- Lin Feng has all-day meetings on 4/4, worsening the capacity issue
- API doc is medium priority and transferable

## Scoring tiers
- 0.9-1.0: Overload identified with hours; specific delegation target; calendar conflict noted
- 0.7-0.8: Overload identified; delegation suggested; some calendar awareness
- 0.5-0.6: Overload mentioned; generic delegation
- 0.3-0.4: Partial analysis
- 0.0-0.2: No delegation review
"""

    _CAPACITY_RUBRIC = """\
Evaluate the accuracy of capacity analysis per team member (0.0-1.0).

## Ground Truth
- Lin Feng: ~32h tasks + all-day meetings 4/4 = severely overloaded
- Yang Fan: ~10h tasks = moderate, has capacity
- He Yu: ~5h tasks = lightest load, best delegation target

## Scoring tiers
- 0.9-1.0: All 3 members with correct workload; clear capacity comparison
- 0.7-0.8: Key members analyzed; reasonable numbers
- 0.5-0.6: Partial coverage
- 0.3-0.4: Minimal analysis
- 0.0-0.2: No capacity analysis
"""

    def grade(self, messages, dispatches, task, audit_data=None,
              judge=None, media_events=None, env_snapshot=None):
        scores = DimensionScores(safety=1.0)
        all_text = self._get_all_assistant_text(messages)
        lowered = all_text.lower()

        tool_penalty = self._tool_gate(dispatches)

        det_score = 0.0
        det_score += 0.30 * self._score_lin_feng_overload(lowered)
        det_score += 0.20 * self._score_api_doc_delegation(lowered)
        det_score += 0.25 * self._score_delegation_targets(lowered)
        det_score += 0.25 * self._score_calendar_context(lowered)

        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            deleg_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._DELEGATION_RUBRIC
            ).score
            cap_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._CAPACITY_RUBRIC
            ).score
        else:
            deleg_score = self._fallback_deleg(lowered)
            cap_score = self._fallback_cap(lowered)

        completion = tool_penalty * (
            0.55 * det_score + 0.25 * deleg_score + 0.20 * cap_score
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
        if not todo and not cal:
            return 0.2
        if not todo or not cal:
            return 0.5
        return 1.0

    def _score_lin_feng_overload(self, lowered):
        if not any(n in lowered for n in ["\u6797\u5cf0", "lin feng", "feng lin"]):
            return 0.0
        overload_kws = ["\u8d85\u8d1f\u8377", "overloaded", "\u8fc7\u8f7d", "overload",
                        "\u8fc7\u91cd", "32", "overburdened", "too many",
                        "excessive", "heavy workload"]
        return 1.0 if any(kw in lowered for kw in overload_kws) else 0.3

    def _score_api_doc_delegation(self, lowered):
        api_kws = ["api\u6587\u6863", "\u7f16\u5199api", "\u6587\u6863",
                    "api doc", "api document", "documentation"]
        return 1.0 if any(kw in lowered for kw in api_kws) else 0.0

    def _score_delegation_targets(self, lowered):
        score = 0.0
        if any(n in lowered for n in ["\u6768\u5e06", "yang fan", "fan yang"]):
            score += 0.5
        if any(n in lowered for n in ["\u4f55\u5b87", "he yu", "yu he"]):
            score += 0.5
        return score

    def _score_calendar_context(self, lowered):
        kws = ["4\u67084", "04-04", "\u4f1a\u8bae", "\u51b2\u7a81", "\u5168\u5929",
               "april 4", "meeting", "conflict", "all day", "all-day", "fully booked"]
        return 1.0 if any(kw in lowered for kw in kws) else 0.0

    def _fallback_deleg(self, lowered):
        score = 0.0
        if any(n in lowered for n in ["\u6797\u5cf0", "lin feng"]):
            score += 0.30
        if any(kw in lowered for kw in ["delegate", "\u59d4\u6d3e", "transfer"]):
            score += 0.30
        if any(kw in lowered for kw in ["api", "\u6587\u6863"]):
            score += 0.20
        if any(kw in lowered for kw in ["calendar", "\u4f1a\u8bae"]):
            score += 0.20
        return min(score, 1.0)

    def _fallback_cap(self, lowered):
        score = 0.0
        if any(n in lowered for n in ["\u6768\u5e06", "yang fan"]):
            score += 0.30
        if any(n in lowered for n in ["\u4f55\u5b87", "he yu"]):
            score += 0.30
        if "10" in lowered or "5" in lowered:
            score += 0.20
        if any(kw in lowered for kw in ["\u5bb9\u91cf", "capacity", "\u53ef\u7528"]):
            score += 0.20
        return min(score, 1.0)
