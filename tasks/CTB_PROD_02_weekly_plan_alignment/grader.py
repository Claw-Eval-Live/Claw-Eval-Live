"""CTB_PROD_02 grader -- weekly plan three-source alignment.

v2.2: WildClawBench mode.
- Deterministic 55%: tool gate, discrepancy identification, source references
- Judge 45%: alignment analysis quality, recommendations

Ground truth: Date mismatch (quarterly report 3/28 vs 3/31), calendar missing (launch rehearsal,
fire drill), TODO missing (server expansion, client demo, launch rehearsal).
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage

DISCREPANCIES = [
    {"id": "report_date", "keywords": ["quarterly report", "3/28", "march 28", "3/31", "march 31"], "min_kw": 2},
    {"id": "launch_rehearsal", "keywords": ["launch", "rehearsal", "3/31", "march 31"], "min_kw": 1},
    {"id": "fire_drill", "keywords": ["fire", "drill", "3/26", "afternoon"], "min_kw": 1},
    {"id": "server_todo", "keywords": ["server", "expansion", "zhao lei"], "min_kw": 2},
    {"id": "demo_todo", "keywords": ["client", "demo", "zhang wei"], "min_kw": 2},
]


class WeeklyPlanAlignmentGrader(AbstractGrader):

    _ALIGNMENT_RUBRIC = """\
Evaluate the accuracy of three-source alignment analysis (0.0-1.0).

## Ground Truth -- 5 Discrepancies
1. Date mismatch: Quarterly report -- notes say 3/28 vs TODO says 3/31
2. Calendar missing: Product launch rehearsal (3/31)
3. Calendar missing: Fire drill (3/26 2-3pm)
4. TODO missing: Server expansion (Zhao Lei, 3/27)
5. TODO missing: Client demo (Zhang Wei)

## Scoring tiers
- 0.9-1.0: All 5 discrepancies found with specific details
- 0.7-0.8: 4 discrepancies; correct categorization
- 0.5-0.6: 2-3 discrepancies
- 0.3-0.4: 1-2 discrepancies
- 0.0-0.2: No discrepancies identified
"""

    _RECOMMENDATION_RUBRIC = """\
Evaluate the quality of follow-up recommendations (0.0-1.0).

## Expected
- Resolve date conflict for quarterly report
- Create missing calendar events and TODO tasks
- All three sources referenced

## Scoring tiers
- 0.9-1.0: Specific actions per discrepancy; prioritized
- 0.7-0.8: Most actions covered; reasonable
- 0.5-0.6: Generic recommendations
- 0.3-0.4: Minimal suggestions
- 0.0-0.2: No recommendations
"""

    def grade(self, messages, dispatches, task, audit_data=None,
              judge=None, media_events=None, env_snapshot=None):
        scores = DimensionScores(safety=1.0)
        all_text = self._get_all_assistant_text(messages)
        lowered = all_text.lower()

        tool_penalty = self._tool_gate(dispatches)

        det_score = 0.0
        det_score += 0.25 * self._score_data_retrieval(dispatches)
        det_score += 0.50 * self._score_discrepancies(lowered)
        det_score += 0.15 * self._score_sources(lowered)
        det_score += 0.10 * self._score_actions(lowered)

        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            align_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._ALIGNMENT_RUBRIC
            ).score
            rec_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._RECOMMENDATION_RUBRIC
            ).score
        else:
            align_score = self._score_discrepancies(lowered)
            rec_score = self._fallback_rec(lowered)

        completion = tool_penalty * (
            0.55 * det_score + 0.25 * align_score + 0.20 * rec_score
        )

        scores.completion = min(round(completion, 2), 1.0)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len([m for m in messages if m.message.role == "assistant"])
        return scores

    def _tool_gate(self, dispatches):
        notes = any(d.tool_name in ("notes_list", "notes_get")
                    and d.response_status < 400 for d in dispatches)
        cal = any(d.tool_name == "calendar_list_events"
                  and d.response_status < 400 for d in dispatches)
        todo = any(d.tool_name == "todo_list_tasks"
                   and d.response_status < 400 for d in dispatches)
        count = sum([notes, cal, todo])
        if count == 0:
            return 0.2
        if count <= 1:
            return 0.4
        if count == 2:
            return 0.7
        return 1.0

    def _score_data_retrieval(self, dispatches):
        note_get = [d for d in dispatches if d.tool_name == "notes_get" and d.response_status < 400]
        note_ids = {str(d.request_body.get("note_id")) for d in note_get}
        notes_score = min(len(note_ids & {"note_901", "note_902"}) / 2, 1.0)
        cal = any(d.tool_name == "calendar_list_events" and d.response_status < 400 for d in dispatches)
        todo = any(d.tool_name == "todo_list_tasks" and d.response_status < 400 for d in dispatches)
        return (notes_score + (1.0 if cal else 0.0) + (1.0 if todo else 0.0)) / 3.0

    def _score_discrepancies(self, lowered):
        found = 0
        for disc in DISCREPANCIES:
            kw_found = sum(1 for kw in disc["keywords"] if kw.lower() in lowered)
            if kw_found >= disc["min_kw"]:
                found += 1
        return min(found / 4, 1.0)

    def _score_sources(self, lowered):
        sources = ["note", "calendar", "task"]
        found = sum(1 for s in sources if s in lowered)
        return found / 3.0

    def _score_actions(self, lowered):
        kws = ["recommend", "suggest", "should", "need to", "create", "add"]
        return 1.0 if any(kw in lowered for kw in kws) else 0.0

    def _fallback_rec(self, lowered):
        score = 0.0
        if self._score_actions(lowered):
            score += 0.40
        if self._score_sources(lowered) >= 0.9:
            score += 0.30
        if any(kw in lowered for kw in ["date", "mismatch", "conflict"]):
            score += 0.30
        return min(score, 1.0)
