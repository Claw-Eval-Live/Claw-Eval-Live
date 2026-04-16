"""CTB_PRODAPP_22 grader — team workload balance analysis.

Ground truth source: task.yaml reference_solution is empty for this task.
Rubric ground truth derived from fixture data:   - fixtures/calendar/events.json,  - fixtures/todo/tasks.json,completion

v2.2: WildClawBench mode (operation + analysis).
- Deterministic 55%: tool gate, workload numbers, member identification,
                      overload/underload detection, rebalance action
- Judge 45%: analysis quality rubric, rebalance plan rubric
- Fallback: keyword-based scoring when judge unavailable

Ground truth (from calendar + todo fixtures, April 6-8):
  Meeting hours:
    Lu Xin: 8.5h (evt_001=1h + evt_002=2h + evt_003=2h + evt_004=2h + evt_006=1.5h)
    Xia Lin: 3h (evt_001=1h + evt_004=2h)
    Jia Wei: 6h (evt_001=1h + evt_002=2h + evt_005=1.5h + evt_006=1.5h)
    Cui Jing: 2.5h (evt_001=1h + evt_005=1.5h)
  Task hours:
    Lu Xin: 40h (todo_001=30h + todo_002=10h) -- OVERLOADED
    Xia Lin: 8h (todo_003=8h)
    Jia Wei: 6h (todo_004=6h)
    Cui Jing: 5h (todo_005=5h) -- UNDERLOADED
  Recommendation: transfer API doc task (todo_002, 10h) from Lu Xin to Cui Jing
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class WorkloadBalanceGrader(AbstractGrader):
    """Grade a workload balance analysis with rebalance plan."""

    # ── Judge rubrics ──────────────────────────────────────────────

    _ANALYSIS_RUBRIC = """\
Evaluate the accuracy of workload analysis for all team members (0.0-1.0).

## Ground Truth (April 6-8)
### Meeting hours
- Lu Xin (卢鑫): 8.5h — most meetings by far
- Xia Lin (夏琳): 3h
- Jia Wei (贾伟): 6h
- Cui Jing (崔静): 2.5h — fewest meetings

### Task hours (estimated_hours from todo)
- Lu Xin: 40h (microservice refactor 30h + API doc 10h) — severely overloaded
- Xia Lin: 8h (frontend component)
- Jia Wei: 6h (DB index optimization)
- Cui Jing: 5h (deployment script) — most available

### Total workload
- Lu Xin: ~48.5h — extreme overload
- Xia Lin: ~11h
- Jia Wei: ~12h
- Cui Jing: ~7.5h — significantly underloaded

## Scoring tiers
- 0.9-1.0: All 4 members with correct meeting + task breakdown; total hours close to ground truth
- 0.7-0.8: All members covered; numbers mostly correct (within ~20%)
- 0.5-0.6: 3+ members covered; some numbers correct
- 0.3-0.4: Partial coverage; significant errors
- 0.0-0.2: No meaningful workload analysis
"""

    _REBALANCE_RUBRIC = """\
Evaluate the quality of the rebalance plan (0.0-1.0).

## Expected rebalance
- Key insight: Lu Xin is severely overloaded (48.5h), Cui Jing is underloaded (7.5h)
- Primary recommendation: Transfer API doc task (10h) from Lu Xin to Cui Jing
- This reduces Lu Xin to ~38.5h (still high but better) and brings Cui Jing to ~17.5h

## Scoring tiers
- 0.9-1.0: Correctly identifies Lu Xin overload + Cui Jing underload; proposes API doc transfer;
            quantifies the improvement; considers skills/priority
- 0.7-0.8: Identifies the imbalance; proposes reasonable transfer; some quantification
- 0.5-0.6: Identifies imbalance but vague plan; or wrong transfer target
- 0.3-0.4: Mentions imbalance but no concrete plan
- 0.0-0.2: No rebalance recommendation
"""

    # ── Main grading ──────────────────────────────────────────────

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
        final_text = self._get_final_assistant_text(messages)
        clean = final_text.replace(",", "").replace("\uff0c", "")
        lowered = final_text.lower()

        # 1. Tool gate (deterministic)
        tool_penalty = self._tool_gate(dispatches)

        # 2. Deterministic checks (55%)
        det_score = 0.0
        det_score += 0.25 * self._score_member_coverage(final_text)         # all 4 members
        det_score += 0.25 * self._score_overload_detection(final_text, lowered)  # Lu Xin overloaded
        det_score += 0.15 * self._score_underload_detection(final_text, lowered) # Cui Jing underloaded
        det_score += 0.20 * self._score_workload_numbers(clean, final_text)      # key numbers
        det_score += 0.15 * self._score_json_structure(lowered)             # JSON format

        # 3. Judge scoring (45%)
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
            analysis_score = self._fallback_analysis(clean, final_text, lowered)
            rebalance_score = self._fallback_rebalance(final_text, lowered)

        # 4. Combine
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

    # ── Deterministic helpers ──────────────────────────────────────

    @staticmethod
    def _has_bounded(text: str, num: str) -> bool:
        return bool(re.search(rf'(?<!\d){re.escape(num)}(?!\d)', text))

    def _tool_gate(self, dispatches: list[ToolDispatch]) -> float:
        """Penalty multiplier: did the agent read calendar AND todo data?"""
        cal_calls = [d for d in dispatches
                     if d.tool_name in ("calendar_list_events", "calendar_get_event")
                     and d.response_status < 400]
        todo_calls = [d for d in dispatches
                      if d.tool_name in ("todo_list_tasks", "todo_update_task")
                      and d.response_status < 400]
        if not cal_calls and not todo_calls:
            return 0.2
        if not cal_calls or not todo_calls:
            return 0.5
        return 1.0

    def _score_member_coverage(self, final_text: str) -> float:
        """Check that all 4 team members are mentioned."""
        members = ["\u5362\u946b", "\u590f\u7433", "\u8d3e\u4f1f", "\u5d14\u9759"]
        found = sum(1 for m in members if m in final_text)
        return found / 4.0

    def _score_overload_detection(self, final_text: str, lowered: str) -> float:
        """Check that Lu Xin is identified as overloaded."""
        score = 0.0
        if "\u5362\u946b" in final_text:
            score += 0.3
            overload_kw = ["\u8fc7\u91cd", "\u8fc7\u9ad8", "\u8d85\u8d1f\u8377",
                           "overload", "\u4e0d\u5747\u8861", "\u6700\u591a",
                           "\u8fc7\u8f7d", "\u8d85\u51fa"]
            if any(kw in lowered for kw in overload_kw):
                score += 0.4
            # Specific number: 40h tasks or 48.5h total or 8.5h meetings
            if (self._has_bounded(final_text, "40") or
                    self._has_bounded(final_text, "48.5") or
                    self._has_bounded(final_text, "48") or
                    self._has_bounded(final_text, "8.5")):
                score += 0.3
        return min(score, 1.0)

    def _score_underload_detection(self, final_text: str, lowered: str) -> float:
        """Check that Cui Jing is identified as underloaded."""
        score = 0.0
        if "\u5d14\u9759" in final_text:
            score += 0.4
            underload_kw = ["\u8f7b", "\u5c11", "\u4f4e", "\u7a7a\u95f2",
                            "underload", "\u53ef\u7528", "\u6700\u5c11",
                            "\u8f83\u4f4e"]
            if any(kw in lowered for kw in underload_kw):
                score += 0.6
        return min(score, 1.0)

    def _score_workload_numbers(self, clean: str, final_text: str) -> float:
        """Check for key workload numbers."""
        score = 0.0
        # Lu Xin meeting hours: 8.5h
        if self._has_bounded(final_text, "8.5"):
            score += 0.20
        # Lu Xin task hours: 40h
        if self._has_bounded(clean, "40") and "\u5362\u946b" in final_text:
            score += 0.20
        # Cui Jing task: 5h
        if self._has_bounded(clean, "5") and "\u5d14\u9759" in final_text:
            score += 0.15
        # Xia Lin: 3h meetings or 8h tasks
        if (self._has_bounded(clean, "3") or self._has_bounded(clean, "8")) and "\u590f\u7433" in final_text:
            score += 0.15
        # Jia Wei: 6h meetings or 6h tasks
        if self._has_bounded(clean, "6") and "\u8d3e\u4f1f" in final_text:
            score += 0.15
        # API doc: 10h
        if self._has_bounded(clean, "10") and "API" in final_text:
            score += 0.15
        return min(score, 1.0)

    def _score_json_structure(self, lowered: str) -> float:
        """Check for requested JSON output structure."""
        json_keys = ["workload_summary", "imbalance_analysis", "rebalance_plan",
                     "meeting_hours", "task_hours", "total_hours"]
        found = sum(1 for kw in json_keys if kw in lowered)
        return min(found / 3, 1.0)

    # ── Fallback scorers ───────────────────────────────────────────

    def _fallback_analysis(self, clean: str, final_text: str, lowered: str) -> float:
        """_fallback_: dev-only keyword scoring for workload analysis."""
        score = 0.0
        members = ["\u5362\u946b", "\u590f\u7433", "\u8d3e\u4f1f", "\u5d14\u9759"]
        score += 0.25 * (sum(1 for m in members if m in final_text) / 4)
        if self._has_bounded(final_text, "8.5"):
            score += 0.10
        if self._has_bounded(clean, "40"):
            score += 0.10
        if self._has_bounded(clean, "48") or self._has_bounded(final_text, "48.5"):
            score += 0.10
        if "\u5fae\u670d\u52a1" in final_text or "todo_001" in lowered:
            score += 0.10
        tasks = ["API\u6587\u6863", "\u524d\u7aef\u8868\u5355", "\u7d22\u5f15\u4f18\u5316",
                 "\u90e8\u7f72\u811a\u672c"]
        score += 0.15 * (sum(1 for t in tasks if t in final_text) / 4)
        if "\u4f1a\u8bae" in final_text and "\u4efb\u52a1" in final_text:
            score += 0.10
        return min(score, 1.0)

    def _fallback_rebalance(self, final_text: str, lowered: str) -> float:
        """_fallback_: dev-only keyword scoring for rebalance plan."""
        score = 0.0
        transfer_kw = ["\u8f6c\u79fb", "\u59d4\u6d3e", "\u5206\u914d",
                       "transfer", "delegate", "\u8c03\u6574"]
        if any(k in lowered for k in transfer_kw):
            score += 0.25
        if "API\u6587\u6863" in final_text or "api\u6587\u6863" in lowered:
            score += 0.25
        if ("\u5362\u946b" in final_text and "\u5d14\u9759" in final_text and
                any(k in lowered for k in transfer_kw)):
            score += 0.25
        if self._has_bounded(final_text, "10") and any(
            k in final_text for k in ["\u5c0f\u65f6", "h", "\u5de5\u65f6"]
        ):
            score += 0.15
        if any(k in final_text for k in ["\u5747\u8861", "\u4f18\u5316"]):
            score += 0.10
        return min(score, 1.0)
