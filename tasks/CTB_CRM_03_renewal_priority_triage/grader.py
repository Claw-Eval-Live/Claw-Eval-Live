"""CTB_CRM_03 grader -- contract renewal priority triage.

Ground truth source: task.yaml reference_solution + fixtures.

v2.2: analysis mode (analysis/ranking report).
- Deterministic 35%: tool gate, customer coverage, priority ordering, revenue total
- Judge 65%: priority accuracy, strategy quality
- Fallback: keyword-based scoring when judge unavailable

Ground truth:
  P0: Ruitong Medical (4/10, VIP, 450K, 3 conditions)
  P1: Jinding Tech (4/15, VIP, 500K, wants add-on)
  P2: Haichuan Logistics (4/30, std, 120K, complaints)
  P3: Xingchen Education (5/1, VIP, 380K, stable)
  P4: Hengtai Trading (6/30, std, 80K, health 35)
  Total expiring revenue: 1,530K
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class RenewalPriorityTriageGrader(AbstractGrader):
    """Grade contract renewal priority triage report."""

    CUSTOMERS = ["Ruitong", "Jinding", "Haichuan", "Xingchen", "Hengtai"]

    @staticmethod
    def _has_bounded(text: str, num: str) -> bool:
        return bool(re.search(rf'(?<!\d){re.escape(num)}(?!\d)', text))

    # ── Judge rubrics ──────────────────────────────────────────────

    _PRIORITY_RUBRIC = """\
Evaluate the accuracy of renewal priority ranking (0.0-1.0).

## Ground Truth Priority Order
1. P0 (Most Urgent): Ruitong Medical -- expires 4/10, VIP, 450K, has 3 specific renewal conditions (compliance/SLA)
2. P1 (High): Jinding Tech -- expires 4/15, VIP, 500K, positive intent, wants add-on/upgrade
3. P2 (Medium): Haichuan Logistics -- expires 4/30, standard, 120K, has complaints, churn risk
4. P3 (Lower): Xingchen Education -- expires 5/1, VIP, 380K, stable long-term customer
5. P4 (Lowest): Hengtai Trading -- expires 6/30, standard, 80K, health score 35

## Key factors for ranking
- Urgency (closer expiration = higher priority)
- Customer value (revenue x tier)
- Health risk (complaints, low health score)
- Specific conditions requiring response

## Scoring tiers
- 0.9-1.0: All 5 customers correctly ordered with rationale covering urgency + value + risk
- 0.7-0.8: Ordering mostly correct (Ruitong first, Hengtai last); reasonable rationale
- 0.5-0.6: 3-4 customers in roughly correct order; some rationale
- 0.3-0.4: Partial ordering; minimal rationale
- 0.0-0.2: No meaningful priority ranking
"""

    _STRATEGY_RUBRIC = """\
Evaluate the quality of renewal strategy recommendations (0.0-1.0).

## Expected strategies
- Ruitong: Urgently address 3 conditions (compliance, SLA 99.9%, specific requirements)
- Jinding: Capitalize on positive intent; prepare add-on/upsell proposal
- Haichuan: Address complaints first; retention offer; prevent churn
- Xingchen: Standard renewal process; maintain relationship
- Hengtai: Low priority; basic follow-up
- Total expiring revenue should be calculated: 1,530K

## Scoring tiers
- 0.9-1.0: Specific strategies per customer; addresses unique situations; total revenue calculated
- 0.7-0.8: Strategies for most customers; mostly specific
- 0.5-0.6: Generic strategies; some customer-specific elements
- 0.3-0.4: Minimal strategy; few specifics
- 0.0-0.2: No meaningful strategies
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
        det_score += 0.25 * self._score_customer_coverage(all_text)
        det_score += 0.35 * self._score_ordering(all_text)
        det_score += 0.20 * self._score_revenue_total(clean, all_text)
        det_score += 0.20 * self._score_data_retrieval(dispatches)

        # 3. Judge scoring (65%)
        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            priority_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._PRIORITY_RUBRIC
            ).score
            strategy_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._STRATEGY_RUBRIC
            ).score
        else:
            priority_score = self._fallback_priority(all_text)
            strategy_score = self._fallback_strategy(all_text)

        # 4. Combine
        completion = tool_penalty * (
            0.35 * det_score
            + 0.35 * priority_score
            + 0.30 * strategy_score
        )

        scores.completion = min(round(completion, 2), 1.0)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len(
            [m for m in messages if m.message.role == "assistant"]
        )
        return scores

    # ── Deterministic helpers ──────────────────────────────────────

    def _tool_gate(self, dispatches: list[ToolDispatch]) -> float:
        crm_calls = [d for d in dispatches
                     if d.tool_name in ("crm_list_customers", "crm_get_customer")
                     and d.response_status < 400]
        email_calls = [d for d in dispatches
                       if d.tool_name in ("gmail_list_messages", "gmail_get_message")
                       and d.response_status < 400]
        if not crm_calls and not email_calls:
            return 0.2
        if not crm_calls or not email_calls:
            return 0.5
        return 1.0

    def _score_customer_coverage(self, all_text: str) -> float:
        found = sum(1 for name in self.CUSTOMERS if name in all_text)
        return min(found / 4, 1.0)

    def _score_ordering(self, all_text: str) -> float:
        """Ruitong should appear before Hengtai."""
        r_found = "Ruitong" in all_text
        h_found = "Hengtai" in all_text
        if r_found and h_found:
            r_idx = all_text.index("Ruitong")
            h_idx = all_text.index("Hengtai")
            return 1.0 if r_idx < h_idx else 0.3
        if r_found or h_found:
            return 0.3
        return 0.0

    def _score_revenue_total(self, clean: str, all_text: str) -> float:
        if any(v in clean for v in ["1530000", "1530K", "1530k", "153万"]):
            return 1.0
        if self._has_bounded(clean, "1530") or "1.53M" in all_text:
            return 1.0
        return 0.0

    def _score_data_retrieval(self, dispatches: list[ToolDispatch]) -> float:
        crm_calls = [d for d in dispatches
                     if d.tool_name in ("crm_list_customers", "crm_get_customer")
                     and d.response_status < 400]
        email_calls = [d for d in dispatches
                       if d.tool_name == "gmail_get_message" and d.response_status < 400]
        return 0.50 * min(len(crm_calls) / 3, 1.0) + 0.50 * min(len(email_calls) / 2, 1.0)

    # ── Fallback scorers ───────────────────────────────────────────

    def _fallback_priority(self, all_text: str) -> float:
        """_fallback_: dev-only keyword scoring for priority ranking."""
        score = 0.0
        lower = all_text.lower()
        if "ruitong" in lower and any(k in lower for k in ["urgent", "first", "highest", "p0"]):
            score += 0.20
        if "jinding" in lower and any(k in lower for k in ["high", "second", "add-on", "p1"]):
            score += 0.15
        if "haichuan" in lower and any(k in lower for k in ["medium", "risk", "complaint", "p2"]):
            score += 0.15
        if "hengtai" in lower and any(k in lower for k in ["lowest", "last", "p4"]):
            score += 0.15
        return min(score, 1.0)

    def _fallback_strategy(self, all_text: str) -> float:
        """_fallback_: dev-only keyword scoring for strategy quality."""
        score = 0.0
        lower = all_text.lower()
        actions = ["compliance", "sla", "add-on", "upsell", "complaint", "retain",
                   "negotiate", "resolve", "address"]
        score += 0.50 * min(sum(1 for a in actions if a in lower) / 3, 1.0)
        if "1530" in all_text.replace(",", ""):
            score += 0.20
        if len(all_text.strip()) >= 300:
            score += 0.15
        return min(score, 1.0)
