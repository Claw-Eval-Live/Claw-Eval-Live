"""CTB_PROD_03 grader -- sprint progress gap analysis.

v2.2: WildClawBench mode.
- Deterministic 55%: tool gate, completion stats, risk items, bug insertion
- Judge 45%: progress analysis quality, member analysis

Ground truth: 18 pts committed, 8 completed (44%). SP12-002 in-progress (5pts),
SP12-004 pending, SP12-006 blocked. BUG-101 unplanned insertion consumed Zhang's time.
Zhang overloaded (10pts + bug). Zhao has blocked item.
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class SprintProgressGapGrader(AbstractGrader):

    _PROGRESS_RUBRIC = """\
Evaluate the accuracy of sprint progress analysis (0.0-1.0).

## Ground Truth
- 18 story points committed, 8 completed (44%)
- 3 tasks done: SP12-001 (3pts), SP12-003 (3pts), SP12-005 (2pts)
- At risk: SP12-002 (5pts, in_progress), SP12-004 (pending), SP12-006 (blocked)
- BUG-101 unplanned insertion consumed Zhang's time
- Zhang overloaded: 10pts committed + bug, Zhao has blocked item

## Scoring tiers
- 0.9-1.0: Correct total/completed/percentage; all risk items; bug insertion; member analysis
- 0.7-0.8: Stats mostly correct; key risks identified; bug insertion noted
- 0.5-0.6: Partial stats; some risks
- 0.3-0.4: Minimal analysis
- 0.0-0.2: No progress analysis
"""

    _RECOMMENDATION_RUBRIC = """\
Evaluate the quality of risk recommendations (0.0-1.0).

## Expected
- Address blocked SP12-006; unblock recommendation
- Address Zhang's overload and bug insertion impact
- Carry-over plan for incomplete items
- Sprint health assessment

## Scoring tiers
- 0.9-1.0: All risks addressed with concrete actions; carry-over plan
- 0.7-0.8: Key risks addressed; reasonable actions
- 0.5-0.6: Some recommendations
- 0.3-0.4: Generic suggestions
- 0.0-0.2: No recommendations
"""

    @staticmethod
    def _has_bounded(text, num):
        return bool(re.search(rf'(?<!\d){re.escape(num)}(?!\d)', text))

    def grade(self, messages, dispatches, task, audit_data=None,
              judge=None, media_events=None, env_snapshot=None):
        scores = DimensionScores(safety=1.0)
        all_text = self._get_all_assistant_text(messages)
        lowered = all_text.lower()
        clean = all_text.replace(",", "")

        tool_penalty = self._tool_gate(dispatches)

        det_score = 0.0
        det_score += 0.15 * self._score_data_retrieval(dispatches)
        det_score += 0.25 * self._score_stats(clean, lowered)
        det_score += 0.25 * self._score_risks(lowered)
        det_score += 0.15 * self._score_bug_insertion(lowered)
        det_score += 0.20 * self._score_members(lowered)

        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            prog_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._PROGRESS_RUBRIC
            ).score
            rec_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._RECOMMENDATION_RUBRIC
            ).score
        else:
            prog_score = self._fallback_prog(clean, lowered)
            rec_score = self._fallback_rec(lowered)

        completion = tool_penalty * (
            0.55 * det_score + 0.25 * prog_score + 0.20 * rec_score
        )

        scores.completion = min(round(completion, 2), 1.0)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len([m for m in messages if m.message.role == "assistant"])
        return scores

    def _tool_gate(self, dispatches):
        notes = any(d.tool_name in ("notes_list", "notes_get")
                    and d.response_status < 400 for d in dispatches)
        todo = any(d.tool_name == "todo_list_tasks"
                   and d.response_status < 400 for d in dispatches)
        if not notes and not todo:
            return 0.2
        if not notes or not todo:
            return 0.5
        return 1.0

    def _score_data_retrieval(self, dispatches):
        notes = any(d.tool_name in ("notes_list", "notes_get") and d.response_status < 400
                    for d in dispatches)
        todo = any(d.tool_name == "todo_list_tasks" and d.response_status < 400
                   for d in dispatches)
        return sum([notes, todo]) / 2.0

    def _score_stats(self, clean, lowered):
        score = 0.0
        if self._has_bounded(clean, "18") and any(kw in lowered for kw in ["story point", "total", "committed"]):
            score += 0.30
        if self._has_bounded(clean, "8") and any(kw in lowered for kw in ["complete", "done", "finished"]):
            score += 0.30
        if re.search(r"44[.\d]*%", clean):
            score += 0.20
        if "blocked" in lowered:
            score += 0.10
        if any(kw in lowered for kw in ["not started", "pending", "to do"]):
            score += 0.10
        return min(score, 1.0)

    def _score_risks(self, lowered):
        score = 0.0
        if any(kw in lowered for kw in ["order api", "sp12-002", "api v2"]):
            score += 0.35
        if any(kw in lowered for kw in ["performance test", "sp12-006"]):
            if "blocked" in lowered:
                score += 0.35
            else:
                score += 0.15
        if any(kw in lowered for kw in ["login", "security hardening", "sp12-004"]):
            if any(kw in lowered for kw in ["not started", "pending"]):
                score += 0.30
            else:
                score += 0.15
        return min(score, 1.0)

    def _score_bug_insertion(self, lowered):
        bug_kws = ["bug-101", "bug", "search sort", "unplanned", "inserted"]
        if any(kw in lowered for kw in bug_kws):
            context_kws = ["insert", "unplanned", "extra", "impact",
                           "outside sprint", "ad hoc", "scope"]
            if any(kw in lowered for kw in context_kws):
                return 1.0
            return 0.5
        return 0.0

    def _score_members(self, lowered):
        score = 0.0
        if "zhang" in lowered:
            if any(kw in lowered for kw in ["overload", "heavy", "10", "most", "highest"]):
                score += 0.50
            else:
                score += 0.20
        if "li" in lowered and any(kw in lowered for kw in ["complete", "done"]):
            score += 0.20
        if "zhao" in lowered and "blocked" in lowered:
            score += 0.30
        return min(score, 1.0)

    def _fallback_prog(self, clean, lowered):
        return self._score_stats(clean, lowered) * 0.5 + self._score_risks(lowered) * 0.5

    def _fallback_rec(self, lowered):
        score = 0.0
        if any(kw in lowered for kw in ["unblock", "resolve", "address"]):
            score += 0.30
        if any(kw in lowered for kw in ["carry", "next sprint", "backlog"]):
            score += 0.30
        if any(kw in lowered for kw in ["recommend", "suggest", "action"]):
            score += 0.40
        return min(score, 1.0)
