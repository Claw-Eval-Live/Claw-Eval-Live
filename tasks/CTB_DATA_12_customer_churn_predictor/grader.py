"""CTB_DATA_12 grader -- customer churn risk assessment.

Ground truth source: task.yaml reference_solution + fixtures.

v2.2: Claw-Eval mode (analysis report).
- Deterministic 35%: tool gate, risk classification, customer coverage
- Judge 65%: risk accuracy, retention strategy quality
- Fallback: keyword-based scoring when judge unavailable

Ground truth:
  High risk: A (low usage 12% + considering switch), B (VIP + 5 complaints + threatening),
             D (refund request + SLA breach + expiring)
  Low risk: C (satisfied + upsell intent)
  Total at-risk revenue: 750K
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class Grader(AbstractGrader):
    """Grade customer churn risk assessment."""

    CUSTOMERS = {
        "A": {"risk": "high", "kws": ["12%", "low usage", "switch", "considering"]},
        "B": {"risk": "high", "kws": ["5 complaint", "VIP", "threatening", "complaint"]},
        "C": {"risk": "low", "kws": ["satisfied", "upsell", "additional", "expand"]},
        "D": {"risk": "high", "kws": ["refund", "SLA", "breach", "expiring"]},
    }

    # ── Judge rubrics ──────────────────────────────────────────────

    _RISK_ACCURACY_RUBRIC = """\
Evaluate the accuracy of churn risk assessment for all 4 customers (0.0-1.0).

## Ground Truth
1. Customer A: HIGH risk -- usage rate only 12%, email mentions considering a switch to competitor
2. Customer B: HIGH risk -- VIP customer with 5 complaints, threatening to leave
3. Customer C: LOW risk -- satisfied, positive feedback, interested in upselling/expanding
4. Customer D: HIGH risk -- refund request, SLA breach, contract expiring soon

Total at-risk revenue (A+B+D): approximately 750K

## Scoring tiers
- 0.9-1.0: All 4 correctly classified with supporting evidence; at-risk revenue calculated
- 0.7-0.8: 3-4 correct; most evidence cited
- 0.5-0.6: 2-3 correct; some evidence
- 0.3-0.4: 1-2 correct
- 0.0-0.2: No meaningful assessment
"""

    _RETENTION_RUBRIC = """\
Evaluate the quality of retention strategies for high-risk customers (0.0-1.0).

## Expected strategies
- Customer A: Address low usage with training/onboarding; demonstrate value vs competitor
- Customer B: VIP escalation; resolve complaints; executive outreach
- Customer D: Resolve SLA breach; process refund; renewal negotiation
- Strategies should be specific to each customer's situation

## Scoring tiers
- 0.9-1.0: Specific retention plans for all 3 high-risk customers; addresses their unique concerns
- 0.7-0.8: Plans for most high-risk customers; mostly specific
- 0.5-0.6: Generic retention plans; some specificity
- 0.3-0.4: Vague mentions of retention
- 0.0-0.2: No retention strategies
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

        # 1. Tool gate
        tool_penalty = self._tool_gate(dispatches)

        # 2. Deterministic checks (35%)
        det_score = 0.0
        det_score += 0.25 * self._score_data_retrieval(dispatches)
        det_score += 0.45 * self._score_risk_classification(all_text)
        det_score += 0.30 * self._score_customer_coverage(all_text)

        # 3. Judge scoring (65%)
        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            risk_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._RISK_ACCURACY_RUBRIC
            ).score
            retention_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._RETENTION_RUBRIC
            ).score
        else:
            risk_score = self._fallback_risk(all_text)
            retention_score = self._fallback_retention(all_text)

        # 4. Combine
        completion = tool_penalty * (
            0.35 * det_score
            + 0.35 * risk_score
            + 0.30 * retention_score
        )

        scores.completion = min(round(completion, 2), 1.0)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len(
            [m for m in messages if m.message.role == "assistant"]
        )
        return scores

    # ── Deterministic helpers ──────────────────────────────────────

    @staticmethod
    def _find_anchor(text: str, anchors: list[str]) -> int:
        lower_text = text.lower()
        for anchor in anchors:
            target = anchor.lower()
            if target in lower_text:
                return lower_text.index(target)
        return -1

    def _tool_gate(self, dispatches: list[ToolDispatch]) -> float:
        email_calls = [d for d in dispatches
                       if d.tool_name == "gmail_get_message" and d.response_status < 400]
        crm_calls = [d for d in dispatches
                     if d.tool_name in ("crm_list_customers", "crm_get_customer")
                     and d.response_status < 400]
        if not email_calls and not crm_calls:
            return 0.2
        if not email_calls or not crm_calls:
            return 0.5
        return 1.0

    def _score_data_retrieval(self, dispatches: list[ToolDispatch]) -> float:
        email_calls = [d for d in dispatches
                       if d.tool_name == "gmail_get_message" and d.response_status < 400]
        crm_calls = [d for d in dispatches
                     if d.tool_name in ("crm_list_customers", "crm_get_customer")
                     and d.response_status < 400]
        return 0.50 * min(len(email_calls) / 3, 1.0) + 0.50 * min(len(crm_calls) / 2, 1.0)

    def _score_risk_classification(self, all_text: str) -> float:
        """Check correct risk level per customer."""
        lower = all_text.lower()
        correct = 0
        for letter, info in self.CUSTOMERS.items():
            anchors = [f"Customer {letter}", f"customer {letter}",
                       f"Client {letter}", f"Account {letter}"]
            idx = self._find_anchor(all_text, anchors)
            if idx < 0:
                continue
            region = all_text[max(0, idx - 100):idx + 400].lower()
            risk = info["risk"]
            if risk == "high" and any(k in region for k in ["high risk", "high-risk", "critical"]):
                correct += 1
            elif risk == "low" and any(k in region for k in ["low risk", "low-risk", "healthy"]):
                correct += 1
        return min(correct / 3, 1.0)

    def _score_customer_coverage(self, all_text: str) -> float:
        lower = all_text.lower()
        found = 0
        for letter in self.CUSTOMERS:
            if f"customer {letter.lower()}" in lower or f"client {letter.lower()}" in lower:
                found += 1
        return min(found / 4, 1.0)

    # ── Fallback scorers ───────────────────────────────────────────

    def _fallback_risk(self, all_text: str) -> float:
        """_fallback_: dev-only keyword scoring."""
        score = 0.0
        lower = all_text.lower()
        if "12%" in all_text and any(k in lower for k in ["low usage", "switch"]):
            score += 0.20
        if "5 complaint" in lower or "5th complaint" in lower:
            score += 0.15
        if "refund" in lower and "sla" in lower:
            score += 0.15
        if "satisfied" in lower and any(k in lower for k in ["upsell", "low risk"]):
            score += 0.15
        if "750" in all_text.replace(",", ""):
            score += 0.10
        return min(score, 1.0)

    def _fallback_retention(self, all_text: str) -> float:
        """_fallback_: dev-only keyword scoring for retention strategies."""
        score = 0.0
        lower = all_text.lower()
        if any(k in lower for k in ["retention", "retain", "prevent"]):
            score += 0.20
        if any(k in lower for k in ["training", "onboarding", "usage"]):
            score += 0.15
        if any(k in lower for k in ["escalat", "executive", "vip"]):
            score += 0.15
        if any(k in lower for k in ["resolve", "address", "fix"]):
            score += 0.15
        return min(score, 1.0)
