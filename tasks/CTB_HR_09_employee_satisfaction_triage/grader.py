"""CTB_HR_09 grader -- employee satisfaction triage.

Ground truth source: task.yaml reference_solution + fixtures.

v2.2: Claw-Eval mode (HR analysis report).
- Deterministic 35%: tool gate, dept coverage, lowest scores, decline values
- Judge 65%: satisfaction data accuracy, intervention recommendations
- Fallback: keyword-based scoring when judge unavailable

Ground truth:
  Lowest: Engineering work-life balance 2.8/5, Marketing career growth 2.9/5,
    Finance compensation 3.0/5, Product career growth 3.3/5
  Declines: Engineering WLB -0.8, Product mgmt -0.7, Marketing career -0.6, Eng comp -0.5
  Issues: overtime (Eng5+Fin2), compensation (Eng3+Fin3), promotion (Mkt4), cross-dept (Mkt3+Prod2)
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class Grader(AbstractGrader):
    """Grade employee satisfaction triage report."""

    _SATISFACTION_DATA_RUBRIC = """\
Evaluate the accuracy of satisfaction scores and decline analysis (0.0-1.0).

## Ground Truth -- Lowest Scores
- Engineering: work-life balance 2.8/5 (lowest overall)
- Marketing: career growth 2.9/5
- Finance: compensation 3.0/5
- Product: career growth 3.3/5

## Largest Declines from Previous Quarter
- Engineering work-life balance: -0.8
- Product management quality: -0.7
- Marketing career growth: -0.6
- Engineering compensation: -0.5

## Scoring tiers
- 0.9-1.0: All department scores correct; all decline values present
- 0.7-0.8: Most scores correct; key declines mentioned
- 0.5-0.6: Some scores; partial decline data
- 0.3-0.4: Minimal data
- 0.0-0.2: No meaningful data
"""

    _INTERVENTION_RUBRIC = """\
Evaluate the quality of intervention recommendations (0.0-1.0).

## Expected Interventions
- Top 3 critical issues: work-life balance (Engineering 2.8), career growth (Marketing 2.9), compensation (Finance 3.0)
- Common issues: overtime/pressure, compensation competitiveness, promotion pathways, cross-department collaboration
- Recommended actions addressing each critical area
- Department-level action plans

## Scoring tiers
- 0.9-1.0: All critical issues identified; specific actionable interventions per department
- 0.7-0.8: Key issues identified; some interventions
- 0.5-0.6: Partial issue identification; generic recommendations
- 0.3-0.4: Minimal recommendations
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

        tool_penalty = self._tool_gate(dispatches)

        det_score = 0.0
        det_score += 0.30 * self._score_dept_coverage(lower)
        det_score += 0.40 * self._score_key_scores(all_text)
        det_score += 0.30 * self._score_decline_values(all_text)

        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            data_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._SATISFACTION_DATA_RUBRIC
            ).score
            intervention_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._INTERVENTION_RUBRIC
            ).score
        else:
            data_score = self._fallback_data(all_text, lower)
            intervention_score = self._fallback_intervention(all_text, lower)

        completion = tool_penalty * (
            0.35 * det_score
            + 0.35 * data_score
            + 0.30 * intervention_score
        )

        scores.completion = min(round(completion, 2), 1.0)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len(
            [m for m in messages if m.message.role == "assistant"]
        )
        return scores

    @staticmethod
    def _has_bounded(text: str, num: str) -> bool:
        return bool(re.search(rf'(?<!\d){re.escape(num)}(?!\d)', text))

    def _tool_gate(self, dispatches: list[ToolDispatch]) -> float:
        gmail = [d for d in dispatches if d.tool_name in ("gmail_list_messages", "gmail_get_message") and d.response_status < 400]
        crm = [d for d in dispatches if d.tool_name in ("crm_list_customers", "crm_get_customer") and d.response_status < 400]
        if not gmail and not crm:
            return 0.2
        if not gmail or not crm:
            return 0.5
        return 1.0

    def _score_dept_coverage(self, lower: str) -> float:
        depts = ["engineering", "marketing", "finance", "product"]
        found = sum(1 for d in depts if d in lower)
        return found / len(depts)

    def _score_key_scores(self, all_text: str) -> float:
        key = ["2.8", "2.9", "3.0", "3.3"]
        found = sum(1 for s in key if self._has_bounded(all_text, s))
        return min(found / 3, 1.0)

    def _score_decline_values(self, all_text: str) -> float:
        declines = ["0.8", "0.7", "0.6", "0.5"]
        found = sum(1 for d in declines if self._has_bounded(all_text, d))
        return min(found / 3, 1.0)

    def _fallback_data(self, all_text: str, lower: str) -> float:
        """_fallback_: dev-only."""
        score = 0.0
        depts = ["engineering", "marketing", "finance", "product"]
        score += 0.30 * min(sum(1 for d in depts if d in lower) / 3, 1.0)
        scores_list = ["2.8", "2.9", "3.0"]
        score += 0.40 * min(sum(1 for s in scores_list if s in all_text) / 2, 1.0)
        topics = ["work-life", "career", "compensation", "overtime"]
        score += 0.30 * min(sum(1 for t in topics if t in lower) / 3, 1.0)
        return min(score, 1.0)

    def _fallback_intervention(self, all_text: str, lower: str) -> float:
        """_fallback_: dev-only."""
        score = 0.0
        rec_kw = ["recommend", "improv", "action", "intervention", "plan"]
        score += 0.50 * min(sum(1 for kw in rec_kw if kw in lower) / 2, 1.0)
        issues = ["overtime", "compensation", "promotion", "cross-department"]
        score += 0.30 * min(sum(1 for i in issues if i in lower) / 2, 1.0)
        if len(all_text.strip()) >= 300:
            score += 0.20
        return min(score, 1.0)
