"""CTB_PROD_07 grader -- weekly standup summary.

v2.2: WildClawBench mode.
- Deterministic 55%: tool gate, member progress, key decisions, stats
- Judge 45%: summary quality, analysis depth

Ground truth: 4 members (Zheng Peng, Huang Rong, Fang Da, Qian Jin).
Zheng Peng: Recommendation Algorithm full rollout, 12%->8%, 120ms latency, P1.
Huang Rong: Mobile configured, Canary release, iPad issue.
Fang Da: API 90%, Compatibility, Authentication Module, 3/28 deadline.
Qian Jin: Regression Testing 98.5%, P2, 50 test cases.
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class Grader(AbstractGrader):

    _SUMMARY_RUBRIC = """\
Evaluate the quality of the weekly standup summary (0.0-1.0).

## Ground Truth (March 24-27)
- Zheng Peng: Recommendation Algorithm full rollout launch; metrics 12%->8% conversion; 120ms latency; P1 issue
- Huang Rong: Mobile app configured; Complete Canary release; iPad compatibility issue
- Fang Da: API gateway 90% complete; Compatibility testing; Authentication Module; deadline 3/28
- Qian Jin: Regression Testing; 98.5% pass rate; P2 bugs; 50 test cases

## Scoring tiers
- 0.9-1.0: All 4 members with detailed progress; key metrics correct
- 0.7-0.8: All members covered; most metrics present
- 0.5-0.6: 3 members; partial metrics
- 0.3-0.4: 1-2 members
- 0.0-0.2: No meaningful summary
"""

    _DECISIONS_RUBRIC = """\
Evaluate the quality of key decisions and next-week plan extraction (0.0-1.0).

## Expected decisions
- Full rollout decision for recommendation algorithm
- Canary release approach for mobile
- iPad issue not blocking main release
- P1 issue prioritization

## Scoring tiers
- 0.9-1.0: All key decisions extracted; next-week plan present; blockers noted
- 0.7-0.8: Most decisions; some next-week planning
- 0.5-0.6: Partial decisions
- 0.3-0.4: Minimal content
- 0.0-0.2: No decisions or plan
"""

    def grade(self, messages, dispatches, task, audit_data=None,
              judge=None, media_events=None, env_snapshot=None):
        scores = DimensionScores(safety=1.0)
        all_text = self._get_all_assistant_text(messages)
        clean = all_text.replace(",", "").replace("\uff0c", "")
        lowered = all_text.lower()

        tool_penalty = self._tool_gate(dispatches)

        det_score = 0.0
        det_score += 0.10 * self._score_data_retrieval(dispatches)
        det_score += 0.25 * self._score_zheng_peng(clean)
        det_score += 0.20 * self._score_huang_rong(clean)
        det_score += 0.20 * self._score_fang_da(clean)
        det_score += 0.15 * self._score_qian_jin(clean)
        det_score += 0.10 * self._score_decisions(clean, lowered)

        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            summ_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._SUMMARY_RUBRIC
            ).score
            dec_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._DECISIONS_RUBRIC
            ).score
        else:
            summ_score = self._fallback_summ(clean)
            dec_score = self._fallback_dec(clean, lowered)

        completion = tool_penalty * (
            0.55 * det_score + 0.25 * summ_score + 0.20 * dec_score
        )

        scores.completion = min(round(completion, 2), 1.0)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len([m for m in messages if m.message.role == "assistant"])
        return scores

    def _tool_gate(self, dispatches):
        notes = any(d.tool_name in ("notes_list", "notes_get", "notes_list_documents",
                                     "notes_get_document")
                    and d.response_status < 400 for d in dispatches)
        todo = any(d.tool_name in ("todo_list_tasks", "todo_get_task")
                   and d.response_status < 400 for d in dispatches)
        if not notes and not todo:
            return 0.2
        if not notes or not todo:
            return 0.5
        return 1.0

    def _score_data_retrieval(self, dispatches):
        note_calls = [d for d in dispatches
                      if d.tool_name in ("notes_list", "notes_get",
                                          "notes_list_documents", "notes_get_document")
                      and d.response_status < 400]
        todo = any(d.tool_name in ("todo_list_tasks", "todo_get_task")
                   and d.response_status < 400 for d in dispatches)
        score = 0.0
        if note_calls:
            score += 0.5 * min(len(note_calls) / 3, 1.0)
        if todo:
            score += 0.5
        return score

    def _score_zheng_peng(self, clean):
        kws = ["Zheng Peng", "Recommendation Algorithm", "Full Rollout",
               "12%", "8%", "120ms", "latency", "P1"]
        found = sum(1 for kw in kws if kw in clean)
        return min(found / 4, 1.0)

    def _score_huang_rong(self, clean):
        kws = ["Huang Rong", "Mobile", "configured", "Complete",
               "Canary", "iPad"]
        found = sum(1 for kw in kws if kw in clean)
        return min(found / 3, 1.0)

    def _score_fang_da(self, clean):
        kws = ["Fang Da", "API", "90%", "Compatibility",
               "Authentication Module", "3/28", "March 28"]
        found = sum(1 for kw in kws if kw in clean)
        return min(found / 3, 1.0)

    def _score_qian_jin(self, clean):
        kws = ["Qian Jin", "Regression Testing", "98.5%", "P2", "50"]
        found = sum(1 for kw in kws if kw in clean)
        return min(found / 3, 1.0)

    def _score_decisions(self, clean, lowered):
        kws = ["Full Rollout", "iPad", "Canary", "P1"]
        found = sum(1 for kw in kws if kw in clean)
        return min(found / 3, 1.0)

    def _fallback_summ(self, clean):
        members = ["Zheng Peng", "Huang Rong", "Fang Da", "Qian Jin"]
        found = sum(1 for m in members if m in clean)
        return min(found / 3, 1.0)

    def _fallback_dec(self, clean, lowered):
        score = 0.0
        if "Full Rollout" in clean or "full rollout" in lowered:
            score += 0.30
        if "Canary" in clean or "canary" in lowered:
            score += 0.25
        if "P1" in clean:
            score += 0.25
        if any(kw in lowered for kw in ["decision", "key", "blocker", "next week"]):
            score += 0.20
        return min(score, 1.0)
