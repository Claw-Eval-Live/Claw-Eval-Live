"""CTB_FIN_17 grader -- CapEx vs OpEx classification check.

Ground truth source: task.yaml reference_solution + fixtures.

v2.2: Claw-Eval mode (financial analysis).
- Deterministic 40%: tool gate, misclassification detection, total impact
- Judge 60%: classification accuracy, analysis quality
- Fallback: keyword-based scoring when judge unavailable

Ground truth:
  2 misclassified: Software license 3-year 90K (should be CapEx), ERP system 5-year 500K (should be CapEx)
  Laptop 8K < 50K threshold -> OpEx acceptable
  Total impact: 590K
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class Grader(AbstractGrader):
    """Grade CapEx vs OpEx classification check."""

    @staticmethod
    def _has_bounded(text: str, num: str) -> bool:
        return bool(re.search(rf'(?<!\d){re.escape(num)}(?!\d)', text))

    # ── Judge rubrics ──────────────────────────────────────────────

    _CLASSIFICATION_RUBRIC = """\
Evaluate the accuracy of CapEx/OpEx classification verification (0.0-1.0).

## Ground Truth -- Policy: items >50K with useful life >1 year should be capitalized (CapEx)

### Misclassified items (currently OpEx, should be CapEx):
1. Software License (3-year term): Amount 90,000 + useful life 3 years -> exceeds both thresholds -> SHOULD BE CapEx
2. ERP System: Amount 500,000 + useful life 5 years -> SHOULD BE CapEx

### Correctly classified:
3. Laptop: Amount 8,000 (below 50K threshold) -> despite 3-year life, amount is below threshold -> OpEx is ACCEPTABLE

### Total misclassification impact: 90,000 + 500,000 = 590,000

## Scoring tiers
- 0.9-1.0: Both misclassified items correctly identified with amounts and reasoning; laptop correctly noted as acceptable; total impact 590K
- 0.7-0.8: Both misclassified items found; reasoning present; total approximately correct
- 0.5-0.6: 1-2 items identified; partial reasoning
- 0.3-0.4: 1 item identified; minimal reasoning
- 0.0-0.2: No meaningful classification check
"""

    _ANALYSIS_RUBRIC = """\
Evaluate the quality of the CapEx/OpEx analysis report (0.0-1.0).

## Expected elements
1. Clear explanation of the classification policy (>50K + >1 year = CapEx)
2. Per-item analysis with amount, useful life, and classification judgment
3. Total financial impact of misclassifications (590K)
4. Recommendation for reclassification

## Scoring tiers
- 0.9-1.0: Policy clearly stated; all items analyzed; impact quantified; reclassification recommended
- 0.7-0.8: Most elements present; reasonable analysis
- 0.5-0.6: Partial analysis; some elements
- 0.3-0.4: Minimal analysis
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
        lower = all_text.lower()

        # 1. Tool gate
        tool_penalty = self._tool_gate(dispatches)

        # 2. Deterministic checks (40%)
        det_score = 0.0
        det_score += 0.35 * self._score_misclassification_detection(all_text, lower)
        det_score += 0.35 * self._score_total_impact(clean)
        det_score += 0.30 * self._score_laptop_correct(lower)

        # 3. Judge scoring (60%)
        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            class_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._CLASSIFICATION_RUBRIC
            ).score
            analysis_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._ANALYSIS_RUBRIC
            ).score
        else:
            class_score = self._fallback_classification(all_text, lower, clean)
            analysis_score = self._fallback_analysis(lower, all_text)

        # 4. Combine
        completion = tool_penalty * (
            0.40 * det_score
            + 0.30 * class_score
            + 0.30 * analysis_score
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

    def _score_misclassification_detection(self, all_text: str, lower: str) -> float:
        found = 0
        if any(k in lower for k in ["software license"]) and any(k in lower for k in ["capex", "misclass", "incorrect", "should be capital"]):
            found += 1
        elif any(k in lower for k in ["software license"]):
            found += 0.3
        if "erp" in lower and any(k in lower for k in ["capex", "misclass", "incorrect", "should be capital"]):
            found += 1
        elif "erp" in lower:
            found += 0.3
        return min(found / 2, 1.0)

    def _score_total_impact(self, clean: str) -> float:
        if any(v in clean for v in ["590000", "590K", "590k"]):
            return 1.0
        if self._has_bounded(clean, "590"):
            return 1.0
        return 0.0

    def _score_laptop_correct(self, lower: str) -> float:
        if "laptop" in lower and any(k in lower for k in ["below", "under", "acceptable", "correct", "opex"]):
            return 1.0
        if "laptop" in lower:
            return 0.3
        return 0.0

    # ── Fallback scorers ───────────────────────────────────────────

    def _fallback_classification(self, all_text: str, lower: str, clean: str) -> float:
        """_fallback_: dev-only."""
        score = 0.0
        if "software license" in lower:
            score += 0.15
        if "erp" in lower:
            score += 0.15
        if any(k in lower for k in ["misclass", "incorrect", "wrong", "error"]):
            score += 0.15
        if "590" in clean:
            score += 0.15
        if "capex" in lower:
            score += 0.10
        return min(score, 1.0)

    def _fallback_analysis(self, lower: str, all_text: str) -> float:
        """_fallback_: dev-only."""
        score = 0.0
        if "50" in all_text and any(k in lower for k in ["threshold", "policy"]):
            score += 0.20
        if any(k in lower for k in ["reclassif", "correction", "recommend"]):
            score += 0.20
        if len(all_text.strip()) >= 200:
            score += 0.15
        return min(score, 1.0)
