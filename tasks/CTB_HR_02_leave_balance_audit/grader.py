"""CTB_HR_02 grader -- annual leave balance audit.

Ground truth source: task.yaml reference_solution + fixtures.

v2.2: analysis mode (HR audit report).
- Deterministic 35%: tool gate, employee coverage, discrepancy detection, Wang Min case
- Judge 65%: audit accuracy, approval recommendations, report quality
- Fallback: keyword-based scoring when judge unavailable

Ground truth:
  Zhao Li (EMP-201): Claimed 8, actual 6, discrepancy 2. Requested 5, actual 6 -> approve
  Sun Qiang (EMP-202): Claimed 10, actual 10, consistent. Requested 3 -> approve
  Wang Min (EMP-203): Claimed 5, actual 5, consistent. Requested 7 exceeds balance,
    comp-time 1 day, still short 1 day
  Huang Lei (EMP-204): Claimed 6, actual 4, discrepancy 2. Requested 2, actual 4 -> approve
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class Grader(AbstractGrader):
    """Grade annual leave balance audit report."""

    _AUDIT_ACCURACY_RUBRIC = """\
Evaluate the accuracy of the leave balance audit for all employees (0.0-1.0).

## Ground Truth
- Zhao Li (EMP-201): Claimed 8 days, actual 6 days, discrepancy of 2 days. Requested 5 days, can approve (6 >= 5).
- Sun Qiang (EMP-202): Claimed 10 days, actual 10 days, consistent. Requested 3 days, can approve.
- Wang Min (EMP-203): Claimed 5 days, actual 5 days, consistent. Requested 7 days exceeds balance. Comp-time balance only 1 day, still short 1 day. Cannot fully approve.
- Huang Lei (EMP-204): Claimed 6 days, actual 4 days, discrepancy of 2 days. Requested 2 days, can approve (4 >= 2).

## Scoring tiers
- 0.9-1.0: All 4 employees with correct claimed vs actual; all discrepancies flagged correctly
- 0.7-0.8: All employees covered; most balance data correct
- 0.5-0.6: 3+ employees; some balance data correct
- 0.3-0.4: Partial coverage
- 0.0-0.2: No meaningful audit data
"""

    _RECOMMENDATION_RUBRIC = """\
Evaluate the quality of approval recommendations (0.0-1.0).

## Ground Truth
- Zhao Li: APPROVE (actual balance 6 >= requested 5)
- Sun Qiang: APPROVE (balance 10 >= requested 3)
- Wang Min: CANNOT fully approve (5 + 1 comp = 6 < 7 requested). Recommend partial approval or alternative.
- Huang Lei: APPROVE (actual balance 4 >= requested 2)

Key insight: Wang Min's case requires special handling -- comp-time of 1 day is insufficient to cover the gap.

## Scoring tiers
- 0.9-1.0: All 4 decisions correct; Wang Min's comp-time analysis detailed; clear recommendations
- 0.7-0.8: Most decisions correct; Wang Min flagged as problematic
- 0.5-0.6: Some decisions correct; partial Wang Min analysis
- 0.3-0.4: Minimal decision-making
- 0.0-0.2: No recommendations
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

        # 1. Tool gate
        tool_penalty = self._tool_gate(dispatches)

        # 2. Deterministic checks (35%)
        det_score = 0.0
        det_score += 0.30 * self._score_employee_coverage(all_text)
        det_score += 0.35 * self._score_discrepancy_detection(all_text, lower)
        det_score += 0.35 * self._score_wang_min_case(all_text, lower)

        # 3. Judge scoring (65%)
        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            audit_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._AUDIT_ACCURACY_RUBRIC
            ).score
            rec_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._RECOMMENDATION_RUBRIC
            ).score
        else:
            audit_score = self._fallback_audit(all_text, lower)
            rec_score = self._fallback_recommendation(all_text, lower)

        # 4. Combine
        completion = tool_penalty * (
            0.35 * det_score
            + 0.35 * audit_score
            + 0.30 * rec_score
        )

        scores.completion = min(round(completion, 2), 1.0)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len(
            [m for m in messages if m.message.role == "assistant"]
        )
        return scores

    # ── Deterministic helpers ──────────────────────────────────────

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
        employees = ["Zhao Li", "Sun Qiang", "Wang Min", "Huang Lei"]
        found = sum(1 for e in employees if e in all_text)
        return found / len(employees)

    def _score_discrepancy_detection(self, all_text: str, lower: str) -> float:
        score = 0.0
        disc_kw = ["discrepanc", "inconsisten", "mismatch", "differ"]
        # Zhao Li: claimed 8, actual 6
        if "Zhao Li" in all_text and any(kw in lower for kw in disc_kw):
            score += 0.5
        # Huang Lei: claimed 6, actual 4
        if "Huang Lei" in all_text and any(kw in lower for kw in disc_kw):
            score += 0.5
        return min(score, 1.0)

    def _score_wang_min_case(self, all_text: str, lower: str) -> float:
        score = 0.0
        if "Wang Min" not in all_text:
            return 0.0
        score += 0.3  # mentioned
        exceed_kw = ["insufficient", "exceed", "not enough", "short", "cannot"]
        if any(kw in lower for kw in exceed_kw):
            score += 0.35
        comp_kw = ["comp-time", "comp time", "compensat", "1 day"]
        if any(kw in lower for kw in comp_kw):
            score += 0.35
        return min(score, 1.0)

    # ── Fallback scorers ───────────────────────────────────────────

    def _fallback_audit(self, all_text: str, lower: str) -> float:
        """_fallback_: dev-only."""
        score = 0.0
        employees = ["Zhao Li", "Sun Qiang", "Wang Min", "Huang Lei"]
        score += 0.30 * (sum(1 for e in employees if e in all_text) / 4)
        if any(kw in lower for kw in ["discrepanc", "inconsisten", "mismatch"]):
            score += 0.30
        if any(kw in lower for kw in ["approv", "deny", "reject"]):
            score += 0.20
        if "|" in all_text and "---" in all_text:
            score += 0.20
        return min(score, 1.0)

    def _fallback_recommendation(self, all_text: str, lower: str) -> float:
        """_fallback_: dev-only."""
        score = 0.0
        if "Wang Min" in all_text and any(kw in lower for kw in ["insufficient", "exceed"]):
            score += 0.40
        if any(kw in lower for kw in ["comp-time", "comp time", "compensat"]):
            score += 0.25
        appr_kw = ["approv", "deny", "denied", "reject", "recommend"]
        found = sum(1 for kw in appr_kw if kw in lower)
        score += 0.35 * min(found / 2, 1.0)
        return min(score, 1.0)
