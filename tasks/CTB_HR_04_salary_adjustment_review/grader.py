"""CTB_HR_04 grader -- salary adjustment review.

Ground truth source: task.yaml reference_solution + fixtures.

v2.2: Claw-Eval mode (HR analysis report).
- Deterministic 35%: tool gate, employee coverage, VP approval flags, budget compliance
- Judge 65%: salary data accuracy, compliance analysis, report quality
- Fallback: keyword-based scoring when judge unavailable

Ground truth:
  Engineering: Li Qiang +5000 (20% >15% VP), Zhao Yang +3000 (16.7% >15% VP),
    Qian Xiaolei +2000 (13.3%). Total 10K, budget 15K.
  Marketing: Wu Fang +4000 (18.2% >15% VP), Lin Zhiming +2000 (16.7% >15% VP).
    Total 6K, budget 6K (at limit).
  Finance: Zheng Tao +2000 (12.5% <15%). Total 2K, budget 3K.
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class Grader(AbstractGrader):
    """Grade salary adjustment review report."""

    _SALARY_DATA_RUBRIC = """\
Evaluate the accuracy of per-employee salary adjustment data (0.0-1.0).

## Ground Truth
- Li Qiang (Engineering): +5,000/month (20% increase, exceeds 15% threshold -> needs VP approval)
- Zhao Yang (Engineering): +3,000/month (16.7% increase, exceeds 15% -> needs VP approval)
- Qian Xiaolei (Engineering): +2,000/month (13.3% increase, within normal range)
- Wu Fang (Marketing): +4,000/month (18.2% increase, exceeds 15% -> needs VP approval)
- Lin Zhiming (Marketing): +2,000/month (16.7% increase, exceeds 15% -> needs VP approval)
- Zheng Tao (Finance): +2,000/month (12.5% increase, within normal range)

## Scoring tiers
- 0.9-1.0: All 6 employees with correct increase amounts and percentages
- 0.7-0.8: 5-6 employees; most amounts correct
- 0.5-0.6: 3-4 employees; some data correct
- 0.3-0.4: Partial coverage
- 0.0-0.2: No meaningful data
"""

    _COMPLIANCE_RUBRIC = """\
Evaluate the compliance analysis quality (0.0-1.0).

## Ground Truth
- 4 employees exceed the 15% threshold: Li Qiang (20%), Zhao Yang (16.7%), Wu Fang (18.2%), Lin Zhiming (16.7%)
- These 4 require VP approval
- Department budget compliance: Engineering 10K/15K (within), Marketing 6K/6K (at limit), Finance 2K/3K (within)
- Overall total: 18K/month
- Classification: Approve, Approve with modification, or Defer for each

## Scoring tiers
- 0.9-1.0: All VP flags correct; all budget comparisons; clear approve/defer recommendations
- 0.7-0.8: Most VP flags; budget analysis present; some recommendations
- 0.5-0.6: Partial VP identification; some budget data
- 0.3-0.4: Minimal compliance analysis
- 0.0-0.2: No compliance analysis
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
        clean = all_text.replace(",", "").replace("\uff0c", "")

        # 1. Tool gate
        tool_penalty = self._tool_gate(dispatches)

        # 2. Deterministic checks (35%)
        det_score = 0.0
        det_score += 0.30 * self._score_employee_coverage(all_text)
        det_score += 0.35 * self._score_vp_flags(all_text, lower)
        det_score += 0.35 * self._score_budget_compliance(all_text, lower, clean)

        # 3. Judge scoring (65%)
        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            salary_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._SALARY_DATA_RUBRIC
            ).score
            compliance_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._COMPLIANCE_RUBRIC
            ).score
        else:
            salary_score = self._fallback_salary(all_text, clean)
            compliance_score = self._fallback_compliance(all_text, lower)

        completion = tool_penalty * (
            0.35 * det_score
            + 0.35 * salary_score
            + 0.30 * compliance_score
        )

        scores.completion = min(round(completion, 2), 1.0)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len(
            [m for m in messages if m.message.role == "assistant"]
        )
        return scores

    def _tool_gate(self, dispatches: list[ToolDispatch]) -> float:
        gmail_calls = [d for d in dispatches
                       if d.tool_name in ("gmail_list_messages", "gmail_get_message")
                       and d.response_status < 400]
        crm_calls = [d for d in dispatches
                     if d.tool_name in ("crm_list_customers", "crm_get_customer")
                     and d.response_status < 400]
        if not gmail_calls and not crm_calls:
            return 0.2
        if not gmail_calls or not crm_calls:
            return 0.5
        return 1.0

    def _score_employee_coverage(self, all_text: str) -> float:
        employees = ["Li Qiang", "Zhao Yang", "Qian Xiaolei", "Wu Fang",
                      "Lin Zhiming", "Zheng Tao"]
        found = sum(1 for e in employees if e in all_text)
        return min(found / 5, 1.0)

    def _score_vp_flags(self, all_text: str, lower: str) -> float:
        vp_names = ["Li Qiang", "Zhao Yang", "Wu Fang", "Lin Zhiming"]
        vp_kw = ["15%", "vp", "special approval", "requires approval", "exceed"]
        flagged = 0
        for name in vp_names:
            if name in all_text and any(kw in lower for kw in vp_kw):
                flagged += 1
        return min(flagged / 3, 1.0)

    def _score_budget_compliance(self, all_text: str, lower: str, clean: str) -> float:
        score = 0.0
        dept_data = [
            (["engineering", "tech"], ["10000", "15000"]),
            (["marketing"], ["6000"]),
            (["finance"], ["2000", "3000"]),
        ]
        for dept_kw, values in dept_data:
            if any(d in lower for d in dept_kw):
                if any(v in clean for v in values):
                    score += 1.0 / len(dept_data)
        return min(score, 1.0)

    def _fallback_salary(self, all_text: str, clean: str) -> float:
        """_fallback_: dev-only."""
        score = 0.0
        employees = ["Li Qiang", "Zhao Yang", "Qian Xiaolei", "Wu Fang",
                      "Lin Zhiming", "Zheng Tao"]
        score += 0.30 * min(sum(1 for e in employees if e in all_text) / 5, 1.0)
        amounts = ["5000", "3000", "2000", "4000"]
        score += 0.40 * min(sum(1 for a in amounts if a in clean) / 3, 1.0)
        if "15%" in all_text:
            score += 0.30
        return min(score, 1.0)

    def _fallback_compliance(self, all_text: str, lower: str) -> float:
        """_fallback_: dev-only."""
        score = 0.0
        comp_kw = ["compliant", "compliance", "exceed", "within budget",
                    "budget", "approval", "vp"]
        found = sum(1 for kw in comp_kw if kw in lower)
        score += 0.50 * min(found / 2, 1.0)
        if "approve" in lower or "defer" in lower:
            score += 0.30
        if "|" in all_text and "---" in all_text:
            score += 0.20
        return min(score, 1.0)
