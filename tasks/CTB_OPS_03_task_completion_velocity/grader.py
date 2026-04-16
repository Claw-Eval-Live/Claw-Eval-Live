"""CTB_OPS_03 grader -- task completion velocity analysis.

Ground truth source: fixtures/todo/tasks.json

v2.2: WildClawBench mode (data analysis + recommendations).
- Deterministic 55%: tool gate, member coverage, key stats, overdue detection
- Judge 45%: analysis accuracy, ranking and recommendations
- Fallback: keyword-based scoring when judge unavailable

Ground truth:
  zhangwei: 3 completed, 0 pending, avg 5.67 days, 0 overdue, high-pri avg 4 days
  liming: 2 completed, 1 pending, avg 8.5 days, 1 overdue (todo_303), high-pri avg 12 days
  wangli: 1 completed, 1 pending, avg 6 days, 0 overdue
  Team: 6/8 = 75%, avg 6.67 days, 1 overdue (16.7%)
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class Grader(AbstractGrader):
    """Grade task completion velocity analysis."""

    _ANALYSIS_RUBRIC = """\
Evaluate the accuracy of per-member and team-level task analysis (0.0-1.0).

## Ground Truth
Per-member:
- zhangwei: 3 completed, 0 pending. Avg completion: 5.67 days. No overdue. High-priority avg: 4 days.
- liming: 2 completed, 1 pending. Avg completion: 8.5 days. 1 overdue (todo_303, due 3/20 done 3/22). High-priority avg: 12 days.
- wangli: 1 completed, 1 pending. Avg completion: 6 days. No overdue.

Team totals:
- Completion rate: 6/8 = 75%
- Average completion time: ~6.67 days
- Overdue tasks: 1 (16.7%)

## Scoring tiers
- 0.9-1.0: All 3 members with correct counts, averages; team stats correct; overdue identified
- 0.7-0.8: Most member data correct; team stats approximately right
- 0.5-0.6: Some member data; partial team stats
- 0.3-0.4: Minimal data
- 0.0-0.2: No meaningful analysis
"""

    _RECOMMENDATION_RUBRIC = """\
Evaluate the quality of efficiency ranking and improvement recommendations (0.0-1.0).

## Expected elements
- Ranking: zhangwei (fastest, ~5.67 days) > wangli (~6 days) > liming (slowest, ~8.5 days)
- liming has overdue task and slowest average -- needs improvement
- High-priority task handling varies significantly (zhangwei 4 days vs liming 12 days)
- Recommendations should address liming's efficiency gap

## Scoring tiers
- 0.9-1.0: Clear ranking with supporting data; specific recommendations per person
- 0.7-0.8: Ranking present; some recommendations
- 0.5-0.6: Partial ranking; generic suggestions
- 0.3-0.4: Mentions efficiency but no ranking
- 0.0-0.2: No ranking or recommendations
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
        clean = all_text.replace(",", "").replace(" ", "")

        tool_penalty = self._tool_gate(dispatches)

        det_score = 0.0
        det_score += 0.25 * self._score_member_coverage(all_text)
        det_score += 0.25 * self._score_team_stats(all_text, lower, clean)
        det_score += 0.25 * self._score_overdue_detection(lower)
        det_score += 0.25 * self._score_ranking(lower)

        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            analysis_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._ANALYSIS_RUBRIC
            ).score
            rec_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._RECOMMENDATION_RUBRIC
            ).score
        else:
            analysis_score = self._fallback_analysis(all_text, lower, clean)
            rec_score = self._fallback_rec(lower)

        completion = tool_penalty * (
            0.55 * det_score
            + 0.20 * analysis_score
            + 0.25 * rec_score
        )

        scores.completion = min(round(completion, 2), 1.0)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len(
            [m for m in messages if m.message.role == "assistant"]
        )
        return scores

    @staticmethod
    def _has_bounded(text: str, num: str) -> bool:
        return bool(re.search(rf'(?<!\d){re.escape(num)}(?!\d)', text))

    def _tool_gate(self, dispatches: list[ToolDispatch]) -> float:
        todo = [d for d in dispatches if d.tool_name in ("todo_list_tasks", "todo_get_task") and d.response_status < 400]
        if not todo:
            return 0.2
        return 1.0

    def _score_member_coverage(self, all_text: str) -> float:
        members = ["zhangwei", "liming", "wangli"]
        found = sum(1 for m in members if m in all_text.lower())
        return found / len(members)

    def _score_team_stats(self, all_text: str, lower: str, clean: str) -> float:
        score = 0.0
        if re.search(r'75%|6/8', clean):
            score += 0.5
        if re.search(r'6\.6[0-9]|6\.7|6\.67', all_text):
            score += 0.5
        return min(score, 1.0)

    def _score_overdue_detection(self, lower: str) -> float:
        if any(kw in lower for kw in ["overdue", "late", "delayed", "past due"]) and \
           any(kw in lower for kw in ["liming", "todo_303"]):
            return 1.0
        if any(kw in lower for kw in ["overdue", "late"]):
            return 0.5
        return 0.0

    def _score_ranking(self, lower: str) -> float:
        rank_kw = ["rank", "fastest", "slowest", "efficien", "best", "worst"]
        found = sum(1 for kw in rank_kw if kw in lower)
        return min(found / 2, 1.0)

    def _fallback_analysis(self, all_text: str, lower: str, clean: str) -> float:
        """_fallback_: dev-only."""
        score = 0.0
        members = ["zhangwei", "liming", "wangli"]
        score += 0.25 * min(sum(1 for m in members if m in lower) / 3, 1.0)
        if re.search(r'75%|6/8', clean):
            score += 0.20
        if self._has_bounded(all_text, "8.5"):
            score += 0.15
        if self._has_bounded(all_text, "5.67") or self._has_bounded(all_text, "5.7"):
            score += 0.15
        if any(kw in lower for kw in ["overdue", "late"]):
            score += 0.15
        if "|" in all_text:
            score += 0.10
        return min(score, 1.0)

    def _fallback_rec(self, lower: str) -> float:
        """_fallback_: dev-only."""
        score = 0.0
        rec_kw = ["rank", "efficien", "recommend", "improv", "suggest", "fastest", "slowest"]
        found = sum(1 for kw in rec_kw if kw in lower)
        return min(found / 3, 1.0)
