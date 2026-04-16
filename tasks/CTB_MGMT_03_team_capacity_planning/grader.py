"""CTB_MGMT_03 grader -- team capacity planning.

Ground truth source: fixtures/calendar + fixtures/todo

v2.2: WildClawBench mode (analysis + operation recommendations).
- Deterministic 55%: tool gate, member coverage, bottleneck ID, meeting data, risk detection
- Judge 45%: analysis quality, rebalancing plan
- Fallback: keyword-based scoring when judge unavailable

Ground truth:
  Team: zhang.lei, wang.fang, li.na, chen.wei, zhao.peng
  wang.fang is bottleneck (most meetings + tasks)
  Microservice deadline April 1 is at risk
  Rebalancing needed: offload from wang.fang
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class Grader(AbstractGrader):
    """Grade team capacity planning report."""

    _ANALYSIS_RUBRIC = """\
Evaluate the quality of workload analysis and risk identification (0.0-1.0).

## Ground Truth
- wang.fang has the heaviest workload (most meetings ~7h and most tasks)
- Microservice refactoring deadline April 1 is at risk of being missed
- Meeting time percentages should be calculated per person
- Task load should consider priority levels

## Scoring tiers
- 0.9-1.0: All members analyzed; wang.fang bottleneck quantified; April 1 risk flagged with impact
- 0.7-0.8: Most members covered; bottleneck identified; risk mentioned
- 0.5-0.6: Partial coverage; some bottleneck awareness
- 0.3-0.4: Minimal analysis
- 0.0-0.2: No meaningful analysis
"""

    _REBALANCE_RUBRIC = """\
Evaluate the quality of task rebalancing recommendations (0.0-1.0).

## Expected elements
- Identify wang.fang as overloaded and recommend offloading tasks
- Suggest specific task transfers (who to whom)
- Address deadline conflict for microservice task
- Consider skill matching when recommending transfers

## Scoring tiers
- 0.9-1.0: Specific rebalancing plan; named task transfers; deadline mitigation strategy
- 0.7-0.8: General rebalancing; mentions offloading; some specifics
- 0.5-0.6: Mentions need for rebalancing but vague
- 0.3-0.4: Minimal suggestions
- 0.0-0.2: No rebalancing
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

        tool_penalty = self._tool_gate(dispatches)

        det_score = 0.0
        det_score += 0.25 * self._score_member_coverage(all_text)
        det_score += 0.25 * self._score_bottleneck(all_text, lower)
        det_score += 0.25 * self._score_meeting_data(lower)
        det_score += 0.25 * self._score_risk_detection(lower)

        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            analysis_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._ANALYSIS_RUBRIC
            ).score
            rebalance_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._REBALANCE_RUBRIC
            ).score
        else:
            analysis_score = self._fallback_analysis(all_text, lower)
            rebalance_score = self._fallback_rebalance(lower)

        completion = tool_penalty * (
            0.55 * det_score
            + 0.20 * analysis_score
            + 0.25 * rebalance_score
        )

        scores.completion = min(round(completion, 2), 1.0)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len(
            [m for m in messages if m.message.role == "assistant"]
        )
        return scores

    def _tool_gate(self, dispatches: list[ToolDispatch]) -> float:
        cal = [d for d in dispatches if d.tool_name == "calendar_list_events" and d.response_status < 400]
        todo = [d for d in dispatches if d.tool_name in ("todo_list_tasks", "todo_get_task") and d.response_status < 400]
        if not cal and not todo:
            return 0.2
        if not cal or not todo:
            return 0.5
        return 1.0

    def _score_member_coverage(self, all_text: str) -> float:
        members = ["zhang.lei", "wang.fang", "li.na", "chen.wei", "zhao.peng"]
        found = sum(1 for m in members if m in all_text)
        return min(found / 4, 1.0)

    def _score_bottleneck(self, all_text: str, lower: str) -> float:
        bottleneck_kw = ["bottleneck", "overload", "heaviest", "highest workload",
                          "busiest", "most", "over-capacity"]
        if "wang.fang" in all_text and any(kw in lower for kw in bottleneck_kw):
            return 1.0
        if "wang.fang" in all_text:
            return 0.3
        return 0.0

    def _score_meeting_data(self, lower: str) -> float:
        meeting_kw = ["meeting", "7 hour", "7h", "5 meeting", "5 session"]
        if any(kw in lower for kw in meeting_kw):
            return 1.0
        return 0.0

    def _score_risk_detection(self, lower: str) -> float:
        risk_kw = ["microservice", "april 1", "deadline", "conflict", "tight",
                    "at risk", "overdue"]
        found = sum(1 for kw in risk_kw if kw in lower)
        return min(found / 2, 1.0)

    def _fallback_analysis(self, all_text: str, lower: str) -> float:
        """_fallback_: dev-only."""
        score = 0.0
        members = ["zhang.lei", "wang.fang", "li.na", "chen.wei", "zhao.peng"]
        score += 0.30 * min(sum(1 for m in members if m in all_text) / 4, 1.0)
        if "wang.fang" in all_text and any(kw in lower for kw in ["bottleneck", "overload", "heaviest"]):
            score += 0.35
        if any(kw in lower for kw in ["microservice", "deadline", "april 1"]):
            score += 0.35
        return min(score, 1.0)

    def _fallback_rebalance(self, lower: str) -> float:
        """_fallback_: dev-only."""
        score = 0.0
        rebalance_kw = ["rebalance", "reassign", "redistribute", "offload",
                         "delegate", "transfer"]
        if any(kw in lower for kw in rebalance_kw):
            score += 0.60
        if any(kw in lower for kw in ["recommend", "suggest", "plan"]):
            score += 0.40
        return min(score, 1.0)
