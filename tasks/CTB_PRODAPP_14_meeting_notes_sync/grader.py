"""CTB_PRODAPP_14 grader -- meeting notes to todo sync check.

v2.2: WildClawBench mode.
- Deterministic 55%: tool gate, missing items identified, sync actions
- Judge 45%: sync analysis quality, recommendation quality

Ground truth: 5 missing items from 3 meetings:
  - Auto-classification feature (product planning)
  - Batch export feature (customer requirements review)
  - Data masking plan (security compliance)
  - SSO integration / data visualization dashboard (customer requirements)
  - GDPR compliance / TLS1.3 upgrade (security compliance)
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class Grader(AbstractGrader):

    _SYNC_ANALYSIS_RUBRIC = """\
Evaluate the completeness of meeting-to-todo sync gap analysis (0.0-1.0).

## Ground Truth -- Missing from todo system
1. Auto-classification feature (from product planning meeting)
2. Batch export feature (from customer requirements review)
3. Data masking plan (from security compliance meeting)
4. SSO integration or data visualization dashboard (customer requirements)
5. GDPR compliance review or TLS1.3 upgrade (security compliance)

## Scoring tiers
- 0.9-1.0: All 5 missing items identified with meeting source attribution
- 0.7-0.8: 4 items identified; meeting sources noted
- 0.5-0.6: 2-3 items; partial attribution
- 0.3-0.4: 1-2 items
- 0.0-0.2: No sync gaps identified
"""

    _RECOMMENDATION_RUBRIC = """\
Evaluate the quality of task creation recommendations (0.0-1.0).

## Expected
- Clear recommendation to create tasks for each missing item
- Assignees and priorities suggested
- Timeline recommendations

## Scoring tiers
- 0.9-1.0: All missing items with create action, assignee, priority, timeline
- 0.7-0.8: Most items with creation recommendation; some details
- 0.5-0.6: Partial recommendations
- 0.3-0.4: Generic sync suggestion
- 0.0-0.2: No recommendations
"""

    def grade(self, messages, dispatches, task, audit_data=None,
              judge=None, media_events=None, env_snapshot=None):
        scores = DimensionScores(safety=1.0)
        all_text = self._get_all_assistant_text(messages)
        lowered = all_text.lower()

        tool_penalty = self._tool_gate(dispatches)

        det_score = 0.0
        det_score += 0.20 * self._score_auto_classify(lowered)
        det_score += 0.20 * self._score_batch_export(lowered)
        det_score += 0.20 * self._score_data_masking(lowered)
        det_score += 0.20 * self._score_sso_viz(lowered)
        det_score += 0.20 * self._score_compliance(lowered)

        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            sync_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._SYNC_ANALYSIS_RUBRIC
            ).score
            rec_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._RECOMMENDATION_RUBRIC
            ).score
        else:
            sync_score = self._fallback_sync(lowered)
            rec_score = self._fallback_rec(lowered)

        completion = tool_penalty * (
            0.55 * det_score + 0.25 * sync_score + 0.20 * rec_score
        )

        scores.completion = min(round(completion, 2), 1.0)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len([m for m in messages if m.message.role == "assistant"])
        return scores

    def _tool_gate(self, dispatches):
        notes = any(d.tool_name in ("notes_list", "notes_get")
                    and d.response_status < 400 for d in dispatches)
        todo = any(d.tool_name in ("todo_list_tasks", "todo_update_task")
                   and d.response_status < 400 for d in dispatches)
        if not notes and not todo:
            return 0.2
        if not notes or not todo:
            return 0.5
        return 1.0

    def _score_auto_classify(self, lowered):
        kws = ["\u81ea\u52a8\u5206\u7c7b", "auto classif", "auto-classif",
               "automatic classif", "auto categoriz"]
        return 1.0 if any(kw in lowered for kw in kws) else 0.0

    def _score_batch_export(self, lowered):
        kws = ["\u6279\u91cf\u5bfc\u51fa", "batch export", "bulk export",
               "volume export", "mass export"]
        return 1.0 if any(kw in lowered for kw in kws) else 0.0

    def _score_data_masking(self, lowered):
        kws = ["\u6570\u636e\u8131\u654f", "data masking", "data anonymi",
               "data de-identif", "data sanitiz"]
        return 1.0 if any(kw in lowered for kw in kws) else 0.0

    def _score_sso_viz(self, lowered):
        kws = ["sso", "\u53ef\u89c6\u5316", "\u770b\u677f",
               "visualization", "dashboard", "visual board"]
        return 1.0 if any(kw in lowered for kw in kws) else 0.0

    def _score_compliance(self, lowered):
        kws = ["gdpr", "tls", "\u5408\u89c4", "compliance"]
        return 1.0 if any(kw in lowered for kw in kws) else 0.0

    def _fallback_sync(self, lowered):
        items = ["\u81ea\u52a8\u5206\u7c7b", "auto classif",
                 "\u6279\u91cf\u5bfc\u51fa", "batch export",
                 "\u6570\u636e\u8131\u654f", "data masking",
                 "sso", "\u53ef\u89c6\u5316", "dashboard",
                 "gdpr", "tls"]
        found = sum(1 for kw in items if kw in lowered)
        return min(found / 4, 1.0)

    def _fallback_rec(self, lowered):
        score = 0.0
        if any(kw in lowered for kw in ["\u521b\u5efa", "\u65b0\u589e", "\u6dfb\u52a0",
                                         "\u540c\u6b65", "sync", "create", "add", "track"]):
            score += 0.50
        if any(kw in lowered for kw in ["\u4f18\u5148", "priority", "\u8d1f\u8d23",
                                         "assignee", "owner"]):
            score += 0.50
        return min(score, 1.0)
