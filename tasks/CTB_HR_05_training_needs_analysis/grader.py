"""CTB_HR_05 grader -- training needs analysis.

Ground truth source: task.yaml reference_solution + fixtures.

v2.2: analysis mode (HR analysis report).
- Deterministic 35%: tool gate, training identification, priority ordering, total budget
- Judge 65%: training data accuracy, prioritization quality, report structure
- Fallback: keyword-based scoring when judge unavailable

Ground truth:
  Urgent: Finance new tax-law training (before Apr 1), 5K, budget 8K
  High: Engineering advanced Python 15K (budget 20K); Leadership training 25K (budget 30K)
  Medium: Marketing data analytics 8K (budget 10K)
  Low: Product UX design 6K (budget 8K)
  Total: 59K
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class Grader(AbstractGrader):
    """Grade training needs analysis report."""

    _TRAINING_DATA_RUBRIC = """\
Evaluate the accuracy of training need identification and data (0.0-1.0).

## Ground Truth -- 5 Training Programs
1. URGENT: Finance Dept -- New tax law training, 5,000 CNY, must complete before April 1, budget 8,000
2. HIGH: Engineering Dept -- Advanced Python, 15,000 CNY, budget 20,000
3. HIGH: All managers -- Leadership training, 25,000 CNY, dedicated budget 30,000
4. MEDIUM: Marketing Dept -- Data analytics, 8,000 CNY, budget 10,000
5. LOW: Product Dept -- UX design, 6,000 CNY, budget 8,000
Total budget required: 59,000 CNY

## Scoring tiers
- 0.9-1.0: All 5 training programs with correct budgets and departments
- 0.7-0.8: 4-5 programs; most data correct
- 0.5-0.6: 3+ programs; some data correct
- 0.3-0.4: 1-2 programs
- 0.0-0.2: No meaningful data
"""

    _PRIORITIZATION_RUBRIC = """\
Evaluate the quality of priority ranking and recommendations (0.0-1.0).

## Expected prioritization
1. Tax law training is URGENT (compliance deadline April 1)
2. Python and Leadership are HIGH (business impact)
3. Data analytics is MEDIUM
4. UX design is LOW
- All within respective budgets
- Categories: Technical (Python), Management (Leadership), Professional (Tax, Analytics, UX)

## Scoring tiers
- 0.9-1.0: Correct priority ranking; clear urgency rationale; budget within limits; categories
- 0.7-0.8: Mostly correct ranking; some rationale
- 0.5-0.6: Partial ranking; tax law urgency noted
- 0.3-0.4: Minimal prioritization
- 0.0-0.2: No prioritization
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
        det_score += 0.40 * self._score_training_identification(all_text, lower)
        det_score += 0.30 * self._score_priority_ordering(all_text, lower)
        det_score += 0.30 * self._score_total_budget(clean)

        # 3. Judge scoring (65%)
        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            data_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._TRAINING_DATA_RUBRIC
            ).score
            priority_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._PRIORITIZATION_RUBRIC
            ).score
        else:
            data_score = self._fallback_data(all_text, lower, clean)
            priority_score = self._fallback_priority(all_text, lower)

        completion = tool_penalty * (
            0.35 * det_score
            + 0.35 * data_score
            + 0.30 * priority_score
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

    def _score_training_identification(self, all_text: str, lower: str) -> float:
        trainings = [
            ["tax law", "tax regulation", "new tax"],
            ["python", "advanced python"],
            ["leadership"],
            ["data analytics", "data analysis"],
            ["ux", "user experience", "ux design"],
        ]
        found = sum(1 for group in trainings if any(t in lower for t in group))
        return min(found / 4, 1.0)

    def _score_priority_ordering(self, all_text: str, lower: str) -> float:
        score = 0.0
        # Tax law urgent
        if any(kw in lower for kw in ["urgent", "compliance", "deadline"]) and \
           any(kw in lower for kw in ["tax", "law"]):
            score += 0.5
        # UX low priority
        if any(kw in lower for kw in ["low", "lowest"]) and any(kw in lower for kw in ["ux", "user experience"]):
            score += 0.3
        # Tax before UX in text
        tax_idx = lower.find("tax")
        ux_idx = max(lower.find("ux"), lower.find("user experience"))
        if tax_idx >= 0 and ux_idx > tax_idx:
            score += 0.2
        return min(score, 1.0)

    @staticmethod
    def _has_bounded(text: str, num: str) -> bool:
        return bool(re.search(rf'(?<!\d){re.escape(num)}(?!\d)', text))

    def _score_total_budget(self, clean: str) -> float:
        if self._has_bounded(clean, "59000") or self._has_bounded(clean, "59K") or \
           "5.9" in clean:
            return 1.0
        return 0.0

    def _fallback_data(self, all_text: str, lower: str, clean: str) -> float:
        """_fallback_: dev-only."""
        score = 0.0
        budgets = ["5000", "15000", "25000", "8000", "6000"]
        score += 0.50 * min(sum(1 for b in budgets if b in clean) / 3, 1.0)
        depts = ["finance", "engineering", "marketing", "product"]
        score += 0.30 * min(sum(1 for d in depts if d in lower) / 3, 1.0)
        if self._has_bounded(clean, "59000"):
            score += 0.20
        return min(score, 1.0)

    def _fallback_priority(self, all_text: str, lower: str) -> float:
        """_fallback_: dev-only."""
        score = 0.0
        prio_kw = ["urgent", "high", "medium", "low", "priority"]
        score += 0.40 * min(sum(1 for kw in prio_kw if kw in lower) / 3, 1.0)
        if "April 1" in all_text or "Apr 1" in all_text:
            score += 0.30
        cats = ["technical", "management", "professional"]
        score += 0.30 * min(sum(1 for c in cats if c in lower) / 2, 1.0)
        return min(score, 1.0)
