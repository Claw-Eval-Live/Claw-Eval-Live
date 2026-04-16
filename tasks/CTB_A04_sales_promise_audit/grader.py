"""CTB_A04 grader -- sales promise vs CRM contract consistency audit.

Ground truth source: task.yaml reference_solution + fixtures.
Rubric ground truth derived from fixture data:
  - fixtures/gmail/inbox.json (msg_901, msg_902, msg_903)
  - fixtures/crm/customers.json (CUS-501, CUS-502, CUS-503)

v2.2: Claw-Eval mode (analysis/audit report).
- Deterministic 35%: tool gate, discrepancy detection, risk classification
- Judge 65%: discrepancy accuracy, report quality + analysis
- Fallback: keyword-based scoring when judge unavailable

Ground truth discrepancies:
  CUS-501 GreenTech: 4 discrepancies
    - Price: Email 20% off => 96K vs CRM 120K (high risk)
    - Trial: Email 3-month premium trial vs CRM none (medium risk)
    - Training: Email 2 on-site sessions vs CRM 0 (medium risk)
    - SLA: Email 4h response vs CRM 8h (medium risk)
  CUS-502 Precision Mfg: 4 discrepancies
    - Price: Email 65K vs CRM 80K (high risk)
    - Module: Email free anomaly detection vs CRM none (medium risk)
    - Migration: Email free data migration vs CRM none (medium risk)
    - Support: Email priority vs CRM standard (low risk)
  CUS-503 Nova Retail: No discrepancies (consistent)
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class SalesPromiseAuditGrader(AbstractGrader):
    """Grade a sales promise vs CRM consistency audit report."""

    # ── Judge rubrics ──────────────────────────────────────────────

    _DISCREPANCY_RUBRIC = """\
Evaluate the accuracy and completeness of discrepancy identification (0.0-1.0).

## Ground Truth -- CUS-501 (GreenTech) Discrepancies
1. Price: Email promises 20% discount => 96K; CRM records 120K (high risk)
2. Trial: Email promises 3-month premium edition trial; CRM has no such term (medium risk)
3. Training: Email promises 2 on-site training sessions; CRM records 0 (medium risk)
4. SLA response: Email promises 4-hour response; CRM records 8-hour (medium risk)

## Ground Truth -- CUS-502 (Precision Manufacturing) Discrepancies
1. Price: Email quotes 65K; CRM records 80K (high risk)
2. Module: Email promises free anomaly detection module; CRM has no record (medium risk)
3. Migration: Email promises free data migration; CRM has no record (medium risk)
4. Support: Email promises priority support; CRM records standard (low risk)

## Ground Truth -- CUS-503 (Nova Retail)
- No discrepancies; email commitments are consistent with CRM terms

## Scoring tiers
- 0.9-1.0: All 8 discrepancies across CUS-501 and CUS-502 correctly identified with both email and CRM values; Nova Retail correctly noted as consistent
- 0.7-0.8: 6+ discrepancies found with correct values; Nova Retail handled correctly
- 0.5-0.6: 4-5 discrepancies found; some values correct
- 0.3-0.4: 2-3 discrepancies found; partial values
- 0.0-0.2: Fewer than 2 discrepancies or major errors
"""

    _REPORT_RUBRIC = """\
Evaluate the quality and structure of the audit report (0.0-1.0).

## Expected report elements
1. Audit scope: 3 customers checked, 3 sales proposal emails reviewed
2. Item-by-item comparison for each customer (email commitment vs CRM record)
3. Inconsistency summary table with columns: Customer, Item, Email Commitment, CRM Record, Discrepancy, Risk Level
4. Risk classification: High risk for pricing discrepancies, Medium for add-ons, Low for wording differences
5. High-risk items list: 2 pricing discrepancies requiring legal review (GreenTech 96K vs 120K, Precision 65K vs 80K)

## Scoring tiers
- 0.9-1.0: All elements present; well-structured with clear table format; risk levels correctly assigned; actionable legal review list
- 0.7-0.8: Most elements present; reasonable structure; risk levels mostly correct
- 0.5-0.6: Partial structure; some risk levels; missing summary or legal review list
- 0.3-0.4: Minimal structure; few risk assignments
- 0.0-0.2: No meaningful report structure
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

        # 2. Deterministic checks (35%)
        det_score = 0.0
        det_score += 0.30 * self._score_discrepancy_detection(all_text, clean)
        det_score += 0.25 * self._score_consistent_customer(all_text)
        det_score += 0.25 * self._score_risk_classification(all_text)
        det_score += 0.20 * self._score_key_numbers(clean, all_text)

        # 3. Judge scoring (65%)
        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            discrepancy_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._DISCREPANCY_RUBRIC
            ).score
            report_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._REPORT_RUBRIC
            ).score
        else:
            discrepancy_score = self._fallback_discrepancy(all_text, clean)
            report_score = self._fallback_report(all_text)

        # 4. Combine
        completion = tool_penalty * (
            0.35 * det_score
            + 0.35 * discrepancy_score
            + 0.30 * report_score
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
        """Penalty multiplier: did the agent read emails AND query CRM?"""
        email_calls = [d for d in dispatches
                       if d.tool_name == "gmail_get_message" and d.response_status < 400]
        crm_calls = [d for d in dispatches
                     if d.tool_name in ("crm_get_customer", "crm_search_customer")
                     and d.response_status < 400]
        if not email_calls and not crm_calls:
            return 0.2
        if not email_calls or not crm_calls:
            return 0.5
        return 1.0

    def _score_discrepancy_detection(self, all_text: str, clean: str) -> float:
        """Check how many of the 8 discrepancies are found."""
        lower = all_text.lower()
        disc_signals = [
            # CUS-501 price
            any(k in lower for k in ["20%", "96000", "96,000", "96k"]) and "120" in clean,
            # CUS-501 trial
            any(k in lower for k in ["3-month", "three-month", "3 month", "premium"]) and any(k in lower for k in ["trial", "no record", "none"]),
            # CUS-501 training
            any(k in lower for k in ["2 session", "two session", "on-site training", "2 training"]),
            # CUS-501 SLA
            any(k in lower for k in ["4 hour", "4h", "4-hour"]) and any(k in lower for k in ["8 hour", "8h", "8-hour"]),
            # CUS-502 price
            any(k in clean for k in ["65000", "65K", "65k"]) and any(k in clean for k in ["80000", "80K", "80k"]),
            # CUS-502 module
            any(k in lower for k in ["anomaly detection", "free module", "premium module"]),
            # CUS-502 migration
            any(k in lower for k in ["data migration", "free migration"]),
            # CUS-502 support
            any(k in lower for k in ["priority support", "priority channel"]) and "standard" in lower,
        ]
        found = sum(1 for s in disc_signals if s)
        return min(found / 6, 1.0)

    def _score_consistent_customer(self, all_text: str) -> float:
        """Check that Nova Retail is identified as consistent."""
        lower = all_text.lower()
        if "nova retail" not in lower and "nova" not in lower:
            return 0.0
        consistent_kw = ["consistent", "no discrepanc", "match", "aligned",
                         "no inconsistenc", "no issue", "compliant"]
        if any(kw in lower for kw in consistent_kw):
            return 1.0
        return 0.3

    def _score_risk_classification(self, all_text: str) -> float:
        """Check presence of risk level classification."""
        lower = all_text.lower()
        score = 0.0
        if "high risk" in lower or "high-risk" in lower:
            score += 0.40
        if "medium risk" in lower or "medium-risk" in lower:
            score += 0.30
        if "low risk" in lower or "low-risk" in lower:
            score += 0.15
        if re.search(r"pric.*high.?risk|high.?risk.*pric", lower, re.DOTALL):
            score += 0.15
        return min(score, 1.0)

    def _score_key_numbers(self, clean: str, all_text: str) -> float:
        """Check for key financial numbers with boundary matching."""
        nums = ["96000", "120000", "65000", "80000"]
        found = sum(1 for n in nums if self._has_bounded(clean, n) or self._has_bounded(clean, n[:2] + "," + n[2:]))
        return min(found / 3, 1.0)

    # ── Fallback scorers ───────────────────────────────────────────

    def _fallback_discrepancy(self, all_text: str, clean: str) -> float:
        """_fallback_: dev-only keyword scoring for discrepancy accuracy."""
        score = 0.0
        lower = all_text.lower()
        if "greentech" in lower:
            score += 0.08
        if "precision" in lower:
            score += 0.08
        if "nova" in lower:
            score += 0.04
        if any(k in clean for k in ["96000", "96,000"]):
            score += 0.10
        if any(k in clean for k in ["65000", "65,000"]):
            score += 0.10
        if any(k in lower for k in ["20%", "20% off", "20% discount"]):
            score += 0.08
        if any(k in lower for k in ["anomaly detection", "free module"]):
            score += 0.06
        if any(k in lower for k in ["4 hour", "4h"]) and any(k in lower for k in ["8 hour", "8h"]):
            score += 0.06
        if any(k in lower for k in ["3-month", "trial"]):
            score += 0.06
        if any(k in lower for k in ["migration", "data migration"]):
            score += 0.06
        if "priority" in lower and "standard" in lower:
            score += 0.06
        return min(score, 1.0)

    def _fallback_report(self, all_text: str) -> float:
        """_fallback_: dev-only keyword scoring for report quality."""
        score = 0.0
        lower = all_text.lower()
        if "high risk" in lower or "high-risk" in lower:
            score += 0.15
        if "medium risk" in lower or "medium-risk" in lower:
            score += 0.10
        if "|" in all_text and "---" in all_text:
            score += 0.20
        if any(k in lower for k in ["audit scope", "scope"]):
            score += 0.10
        if any(k in lower for k in ["legal review", "legal"]):
            score += 0.15
        if len(all_text.strip()) >= 500:
            score += 0.15
        if any(k in lower for k in ["inconsistenc", "discrepanc"]):
            score += 0.10
        return min(score, 1.0)
