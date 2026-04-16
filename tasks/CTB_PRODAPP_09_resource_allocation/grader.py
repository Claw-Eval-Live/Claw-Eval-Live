"""CTB_PRODAPP_09 grader -- resource allocation analysis.

Ground truth source: task.yaml reference_solution is empty for this task.
Rubric ground truth derived from fixture data:
  - fixtures/calendar/events.json, fixtures/todo/tasks.json

v2.2: WildClawBench mode (operation + analysis).
- Deterministic 55%: tool gate, overloaded member, underloaded member, rebalance action
- Judge 45%: workload analysis quality, reallocation plan quality
- Fallback: keyword-based scoring when judge unavailable

Ground truth (from fixtures):
  Wang Qiang: overloaded (most tasks + most meetings)
  Zhao Min: overloaded (heavy tasks)
  Zhou Fang / Chen Lei: underloaded (fewer tasks, available capacity)
  Meeting density and scheduling conflicts on 4/6
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class Grader(AbstractGrader):
    """Grade resource allocation analysis with reallocation recommendations."""

    _WORKLOAD_RUBRIC = """\
Evaluate the accuracy of per-person workload analysis (0.0-1.0).

## Ground Truth
- Wang Qiang: heaviest load -- multiple tasks + most meeting commitments
- Zhao Min: also overloaded -- heavy task hours (~20h)
- Zhou Fang and/or Chen Lei: lighter load, available capacity

## Scoring tiers
- 0.9-1.0: All members analyzed with task hours + meeting hours; overload/underload correctly identified
- 0.7-0.8: Key overloaded/underloaded members identified; reasonable numbers
- 0.5-0.6: Partial member coverage; some overload detection
- 0.3-0.4: Minimal analysis; 1-2 members
- 0.0-0.2: No workload analysis
"""

    _REALLOCATION_RUBRIC = """\
Evaluate the quality of task redistribution recommendations (0.0-1.0).

## Expected recommendations
- Transfer tasks from Wang Qiang to less loaded members
- Consider meeting density when suggesting transfers
- Identify specific tasks suitable for redistribution
- Account for scheduling conflicts on 4/6

## Scoring tiers
- 0.9-1.0: Specific task transfers recommended; from/to named; scheduling considered
- 0.7-0.8: Reasonable transfer suggestions; source/target identified
- 0.5-0.6: Generic rebalancing suggestion; partial specifics
- 0.3-0.4: Mentions imbalance but no concrete plan
- 0.0-0.2: No reallocation recommendation
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
        det_score += 0.20 * self._score_data_retrieval(dispatches, audit_data)
        det_score += 0.30 * self._score_overloaded(all_text, lowered)
        det_score += 0.20 * self._score_underloaded(all_text, lowered)
        det_score += 0.30 * self._score_rebalance_action(lowered)

        # 3. Judge scoring (45%)
        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            workload_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._WORKLOAD_RUBRIC
            ).score
            realloc_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._REALLOCATION_RUBRIC
            ).score
        else:
            workload_score = self._fallback_workload(all_text, lowered)
            realloc_score = self._fallback_realloc(lowered)

        # 4. Combine
        completion = tool_penalty * (
            0.55 * det_score
            + 0.20 * workload_score
            + 0.25 * realloc_score
        )

        scores.completion = min(round(completion, 2), 1.0)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len(
            [m for m in messages if m.message.role == "assistant"]
        )
        return scores

    # -- Deterministic helpers --

    def _tool_gate(self, dispatches: list[ToolDispatch]) -> float:
        cal = any(d.tool_name in ("calendar_list_events", "calendar_get_event")
                  and d.response_status < 400 for d in dispatches)
        todo = any(d.tool_name in ("todo_list_tasks", "todo_update_task")
                   and d.response_status < 400 for d in dispatches)
        if not cal and not todo:
            return 0.2
        if not cal or not todo:
            return 0.5
        return 1.0

    def _score_data_retrieval(self, dispatches: list[ToolDispatch],
                              audit_data: dict | None) -> float:
        cal_ok = any(d.tool_name in ("calendar_list_events", "calendar_get_event")
                     and d.response_status < 400 for d in dispatches)
        todo_ok = any(d.tool_name in ("todo_list_tasks", "todo_update_task")
                      and d.response_status < 400 for d in dispatches)
        score = sum([cal_ok, todo_ok]) / 2.0
        if audit_data:
            audit_ok = (audit_data.get("calendar", {}).get("calls") and
                        audit_data.get("todo", {}).get("calls"))
            if audit_ok:
                score = min(score + 0.2, 1.0)
        return score

    def _score_overloaded(self, text: str, lowered: str) -> float:
        """Check Wang Qiang and/or Zhao Min identified as overloaded."""
        score = 0.0
        overload_kws = ["\u8fc7\u91cd", "\u8fc7\u9ad8", "\u8d85\u8d1f\u8377",
                        "overload", "\u6700\u591a", "\u8fc7\u8f7d"]
        if "\u738b\u5f3a" in text or any(n in lowered for n in ["wang qiang", "qiang wang"]):
            score += 0.3
            if any(kw in lowered for kw in overload_kws):
                score += 0.3
        if "\u8d75\u654f" in text or any(n in lowered for n in ["zhao min", "min zhao"]):
            score += 0.2
            if "20" in text or any(kw in text for kw in ["\u8d1f\u8377", "\u4efb\u52a1"]):
                score += 0.2
        return min(score, 1.0)

    def _score_underloaded(self, text: str, lowered: str) -> float:
        """Check Zhou Fang or Chen Lei identified as available."""
        if any(n in text for n in ["\u5468\u82b3", "\u9648\u78ca"]) or \
           any(n in lowered for n in ["zhou fang", "chen lei"]):
            return 1.0
        return 0.0

    def _score_rebalance_action(self, lowered: str) -> float:
        realloc_kws = ["\u5206\u914d", "\u8c03\u6574", "\u8f6c\u79fb", "\u59d4\u6d3e",
                       "\u5747\u8861", "\u4f18\u5316", "redistribute", "rebalance",
                       "delegate", "transfer", "reallocat"]
        return 1.0 if any(k in lowered for k in realloc_kws) else 0.0

    # -- Fallback scorers --

    def _fallback_workload(self, text: str, lowered: str) -> float:
        """_fallback_: dev-only keyword scoring for workload analysis."""
        score = 0.0
        if "\u738b\u5f3a" in text:
            score += 0.30
        if "\u8d75\u654f" in text:
            score += 0.20
        if "\u5468\u82b3" in text or "\u9648\u78ca" in text:
            score += 0.20
        if any(kw in lowered for kw in ["\u51b2\u7a81", "\u5bc6\u96c6", "conflict", "overlap"]):
            score += 0.15
        if any(kw in lowered for kw in ["\u4f1a\u8bae", "meeting", "\u4efb\u52a1", "task"]):
            score += 0.15
        return min(score, 1.0)

    def _fallback_realloc(self, lowered: str) -> float:
        """_fallback_: dev-only keyword scoring for reallocation."""
        score = 0.0
        if any(k in lowered for k in ["\u8f6c\u79fb", "\u59d4\u6d3e", "transfer", "delegate"]):
            score += 0.40
        if any(k in lowered for k in ["\u5747\u8861", "\u4f18\u5316", "balance", "optimize"]):
            score += 0.30
        if any(k in lowered for k in ["\u5efa\u8bae", "recommend", "suggest"]):
            score += 0.30
        return min(score, 1.0)
