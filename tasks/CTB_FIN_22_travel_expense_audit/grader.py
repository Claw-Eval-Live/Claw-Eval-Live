"""CTB_FIN_22 grader -- travel expense compliance audit.

Ground truth source: task.yaml reference_solution + fixtures.

v2.2: analysis mode (financial audit report).
- Deterministic 40%: tool gate, flagged employees, excess total, department breakdown
- Judge 60%: audit accuracy, report quality
- Fallback: keyword-based scoring when judge unavailable

Ground truth:
  4 over-limit: James Wilson 12500 (over 4500), David Park 15000 (over 7000),
                Robert Taylor 22000 (over 7000), Linda Huang 9500 (over 1500)
  Total excess: 20,000
  By department: Sales 19700, Marketing 16000, Engineering 20800, Executive 22000
  Worst: David Park and Robert Taylor tied at 7000 over
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class Grader(AbstractGrader):
    """Grade travel expense compliance audit."""

    @staticmethod
    def _has_bounded(text: str, num: str) -> bool:
        return bool(re.search(rf'(?<!\d){re.escape(num)}(?!\d)', text))

    FLAGGED_EMPLOYEES = [
        ("James Wilson", "张伟"),
        ("David Park", "王强"),
        ("Robert Taylor", "陈磊"),
        ("Linda Huang", "刘芳"),
    ]

    # ── Judge rubrics ──────────────────────────────────────────────

    _AUDIT_ACCURACY_RUBRIC = """\
Evaluate the accuracy of the travel expense compliance audit (0.0-1.0).

## Ground Truth -- Over-limit records
1. James Wilson: Amount 12,500, Limit 8,000, Over by 4,500
2. David Park: Amount 15,000, Limit 8,000, Over by 7,000
3. Robert Taylor: Amount 22,000, Limit 15,000, Over by 7,000
4. Linda Huang: Amount 9,500, Limit 8,000, Over by 1,500

Total excess amount: 4,500 + 7,000 + 7,000 + 1,500 = 20,000

## Department totals
- Sales: 19,700
- Marketing: 16,000
- Engineering: 20,800
- Executive: 22,000

## Most severe violator: David Park and Robert Taylor (tied at 7,000 over)

## Scoring tiers
- 0.9-1.0: All 4 employees flagged with correct excess amounts; total 20K; department breakdown; worst identified
- 0.7-0.8: All flagged; total approximately correct; some department data
- 0.5-0.6: 3-4 employees flagged; partial totals
- 0.3-0.4: 1-2 employees flagged
- 0.0-0.2: No meaningful audit
"""

    _REPORT_QUALITY_RUBRIC = """\
Evaluate the quality of the audit report (0.0-1.0).

## Expected elements
1. Complete list of all travel expenses with employee, department, amount, and limit
2. Clear flagging of non-compliant records (amount > limit)
3. Excess amount calculated per employee
4. Department summary totals
5. Identification of most severe violator(s)

## Scoring tiers
- 0.9-1.0: All elements present; clear tabular format; actionable findings
- 0.7-0.8: Most elements; reasonable structure
- 0.5-0.6: Partial elements; some structure
- 0.3-0.4: Minimal report
- 0.0-0.2: No meaningful report
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
        all_text = self._get_all_assistant_text(messages)
        clean = all_text.replace(",", "").replace("\uff0c", "")

        # 1. Tool gate
        tool_penalty = self._tool_gate(dispatches)

        # 2. Deterministic checks (40%)
        det_score = 0.0
        det_score += 0.30 * self._score_flagged_employees(all_text)
        det_score += 0.25 * self._score_excess_total(clean)
        det_score += 0.25 * self._score_department_breakdown(clean)
        det_score += 0.20 * self._score_worst_violator(all_text, clean)

        # 3. Judge scoring (60%)
        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            audit_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._AUDIT_ACCURACY_RUBRIC
            ).score
            report_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._REPORT_QUALITY_RUBRIC
            ).score
        else:
            audit_score = self._fallback_audit(all_text, clean)
            report_score = self._fallback_report(all_text)

        # 4. Combine
        completion = tool_penalty * (
            0.40 * det_score
            + 0.30 * audit_score
            + 0.30 * report_score
        )

        scores.completion = min(round(completion, 2), 1.0)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len(
            [m for m in messages if m.message.role == "assistant"]
        )
        return scores

    # ── Deterministic helpers ──────────────────────────────────────

    def _tool_gate(self, dispatches: list[ToolDispatch]) -> float:
        calls = [d for d in dispatches
                 if d.tool_name == "finance_list_transactions" and d.response_status < 400]
        return 1.0 if calls else 0.3

    def _score_flagged_employees(self, all_text: str) -> float:
        flagged = 0
        for en, zh in self.FLAGGED_EMPLOYEES:
            if en in all_text or zh in all_text:
                flagged += 1
        return min(flagged / 4, 1.0)

    def _score_excess_total(self, clean: str) -> float:
        if self._has_bounded(clean, "20000"):
            return 1.0
        return 0.0

    def _score_department_breakdown(self, clean: str) -> float:
        dept_nums = ["19700", "20800", "16000", "22000"]
        found = sum(1 for n in dept_nums if self._has_bounded(clean, n))
        return min(found / 3, 1.0)

    def _score_worst_violator(self, all_text: str, clean: str) -> float:
        if any(k in all_text for k in ["David Park", "Robert Taylor"]):
            if self._has_bounded(clean, "7000"):
                return 1.0
        return 0.0

    # ── Fallback scorers ───────────────────────────────────────────

    def _fallback_audit(self, all_text: str, clean: str) -> float:
        """_fallback_: dev-only."""
        score = 0.0
        flagged = sum(1 for en, zh in self.FLAGGED_EMPLOYEES if en in all_text or zh in all_text)
        score += 0.30 * min(flagged / 4, 1.0)
        if "20000" in clean:
            score += 0.15
        if "7000" in clean:
            score += 0.10
        lower = all_text.lower()
        if any(k in lower for k in ["exceed", "over limit", "non-compliant", "violation"]):
            score += 0.10
        return min(score, 1.0)

    def _fallback_report(self, all_text: str) -> float:
        """_fallback_: dev-only."""
        score = 0.0
        if "|" in all_text and "---" in all_text:
            score += 0.20
        if len(all_text.strip()) >= 300:
            score += 0.15
        lower = all_text.lower()
        if any(k in lower for k in ["department", "summary"]):
            score += 0.15
        return min(score, 1.0)
