"""CTB_HR_10 grader -- benefits enrollment verification.

Ground truth source: task.yaml reference_solution + fixtures.

v2.2: Claw-Eval mode (HR audit report).
- Deterministic 35%: tool gate, employee coverage, violation identification
- Judge 65%: compliance assessment accuracy, violation detail quality
- Fallback: keyword-based scoring when judge unavailable

Ground truth:
  Zhang Wei (EMP-1001): Fully compliant. Annuity 5% = max limit.
  Liu Yang (EMP-1002): Child education subsidy non-compliant (joined June 2025, <2 years tenure).
  Chen Jing (EMP-1003): Rent subsidy non-compliant (already has housing fund, conflict).
  Zhao Li (EMP-1004): Plan C non-compliant (director level only, she is supervisor);
    annuity 8% exceeds 5% cap.
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class Grader(AbstractGrader):
    """Grade benefits enrollment verification report."""

    _COMPLIANCE_RUBRIC = """\
Evaluate the accuracy of per-employee compliance assessment (0.0-1.0).

## Ground Truth
- Zhang Wei (EMP-1001): COMPLIANT. Annuity at 5% = maximum allowed.
- Liu Yang (EMP-1002): NON-COMPLIANT. Child education subsidy requires 2+ years tenure; Liu Yang joined June 2025 (<2 years).
- Chen Jing (EMP-1003): NON-COMPLIANT. Rent subsidy conflicts with housing fund -- cannot receive both simultaneously.
- Zhao Li (EMP-1004): NON-COMPLIANT on two counts: (1) Plan C is for director level and above only -- she is a marketing supervisor; (2) Annuity rate 8% exceeds 5% cap, requires special approval.

## Scoring tiers
- 0.9-1.0: All 4 employees correctly assessed; all violation reasons explained
- 0.7-0.8: All employees covered; most violations identified
- 0.5-0.6: 3+ employees; some violations
- 0.3-0.4: Partial coverage
- 0.0-0.2: No meaningful assessment
"""

    _REPORT_RUBRIC = """\
Evaluate the quality of the verification report (0.0-1.0).

## Expected elements
- Enrollment completion statistics (1 compliant, 3 non-compliant)
- Specific violation rules cited for each non-compliant case
- Required follow-up actions per employee
- Benefit types mentioned (medical insurance, accident insurance, corporate annuity, etc.)
- Structured table format

## Scoring tiers
- 0.9-1.0: Complete statistics; all rules cited; clear follow-up actions; well-structured
- 0.7-0.8: Good coverage; most rules mentioned; reasonable structure
- 0.5-0.6: Partial coverage; some rules
- 0.3-0.4: Minimal report
- 0.0-0.2: No meaningful report
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
        det_score += 0.30 * self._score_employee_coverage(all_text)
        det_score += 0.40 * self._score_violations(all_text, lower)
        det_score += 0.30 * self._score_compliant_check(all_text, lower)

        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            comp_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._COMPLIANCE_RUBRIC
            ).score
            report_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._REPORT_RUBRIC
            ).score
        else:
            comp_score = self._fallback_compliance(all_text, lower)
            report_score = self._fallback_report(all_text, lower)

        completion = tool_penalty * (
            0.35 * det_score
            + 0.35 * comp_score
            + 0.30 * report_score
        )

        scores.completion = min(round(completion, 2), 1.0)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len(
            [m for m in messages if m.message.role == "assistant"]
        )
        return scores

    def _tool_gate(self, dispatches: list[ToolDispatch]) -> float:
        gmail = [d for d in dispatches if d.tool_name in ("gmail_list_messages", "gmail_get_message") and d.response_status < 400]
        crm = [d for d in dispatches if d.tool_name in ("crm_list_customers", "crm_get_customer") and d.response_status < 400]
        if not gmail and not crm:
            return 0.2
        if not gmail or not crm:
            return 0.5
        return 1.0

    def _score_employee_coverage(self, all_text: str) -> float:
        employees = ["Zhang Wei", "Liu Yang", "Chen Jing", "Zhao Li"]
        found = sum(1 for e in employees if e in all_text)
        return found / len(employees)

    def _score_violations(self, all_text: str, lower: str) -> float:
        score = 0.0
        # Liu Yang: child education + tenure
        if "Liu Yang" in all_text and any(kw in lower for kw in [
            "child education", "children education", "non-compliant", "not eligible",
            "tenure", "less than 2 year"
        ]):
            score += 0.33
        # Chen Jing: rent + housing fund conflict
        if "Chen Jing" in all_text and any(kw in lower for kw in [
            "rent", "housing fund", "provident fund", "conflict",
            "cannot simultaneously", "non-compliant"
        ]):
            score += 0.33
        # Zhao Li: Plan C level + annuity cap
        if "Zhao Li" in all_text and any(kw in lower for kw in [
            "plan c", "package c", "director", "level", "annuity",
            "pension", "8%", "5%", "exceed", "non-compliant"
        ]):
            score += 0.34
        return min(score, 1.0)

    def _score_compliant_check(self, all_text: str, lower: str) -> float:
        if "Zhang Wei" in all_text and any(kw in lower for kw in [
            "compliant", "pass", "eligible", "normal", "ok"
        ]):
            return 1.0
        if "Zhang Wei" in all_text:
            return 0.3
        return 0.0

    def _fallback_compliance(self, all_text: str, lower: str) -> float:
        """_fallback_: dev-only."""
        score = 0.0
        employees = ["Zhang Wei", "Liu Yang", "Chen Jing", "Zhao Li"]
        score += 0.30 * min(sum(1 for e in employees if e in all_text) / 3, 1.0)
        violations = ["non-compliant", "not eligible", "conflict", "exceed", "tenure"]
        score += 0.40 * min(sum(1 for v in violations if v in lower) / 2, 1.0)
        if "5%" in all_text and "8%" in all_text:
            score += 0.15
        if "child education" in lower or "rent" in lower:
            score += 0.15
        return min(score, 1.0)

    def _fallback_report(self, all_text: str, lower: str) -> float:
        """_fallback_: dev-only."""
        score = 0.0
        benefits = ["medical insurance", "health insurance", "accident insurance",
                      "annuity", "pension", "fitness"]
        score += 0.30 * min(sum(1 for b in benefits if b in lower) / 2, 1.0)
        if any(kw in lower for kw in ["recommend", "action", "adjust", "follow-up"]):
            score += 0.35
        if "|" in all_text and "---" in all_text:
            score += 0.20
        if len(all_text.strip()) >= 300:
            score += 0.15
        return min(score, 1.0)
