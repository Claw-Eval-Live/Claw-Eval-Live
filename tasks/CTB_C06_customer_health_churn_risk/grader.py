"""CTB_C06 grader -- customer health monitoring and churn risk intervention.

Ground truth source: task.yaml reference_solution + fixtures.

v2.2: WildClawBench mode (operations with CRM task creation).
- Deterministic 55%: tool gate, risk classification, task creation for high-risk
- Judge 45%: risk assessment accuracy, intervention plan quality
- Fallback: keyword-based scoring when judge unavailable

Ground truth:
  Everway Logistics: HIGH risk (2 neg emails + open tickets + May renewal)
  Azure Sea Medical: HIGH risk (2 neg emails + high-priority tickets + June renewal)
  CloudSail Education: MEDIUM risk (60 days no contact + April renewal)
  Stellar Finance: LOW risk (positive email + no tickets + Sept renewal)
  Must create urgent follow-up tasks for Everway and Azure Sea
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class CustomerHealthChurnGrader(AbstractGrader):
    """Grade customer health monitoring and churn risk assessment."""

    CUSTOMERS = {
        "Everway Logistics": {"risk": "high", "id": "CUS-401"},
        "Azure Sea Medical": {"risk": "high", "id": "CUS-404"},
        "CloudSail Education": {"risk": "medium", "id": "CUS-402"},
        "Stellar Finance": {"risk": "low", "id": "CUS-403"},
    }

    HIGH_RISK_IDS = {"CUS-401", "CUS-404"}

    # ── Judge rubrics ──────────────────────────────────────────────

    _RISK_ASSESSMENT_RUBRIC = """\
Evaluate the accuracy of risk classification for each VIP customer (0.0-1.0).

## Ground Truth
1. Everway Logistics: HIGH risk
   - 2 negative emails mentioning "alternative vendor" and "severely dissatisfied"
   - 2 open support tickets
   - May renewal approaching

2. Azure Sea Medical: HIGH risk
   - 2 negative emails with "urging" and "renewal concerns"
   - 2 high-priority open tickets
   - June renewal approaching

3. CloudSail Education: MEDIUM risk
   - 60 days with no contact
   - April renewal approaching
   - No recent emails

4. Stellar Finance: LOW risk
   - 1 positive email (smooth operations, expansion plans)
   - No open tickets
   - September renewal (ample time)

## Scoring tiers
- 0.9-1.0: All 4 customers classified correctly with supporting evidence from emails/CRM
- 0.7-0.8: 3-4 correct classifications; most evidence cited
- 0.5-0.6: 2-3 correct classifications; some evidence
- 0.3-0.4: 1-2 correct; minimal evidence
- 0.0-0.2: No meaningful risk classification
"""

    _INTERVENTION_RUBRIC = """\
Evaluate the quality of intervention recommendations for high-risk customers (0.0-1.0).

## Expected interventions
- Everway Logistics: Urgent executive outreach, address service quality concerns, retention offer
- Azure Sea Medical: Priority ticket resolution, renewal discussion, address open concerns
- Both should have concrete CRM follow-up tasks created

## Scoring tiers
- 0.9-1.0: Specific, actionable intervention plans for both high-risk customers; addresses their unique concerns
- 0.7-0.8: Plans for both high-risk customers; mostly specific
- 0.5-0.6: Plans present but generic; or only one high-risk customer covered
- 0.3-0.4: Vague intervention mentions
- 0.0-0.2: No intervention recommendations
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

        # 2. Deterministic checks (55%)
        det_score = 0.0
        det_score += 0.25 * self._score_data_retrieval(dispatches)
        det_score += 0.35 * self._score_risk_classification(all_text)
        det_score += 0.25 * self._score_task_creation(dispatches, audit_data)
        det_score += 0.15 * self._score_customer_coverage(all_text)

        # 3. Judge scoring (45%)
        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            risk_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._RISK_ASSESSMENT_RUBRIC
            ).score
            intervention_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._INTERVENTION_RUBRIC
            ).score
        else:
            risk_score = self._fallback_risk(all_text)
            intervention_score = self._fallback_intervention(all_text)

        # 4. Combine
        completion = tool_penalty * (
            0.55 * det_score
            + 0.25 * risk_score
            + 0.20 * intervention_score
        )

        scores.completion = min(round(completion, 2), 1.0)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len(
            [m for m in messages if m.message.role == "assistant"]
        )
        return scores

    # ── Deterministic helpers ──────────────────────────────────────

    def _tool_gate(self, dispatches: list[ToolDispatch]) -> float:
        """Penalty: must read CRM AND emails."""
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

    def _score_data_retrieval(self, dispatches: list[ToolDispatch]) -> float:
        """Check CRM detail reads and email reads."""
        crm_get = [d for d in dispatches
                   if d.tool_name == "crm_get_customer" and d.response_status < 400]
        read_ids = {str(d.request_body.get("customer_id")) for d in crm_get}
        expected = {"CUS-401", "CUS-402", "CUS-403", "CUS-404"}
        crm_score = len(read_ids & expected) / len(expected)

        email_get = [d for d in dispatches
                     if d.tool_name == "gmail_get_message" and d.response_status < 400]
        email_score = min(len(email_get) / 4, 1.0)

        return 0.50 * crm_score + 0.50 * email_score

    def _score_risk_classification(self, all_text: str) -> float:
        """Check correct risk level assigned to each customer."""
        lower = all_text.lower()
        risk_map = {
            "high": ["high risk", "high-risk", "critical risk"],
            "medium": ["medium risk", "medium-risk", "moderate risk"],
            "low": ["low risk", "low-risk"],
        }
        correct_count = 0
        for name, info in self.CUSTOMERS.items():
            if name.lower() not in lower:
                continue
            idx = lower.index(name.lower())
            region = lower[max(0, idx - 200):idx + 500]
            expected = info["risk"]
            if any(kw in region for kw in risk_map[expected]):
                correct_count += 1
        return correct_count / len(self.CUSTOMERS)

    def _score_task_creation(self, dispatches: list[ToolDispatch],
                             audit_data: dict[str, dict] | None) -> float:
        """Check CRM tasks created for high-risk customers."""
        task_calls = [d for d in dispatches
                      if d.tool_name == "crm_create_task" and d.response_status < 400]
        created_tasks = self.get_service_actions(audit_data, "crm", "tasks")
        all_tasks = [d.request_body or {} for d in task_calls] + list(created_tasks)
        if not all_tasks:
            return 0.0

        high_risk_names = {"everway logistics", "azure sea medical"}
        tasks_for_high = 0
        for t in all_tasks:
            t_str = str(t).lower()
            cid = str(t.get("customer_id", ""))
            if cid in self.HIGH_RISK_IDS or any(n in t_str for n in high_risk_names):
                tasks_for_high += 1
        return min(tasks_for_high / 2, 1.0)

    def _score_customer_coverage(self, all_text: str) -> float:
        """Check all 4 customers are mentioned."""
        lower = all_text.lower()
        found = sum(1 for name in self.CUSTOMERS if name.lower() in lower)
        return found / len(self.CUSTOMERS)

    # ── Fallback scorers ───────────────────────────────────────────

    def _fallback_risk(self, all_text: str) -> float:
        """_fallback_: dev-only keyword scoring for risk assessment."""
        score = 0.0
        lower = all_text.lower()
        if "everway" in lower and "high" in lower:
            score += 0.20
        if "azure sea" in lower and "high" in lower:
            score += 0.20
        if "cloudsail" in lower and "medium" in lower:
            score += 0.15
        if "stellar" in lower and "low" in lower:
            score += 0.15
        if any(k in lower for k in ["alternative vendor", "dissatisfi"]):
            score += 0.10
        if any(k in lower for k in ["renewal", "concern", "urging"]):
            score += 0.10
        return min(score, 1.0)

    def _fallback_intervention(self, all_text: str) -> float:
        """_fallback_: dev-only keyword scoring for intervention quality."""
        score = 0.0
        lower = all_text.lower()
        if any(k in lower for k in ["urgent", "immediate", "priority", "escalat"]):
            score += 0.25
        if any(k in lower for k in ["follow-up", "follow up", "outreach", "contact"]):
            score += 0.25
        if any(k in lower for k in ["retention", "retain", "prevent churn"]):
            score += 0.20
        if any(k in lower for k in ["resolve", "address", "fix"]):
            score += 0.15
        if len(all_text.strip()) >= 300:
            score += 0.10
        return min(score, 1.0)
