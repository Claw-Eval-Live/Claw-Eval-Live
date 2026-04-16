"""CTB_A02 grader -- score an investment priority report over three projects.

v2.2: hybrid deterministic + judge scoring.
- Deterministic: project names, final scores (82.0/76.0/52.0), ranking order
- Judge: financial data accuracy (NPV/IRR/payback), methodology & recommendation quality
- Fallback: keyword-based scoring when judge unavailable
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class InvestmentPriorityMatrixGrader(AbstractGrader):
    """Grade a structured project investment ranking report."""

    _DATA_RUBRIC = """\
Evaluate the accuracy of financial calculations for all three projects (0.0-1.0).

## Ground Truth
### Project A
- NPV (8%): ~1888.34 million CNY
- IRR: ~22.85%
- Dynamic payback period: ~2.81 years
- Risk level: Medium

### Project B
- NPV (8%): ~3583.23 million CNY
- IRR: ~30.02%
- Dynamic payback period: ~2.43 years
- Risk level: High

### Project C
- NPV (8%): ~2586.15 million CNY
- IRR: ~26.07%
- Dynamic payback period: ~2.63 years
- Risk level: Low

### Composite scores
- Financial score ranking: B (100) > C (70) > A (40)
- Risk score: C (100, low) > A (70, medium) > B (40, high)
- Final composite: C = 82.0, B = 76.0, A = 52.0

## Scoring tiers
- 0.9-1.0: All 3 projects with correct NPV, IRR, payback, risk level, and composite scores
- 0.7-0.8: Most calculations correct, 1-2 minor numerical differences
- 0.5-0.6: Correct direction but significant calculation errors
- 0.3-0.4: Only 1-2 projects with partial data
- 0.0-0.2: No meaningful financial analysis

## Notes
- Accept reasonable rounding differences (NPV within 50, IRR within 1%, payback within 0.1)
"""

    _ANALYSIS_RUBRIC = """\
Evaluate the quality of the investment methodology and recommendation (0.0-1.0).

## Expected methodology
- Formula: financial_score x 0.6 + risk_score x 0.4
- Financial ranking: NPV 50% + IRR 30% + payback 20%, mapped to 100/70/40
- Risk mapping: Low=100, Medium=70, High=40

## Expected recommendation
- Priority 1 (Primary choice): Project C (highest composite 82.0, low risk)
- Priority 2 (Alternate choice): Project B (strong financials but high risk)
- Priority 3 (Deferred): Project A (lowest composite 52.0)

## Scoring tiers
- 0.9-1.0: Methodology clearly explained; risk-return matrix present; correct priority ranking with rationale
- 0.7-0.8: Correct ranking with some methodology explanation
- 0.5-0.6: Correct ranking but thin rationale
- 0.3-0.4: Partial ranking or wrong priority order
- 0.0-0.2: No meaningful recommendation
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
        final_text = self._get_final_assistant_text(messages)
        normalized = final_text.replace(",", "")

        # 1. No tool gate (pure attachment analysis)

        # 2. Deterministic: project names + final composite scores + ranking
        det_score = self._score_deterministic(normalized, final_text)

        # 3. Judge
        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            data_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._DATA_RUBRIC
            ).score
            analysis_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._ANALYSIS_RUBRIC
            ).score
        else:
            data_score = self._fb_data(normalized, final_text)
            analysis_score = self._fb_analysis(normalized, final_text)

        # 4. Combine: deterministic (30%) + judge data (35%) + judge analysis (35%)
        completion = (
            0.30 * det_score
            + 0.35 * data_score
            + 0.35 * analysis_score
        )

        scores.completion = min(round(completion, 2), 1.0)
        scores.robustness = 1.0
        scores.communication = self._score_communication(final_text)
        scores.efficiency_turns = len([m for m in messages if m.message.role == "assistant"])
        return scores

    def _has_number_in_range(self, text: str, low: float, high: float) -> bool:
        for match in re.finditer(r"-?\d+(?:\.\d+)?", text):
            value = float(match.group(0))
            if low <= value <= high:
                return True
        return False

    @staticmethod
    def _has_project(text_lower: str, letter: str) -> bool:
        """Match project name variants."""
        targets = [f"project {letter}", f"project{letter}"]
        return any(t in text_lower for t in targets)

    def _score_deterministic(self, normalized: str, text: str) -> float:
        lowered = normalized.lower()
        score = 0.0
        # All 3 projects mentioned
        projs = sum(1 for letter in ["a", "b", "c"] if self._has_project(lowered, letter))
        score += 0.20 * min(projs / 3, 1.0)

        # Final composite scores
        if self._has_number_in_range(normalized, 81.5, 82.5):
            score += 0.15  # C = 82.0
        if self._has_number_in_range(normalized, 75.5, 76.5):
            score += 0.12  # B = 76.0
        if self._has_number_in_range(normalized, 51.5, 52.5):
            score += 0.12  # A = 52.0

        # Correct priority order: C primary, B alternate, A deferred
        if self._has_project(lowered, "c") and any(kw in lowered for kw in ["primary", "priority 1", "first choice", "top priority"]):
            score += 0.14
        if self._has_project(lowered, "b") and any(kw in lowered for kw in ["alternate", "priority 2", "second choice"]):
            score += 0.14
        if self._has_project(lowered, "a") and any(kw in lowered for kw in ["defer", "priority 3", "lowest", "third"]):
            score += 0.13

        return min(score, 1.0)

    def _fb_data(self, normalized: str, text: str) -> float:
        lowered = normalized.lower()
        score = 0.0
        # NPV values
        if self._has_number_in_range(normalized, 1800, 1975):
            score += 0.12
        if self._has_number_in_range(normalized, 3500, 3660):
            score += 0.12
        if self._has_number_in_range(normalized, 2525, 2665):
            score += 0.12
        # IRR values
        if self._has_number_in_range(normalized, 22.0, 23.5):
            score += 0.08
        if self._has_number_in_range(normalized, 29.4, 30.6):
            score += 0.08
        if self._has_number_in_range(normalized, 25.5, 26.6):
            score += 0.08
        # Payback values
        if self._has_number_in_range(normalized, 2.7, 2.95):
            score += 0.06
        if self._has_number_in_range(normalized, 2.3, 2.55):
            score += 0.06
        if self._has_number_in_range(normalized, 2.5, 2.75):
            score += 0.06
        # Risk levels
        if "medium" in lowered and "high" in lowered and "low" in lowered:
            score += 0.1
        # Has table
        if "|" in text and "---" in text:
            score += 0.12
        return min(score, 1.0)

    def _fb_analysis(self, normalized: str, text: str) -> float:
        lowered = normalized.lower()
        score = 0.0
        if "matrix" in lowered:
            score += 0.15
        if "risk" in lowered and ("return" in lowered or "reward" in lowered):
            score += 0.15
        if "primary" in lowered and "alternate" in lowered and "defer" in lowered:
            score += 0.2
        if "0.6" in lowered or "60%" in lowered:
            score += 0.1
        if "0.4" in lowered or "40%" in lowered:
            score += 0.1
        if "npv" in lowered and "irr" in lowered:
            score += 0.15
        if "payback" in lowered:
            score += 0.15
        return min(score, 1.0)

    def _score_communication(self, text: str) -> float:
        entities = ["Project A", "Project B", "Project C", "NPV", "IRR", "82.0", "76.0", "52.0"]
        return self.compute_communication_substance(text, entities, 1.0)
