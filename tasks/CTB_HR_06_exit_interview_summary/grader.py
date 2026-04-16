"""CTB_HR_06 grader -- exit interview summary.

Ground truth source: task.yaml reference_solution + fixtures.

v2.2: Claw-Eval mode (HR analysis report).
- Deterministic 35%: tool gate, employee coverage, reason frequency, department analysis
- Judge 65%: departure data accuracy, pattern analysis, retention recommendations
- Fallback: keyword-based scoring when judge unavailable

Ground truth:
  5 departures: Wang Lei (Engineering, compensation), Zhang Min (Marketing, career),
  Chen Haoran (Engineering, overtime), Lin Xue (Finance, compensation),
  Yang Fan (Product, career).
  Reasons: compensation 2 (40%), career 2 (40%), work-life balance 1 (20%).
  Departments: Engineering 2, Marketing 1, Finance 1, Product 1.
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class Grader(AbstractGrader):
    """Grade exit interview summary report."""

    _DEPARTURE_DATA_RUBRIC = """\
Evaluate the accuracy of individual departure data (0.0-1.0).

## Ground Truth -- 5 Departures
1. Wang Lei: Engineering, departure reason: compensation (below market rate)
2. Zhang Min: Marketing, departure reason: career growth/development (limited promotion)
3. Chen Haoran: Engineering, departure reason: work-life balance (60+ hour weeks, overtime)
4. Lin Xue: Finance, departure reason: compensation (below market rate)
5. Yang Fan: Product, departure reason: career growth/development (limited business scope)

## Scoring tiers
- 0.9-1.0: All 5 employees with correct departments and departure reasons
- 0.7-0.8: 4-5 employees covered; most reasons correct
- 0.5-0.6: 3+ employees; some reasons correct
- 0.3-0.4: Partial coverage
- 0.0-0.2: No meaningful data
"""

    _PATTERN_RUBRIC = """\
Evaluate the quality of pattern analysis and retention recommendations (0.0-1.0).

## Ground Truth -- Patterns
- Compensation: 2 people (40%) -- Wang Lei and Lin Xue
- Career development: 2 people (40%) -- Zhang Min and Yang Fan
- Work-life balance: 1 person (20%) -- Chen Haoran
- Engineering has highest attrition (2 out of 5)
- Recommendations: market-rate benchmarking, improve promotion framework, control overtime, expand business for internal transfers

## Scoring tiers
- 0.9-1.0: Correct frequency breakdown; department analysis; actionable recommendations
- 0.7-0.8: Most patterns identified; some recommendations
- 0.5-0.6: Partial patterns; minimal recommendations
- 0.3-0.4: Mentions reasons but no quantification
- 0.0-0.2: No pattern analysis
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
        det_score += 0.35 * self._score_employee_coverage(all_text)
        det_score += 0.35 * self._score_reason_frequency(all_text, lower)
        det_score += 0.30 * self._score_dept_analysis(all_text, lower)

        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            data_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._DEPARTURE_DATA_RUBRIC
            ).score
            pattern_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._PATTERN_RUBRIC
            ).score
        else:
            data_score = self._fallback_data(all_text, lower)
            pattern_score = self._fallback_pattern(all_text, lower)

        completion = tool_penalty * (
            0.35 * det_score
            + 0.35 * data_score
            + 0.30 * pattern_score
        )

        scores.completion = min(round(completion, 2), 1.0)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len(
            [m for m in messages if m.message.role == "assistant"]
        )
        return scores

    def _tool_gate(self, dispatches: list[ToolDispatch]) -> float:
        gmail = [d for d in dispatches if d.tool_name in ("gmail_list_messages", "gmail_get_message") and d.response_status < 400]
        crm = [d for d in dispatches if d.tool_name in ("crm_list_customers", "crm_get_customer") and d.response_status < 400]
        if not gmail and not crm:
            return 0.2
        if not gmail or not crm:
            return 0.5
        return 1.0

    def _score_employee_coverage(self, all_text: str) -> float:
        employees = ["Wang Lei", "Zhang Min", "Chen Haoran", "Lin Xue", "Yang Fan"]
        found = sum(1 for e in employees if e in all_text)
        return min(found / 4, 1.0)

    def _score_reason_frequency(self, all_text: str, lower: str) -> float:
        score = 0.0
        comp_kw = ["compensation", "salary"]
        career_kw = ["career", "development", "growth", "promotion"]
        if any(kw in lower for kw in comp_kw) and re.search(r'(?:2|two|40%)', all_text, re.IGNORECASE):
            score += 0.5
        if any(kw in lower for kw in career_kw) and re.search(r'(?:2|two|40%)', all_text, re.IGNORECASE):
            score += 0.5
        return min(score, 1.0)

    def _score_dept_analysis(self, all_text: str, lower: str) -> float:
        score = 0.0
        if "engineering" in lower and re.search(r'2\s*(people|employee|person|departure)', lower):
            score += 0.5
        depts = ["engineering", "marketing", "finance", "product"]
        found = sum(1 for d in depts if d in lower)
        score += 0.5 * min(found / 3, 1.0)
        return min(score, 1.0)

    def _fallback_data(self, all_text: str, lower: str) -> float:
        """_fallback_: dev-only."""
        score = 0.0
        employees = ["Wang Lei", "Zhang Min", "Chen Haoran", "Lin Xue", "Yang Fan"]
        score += 0.40 * min(sum(1 for e in employees if e in all_text) / 4, 1.0)
        reasons = ["compensation", "salary", "career", "overtime", "work-life"]
        score += 0.35 * min(sum(1 for r in reasons if r in lower) / 3, 1.0)
        depts = ["engineering", "marketing", "finance", "product"]
        score += 0.25 * min(sum(1 for d in depts if d in lower) / 3, 1.0)
        return min(score, 1.0)

    def _fallback_pattern(self, all_text: str, lower: str) -> float:
        """_fallback_: dev-only."""
        score = 0.0
        if "40%" in all_text:
            score += 0.25
        if "20%" in all_text:
            score += 0.15
        rec_kw = ["recommend", "benchmark", "promotion", "overtime", "intervention"]
        score += 0.40 * min(sum(1 for kw in rec_kw if kw in lower) / 2, 1.0)
        if len(all_text.strip()) >= 300:
            score += 0.20
        return min(score, 1.0)
