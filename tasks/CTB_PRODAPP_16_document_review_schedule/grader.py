"""CTB_PRODAPP_16 grader -- document review schedule.

v2.2: WildClawBench mode.
- Deterministic 55%: tool gate, docs scheduled, priority ordering, deadline respect
- Judge 45%: schedule quality, constraint handling

Ground truth: 4 documents to schedule.
DB migration urgent (deadline 4/12), Security hardening (deadline 4/8).
No self-review (author != reviewer). Calendar conflicts must be avoided.
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class Grader(AbstractGrader):

    _SCHEDULE_RUBRIC = """\
Evaluate the quality of the document review schedule (0.0-1.0).

## Ground Truth
4 documents: Microservice Architecture, DB Migration, API Interface, Security Hardening
- DB Migration: urgent, deadline 4/12 -- should be scheduled first
- Security Hardening: deadline 4/8 -- must be reviewed before 4/8
- No self-review allowed (author cannot be their own reviewer)
- Calendar conflicts must be avoided

## Scoring tiers
- 0.9-1.0: All 4 docs scheduled with correct priority; deadlines respected; no self-review
- 0.7-0.8: All docs scheduled; mostly correct priority; deadline awareness
- 0.5-0.6: 3+ docs; partial priority ordering
- 0.3-0.4: Some docs scheduled; deadline issues
- 0.0-0.2: No meaningful schedule
"""

    _CONSTRAINT_RUBRIC = """\
Evaluate how well scheduling constraints are handled (0.0-1.0).

## Constraints
- No self-review (Zhe Fang authored doc-1 and doc-4, Min He authored doc-2, Si Lu authored doc-3)
- Calendar availability must be checked
- Free slots identified for review meetings

## Scoring tiers
- 0.9-1.0: All constraints handled; free slots proposed; no violations
- 0.7-0.8: Most constraints handled; calendar checked
- 0.5-0.6: Some constraint awareness
- 0.3-0.4: Minimal constraint handling
- 0.0-0.2: No constraint consideration
"""

    def grade(self, messages, dispatches, task, audit_data=None,
              judge=None, media_events=None, env_snapshot=None):
        scores = DimensionScores(safety=1.0)
        all_text = self._get_all_assistant_text(messages)
        lowered = all_text.lower()

        tool_penalty = self._tool_gate(dispatches)

        det_score = 0.0
        det_score += 0.25 * self._score_docs_coverage(lowered)
        det_score += 0.25 * self._score_db_priority(lowered)
        det_score += 0.25 * self._score_security_deadline(lowered)
        det_score += 0.25 * self._score_calendar_awareness(lowered)

        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            sched_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._SCHEDULE_RUBRIC
            ).score
            const_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._CONSTRAINT_RUBRIC
            ).score
        else:
            sched_score = self._fallback_sched(lowered)
            const_score = self._fallback_const(lowered)

        completion = tool_penalty * (
            0.55 * det_score + 0.25 * sched_score + 0.20 * const_score
        )

        scores.completion = min(round(completion, 2), 1.0)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len([m for m in messages if m.message.role == "assistant"])
        return scores

    def _tool_gate(self, dispatches):
        notes = any(d.tool_name in ("notes_list", "notes_get")
                    and d.response_status < 400 for d in dispatches)
        cal = any(d.tool_name in ("calendar_list_events", "calendar_get_event")
                  and d.response_status < 400 for d in dispatches)
        if not notes and not cal:
            return 0.2
        if not notes or not cal:
            return 0.5
        return 1.0

    def _score_docs_coverage(self, lowered):
        pairs = [("\u5fae\u670d\u52a1\u67b6\u6784", "microservice"),
                 ("\u6570\u636e\u5e93\u8fc1\u79fb", "database migrat"),
                 ("api\u63a5\u53e3", "api"),
                 ("\u5b89\u5168\u52a0\u56fa", "security")]
        found = sum(1 for zh, en in pairs if zh in lowered or en in lowered)
        return min(found / 4, 1.0)

    def _score_db_priority(self, lowered):
        db = "\u6570\u636e\u5e93\u8fc1\u79fb" in lowered or "database migrat" in lowered
        priority_kws = ["\u7d27\u6025", "\u4f18\u5148", "\u5148", "urgent",
                        "4\u67086", "04-06", "priorit", "first", "highest", "april 6"]
        return 1.0 if db and any(kw in lowered for kw in priority_kws) else 0.0

    def _score_security_deadline(self, lowered):
        sec = "\u5b89\u5168\u52a0\u56fa" in lowered or "security" in lowered
        date_kws = ["4\u67088", "04-08", "4\u67087", "04-07", "4\u67086", "04-06",
                    "april 8", "april 7", "april 6"]
        return 1.0 if sec and any(kw in lowered for kw in date_kws) else 0.0

    def _score_calendar_awareness(self, lowered):
        kws = ["\u7a7a\u95f2", "idle", "\u53ef\u7528", "available", "\u51b2\u7a81",
               "\u907f\u5f00", "13:", "14:", "15:", "16:", "free slot", "open slot",
               "conflict", "no overlap"]
        return 1.0 if any(kw in lowered for kw in kws) else 0.0

    def _fallback_sched(self, lowered):
        score = 0.0
        score += 0.30 * self._score_docs_coverage(lowered)
        if any(kw in lowered for kw in ["\u4f18\u5148", "priorit", "first"]):
            score += 0.25
        if re.findall(r"\d{1,2}:\d{2}", lowered):
            score += 0.25
        if any(kw in lowered for kw in ["\u8bc4\u5ba1\u4eba", "reviewer", "\u5206\u914d"]):
            score += 0.20
        return min(score, 1.0)

    def _fallback_const(self, lowered):
        score = 0.0
        if any(kw in lowered for kw in ["\u4e0d\u80fd\u81ea\u5df1", "\u975e\u4f5c\u8005",
                                         "self-review", "cannot review own"]):
            score += 0.40
        if any(kw in lowered for kw in ["\u53ef\u7528", "available", "\u7a7a\u95f2", "free"]):
            score += 0.30
        if any(kw in lowered for kw in ["\u51b2\u7a81", "conflict", "\u907f\u5f00"]):
            score += 0.30
        return min(score, 1.0)
