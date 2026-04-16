"""CTB_FIN_30 grader -- tax deduction review.

Ground truth source: task.yaml reference_solution + fixtures/finance/transactions.json

v2.2: Claw-Eval mode (financial analysis report).
- Deterministic 35%: tool gate, non-deductible total, R&D super-deduction, tax impact
- Judge 65%: expense classification accuracy, calculation correctness, analysis quality
- Fallback: keyword-based scoring when judge unavailable

Ground truth (from finance fixtures):
  Non-deductible: Penalties 80K + Late surcharges 25K = 105K
  Business entertainment 350K, 60% limit = 210K, non-deductible = 140K
  R&D expenses 600K, 175% super deduction = 1,050K (extra 450K)
  Tax impact (narrow): 105K x 25% = 26.25K
  Tax impact (with entertainment): 245K x 25% = 61.25K
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class Grader(AbstractGrader):
    """Grade a tax deduction review report."""

    # ── Judge rubrics ──────────────────────────────────────────────

    _CLASSIFICATION_RUBRIC = """\
Evaluate the accuracy of expense deductibility classification (0.0-1.0).

## Ground Truth
- Penalties (80K): Non-deductible (cannot be deducted before tax)
- Late payment surcharges (25K): Non-deductible
- Business entertainment (350K): Partially deductible (60% limit = 210K deductible, 140K non-deductible)
- R&D expenses (600K): Fully deductible with 175% super-deduction (extra 450K deduction)
- Other expenses: Fully deductible (office supplies, employee benefits, etc.)

## Scoring tiers
- 0.9-1.0: All categories correctly classified with correct rules cited
- 0.7-0.8: Most categories correct; entertainment 60% rule and R&D 175% mentioned
- 0.5-0.6: Some categories correct; key rules partially mentioned
- 0.3-0.4: Minimal classification
- 0.0-0.2: No meaningful classification
"""

    _CALCULATION_RUBRIC = """\
Evaluate the correctness of tax calculations (0.0-1.0).

## Ground Truth -- Key Numbers
- Non-deductible total: Penalties 80K + Surcharges 25K = 105K
- Business entertainment: 350K x 60% = 210K deductible, 140K non-deductible
- R&D super deduction: 600K x 175% = 1,050K (extra deduction: 450K)
- Tax impact (basic): 105K x 25% = 26.25K
- Tax impact (including entertainment): (105K + 140K) x 25% = 61.25K

## Scoring tiers
- 0.9-1.0: All key calculations correct (105K, 210K/140K, 1050K/450K, 26.25K or 61.25K)
- 0.7-0.8: Most calculations correct; minor differences acceptable
- 0.5-0.6: Some calculations correct
- 0.3-0.4: One or two numbers present
- 0.0-0.2: No meaningful calculations
"""

    _ANALYSIS_RUBRIC = """\
Evaluate the quality of the tax deduction analysis report (0.0-1.0).

## Expected elements
1. Clear categorization of all expense items by deductibility
2. Explanation of special rules (60% entertainment cap, 175% R&D super-deduction)
3. Tax impact quantification
4. Well-structured report (table format preferred)

## Scoring tiers
- 0.9-1.0: Comprehensive with all rules explained; well-organized; actionable insights
- 0.7-0.8: Good coverage; most rules mentioned; reasonable structure
- 0.5-0.6: Partial coverage; some rules
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

        # 2. Deterministic checks (35%)
        det_score = 0.0
        det_score += 0.30 * self._score_non_deductible(clean, all_text)
        det_score += 0.35 * self._score_special_rules(clean, all_text)
        det_score += 0.35 * self._score_tax_impact(clean)

        # 3. Judge scoring (65%)
        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            class_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._CLASSIFICATION_RUBRIC
            ).score
            calc_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._CALCULATION_RUBRIC
            ).score
            analysis_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._ANALYSIS_RUBRIC
            ).score
        else:
            class_score = self._fallback_classification(all_text, clean)
            calc_score = self._fallback_calculation(clean)
            analysis_score = self._fallback_analysis(all_text)

        # 4. Combine
        completion = tool_penalty * (
            0.35 * det_score
            + 0.20 * class_score
            + 0.25 * calc_score
            + 0.20 * analysis_score
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
        calls = [d for d in dispatches
                 if d.tool_name == "finance_list_transactions"
                 and d.response_status < 400]
        if not calls:
            return 0.2
        return 1.0

    def _score_non_deductible(self, clean: str, all_text: str) -> float:
        """Check non-deductible total = 105K."""
        score = 0.0
        if self._has_bounded(clean, "105000") or self._has_bounded(clean, "105K") or \
           "10.5" in clean:
            score += 0.6
        # Penalties + surcharges identified as non-deductible
        lower = all_text.lower()
        has_penalty = any(kw in lower for kw in ["penalt", "fine"])
        has_surcharge = any(kw in lower for kw in ["surcharge", "late payment"])
        has_nondeduct = any(kw in lower for kw in ["non-deductible", "not deductible",
                                                     "disallowed", "cannot be deducted"])
        if has_penalty and has_surcharge and has_nondeduct:
            score += 0.4
        elif (has_penalty or has_surcharge) and has_nondeduct:
            score += 0.2
        return min(score, 1.0)

    def _score_special_rules(self, clean: str, all_text: str) -> float:
        """Check entertainment 60% and R&D 175% rules."""
        score = 0.0
        lower = all_text.lower()
        # Entertainment 60% rule
        has_entertainment = any(kw in lower for kw in ["entertainment", "business entertainment"])
        has_60 = self._has_bounded(all_text, "60%")
        has_210 = self._has_bounded(clean, "210000") or self._has_bounded(clean, "210K") or \
                  self._has_bounded(clean, "21")
        if has_entertainment and (has_60 or has_210):
            score += 0.5
        elif has_entertainment:
            score += 0.15

        # R&D 175%
        has_rd = any(kw in all_text for kw in ["R&D", "research"])
        has_175 = self._has_bounded(all_text, "175%")
        has_1050 = self._has_bounded(clean, "1050000") or self._has_bounded(clean, "1050K") or \
                   self._has_bounded(clean, "105")
        has_450 = self._has_bounded(clean, "450000") or self._has_bounded(clean, "450K") or \
                  self._has_bounded(clean, "45")
        if has_rd and (has_175 or has_1050 or has_450):
            score += 0.5
        elif has_rd:
            score += 0.15
        return min(score, 1.0)

    def _score_tax_impact(self, clean: str) -> float:
        """Check tax impact calculation."""
        score = 0.0
        # 26.25K or 61.25K
        if self._has_bounded(clean, "26250") or self._has_bounded(clean, "26.25") or \
           self._has_bounded(clean, "2.625"):
            score += 0.5
        if self._has_bounded(clean, "61250") or self._has_bounded(clean, "61.25") or \
           self._has_bounded(clean, "6.125"):
            score += 0.5
        return min(score, 1.0)

    # ── Fallback scorers ───────────────────────────────────────────

    def _fallback_classification(self, all_text: str, clean: str) -> float:
        """_fallback_: dev-only keyword scoring."""
        score = 0.0
        lower = all_text.lower()
        if any(kw in lower for kw in ["penalt"]) and any(kw in lower for kw in ["non-deductible", "not deductible"]):
            score += 0.25
        if any(kw in lower for kw in ["surcharge", "late payment"]) and any(kw in lower for kw in ["non-deductible", "not deductible"]):
            score += 0.25
        if "entertainment" in lower and "60%" in all_text:
            score += 0.25
        if "r&d" in lower and "175%" in all_text:
            score += 0.25
        return min(score, 1.0)

    def _fallback_calculation(self, clean: str) -> float:
        """_fallback_: dev-only keyword scoring."""
        score = 0.0
        if self._has_bounded(clean, "105000") or self._has_bounded(clean, "105"):
            score += 0.25
        if self._has_bounded(clean, "210000") or self._has_bounded(clean, "140000"):
            score += 0.25
        if self._has_bounded(clean, "1050000") or self._has_bounded(clean, "450000"):
            score += 0.25
        if self._has_bounded(clean, "26250") or self._has_bounded(clean, "61250"):
            score += 0.25
        return min(score, 1.0)

    def _fallback_analysis(self, all_text: str) -> float:
        """_fallback_: dev-only keyword scoring."""
        score = 0.0
        if "|" in all_text and "---" in all_text:
            score += 0.30
        if any(kw in all_text.lower() for kw in ["deductible", "deduction"]):
            score += 0.30
        if "25%" in all_text:
            score += 0.20
        if len(all_text.strip()) >= 300:
            score += 0.20
        return min(score, 1.0)
