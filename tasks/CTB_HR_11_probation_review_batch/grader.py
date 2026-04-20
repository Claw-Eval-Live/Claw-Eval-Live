"""CTB_HR_11 grader -- probation review batch.

Ground truth source: task.yaml reference_solution + fixtures.

v2.2: analysis mode (HR analysis report).
- Deterministic 35%: tool gate, employee coverage, decision correctness, special cases
- Judge 65%: assessment data accuracy, decision quality
- Fallback: keyword-based scoring when judge unavailable

Ground truth:
  Confirmed (3/60%): Wang Jianguo (92/95/90, excellent+5% raise),
    Qian Xiaolei (85/90/88), Lin Zhiqiang (78/82/80, needs monitoring)
  Extended (1/20%): Sun Lihua (70/75/65, below targets, +3 months)
  Not confirmed (1/20%): Huang Lijuan (60/55/50, repeated tardiness)
  Ranking: Wang > Qian > Lin > Sun > Huang
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class Grader(AbstractGrader):
    """Grade probation review batch report."""

    _ASSESSMENT_RUBRIC = """\
Evaluate the accuracy of per-employee probation assessment data (0.0-1.0).

## Ground Truth
- Wang Jianguo: Scores 92/95/90, CONFIRM with 5% raise and commendation. Outstanding performance.
- Qian Xiaolei: Scores 85/90/88, CONFIRM. Good performance.
- Lin Zhiqiang: Scores 78/82/80, CONFIRM with monitoring note. Barely met targets.
- Sun Lihua: Scores 70/75/65, EXTEND probation by 3 months. Below targets.
- Huang Lijuan: Scores 60/55/50, NOT CONFIRMED (terminate). Significantly below targets, repeated tardiness.

## Scoring tiers
- 0.9-1.0: All 5 employees with correct scores and decisions
- 0.7-0.8: All employees; most scores and decisions correct
- 0.5-0.6: 3+ employees; some data correct
- 0.3-0.4: Partial coverage
- 0.0-0.2: No meaningful data
"""

    _DECISION_RUBRIC = """\
Evaluate the quality of decisions and special notes (0.0-1.0).

## Expected elements
- Statistics: 3 confirmed (60%), 1 extended (20%), 1 not confirmed (20%)
- Wang Jianguo: recommend 5% salary raise + commendation
- Huang Lijuan: repeated tardiness and lack of initiative cited as reasons
- Ranking from best to worst: Wang > Qian > Lin > Sun > Huang
- Lin Zhiqiang's borderline case flagged for continued monitoring

## Scoring tiers
- 0.9-1.0: All statistics correct; special notes for Wang and Huang; proper ranking
- 0.7-0.8: Most statistics; some special notes
- 0.5-0.6: Partial statistics; mentions key cases
- 0.3-0.4: Minimal decision structure
- 0.0-0.2: No decision framework
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
        det_score += 0.30 * self._score_employee_coverage(all_text)
        det_score += 0.35 * self._score_decision_correctness(all_text, lower)
        det_score += 0.35 * self._score_special_cases(all_text, lower)

        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            assess_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._ASSESSMENT_RUBRIC
            ).score
            decision_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._DECISION_RUBRIC
            ).score
        else:
            assess_score = self._fallback_assessment(all_text, lower)
            decision_score = self._fallback_decision(all_text, lower)

        completion = tool_penalty * (
            0.35 * det_score
            + 0.35 * assess_score
            + 0.30 * decision_score
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
        employees = ["Wang Jianguo", "Qian Xiaolei", "Lin Zhiqiang", "Sun Lihua", "Huang Lijuan"]
        found = sum(1 for e in employees if e in all_text)
        return min(found / 4, 1.0)

    def _score_decision_correctness(self, all_text: str, lower: str) -> float:
        score = 0.0
        # 3 confirmed
        confirm_kw = ["confirm", "permanent", "pass"]
        if re.search(r'(?:3|three)\s*(?:confirm|pass|permanent)', lower) or \
           (re.search(r'60%', all_text) and any(kw in lower for kw in confirm_kw)):
            score += 0.33
        # 1 extended
        extend_kw = ["extend", "postpone", "delay"]
        if any(kw in lower for kw in extend_kw) and re.search(r'(?:1|one|20%)', all_text):
            score += 0.33
        # 1 not confirmed / terminated
        term_kw = ["not confirm", "terminat", "dismiss", "fail"]
        if any(kw in lower for kw in term_kw):
            score += 0.34
        return min(score, 1.0)

    def _score_special_cases(self, all_text: str, lower: str) -> float:
        score = 0.0
        # Wang Jianguo + raise
        if "Wang Jianguo" in all_text and any(kw in lower for kw in ["raise", "salary increase", "5%", "commend", "outstanding"]):
            score += 0.5
        # Huang Lijuan + tardiness
        if "Huang Lijuan" in all_text and any(kw in lower for kw in ["late", "tardiness", "initiative", "terminate", "dismiss"]):
            score += 0.5
        return min(score, 1.0)

    def _fallback_assessment(self, all_text: str, lower: str) -> float:
        """_fallback_: dev-only."""
        score = 0.0
        employees = ["Wang Jianguo", "Qian Xiaolei", "Lin Zhiqiang", "Sun Lihua", "Huang Lijuan"]
        score += 0.30 * min(sum(1 for e in employees if e in all_text) / 4, 1.0)
        perf_scores = ["92", "95", "85", "90", "70", "65", "60", "55", "50"]
        score += 0.40 * min(sum(1 for s in perf_scores if s in all_text) / 5, 1.0)
        if any(kw in lower for kw in ["confirm", "extend", "terminat"]):
            score += 0.30
        return min(score, 1.0)

    def _fallback_decision(self, all_text: str, lower: str) -> float:
        """_fallback_: dev-only."""
        score = 0.0
        if "60%" in all_text or "20%" in all_text:
            score += 0.30
        if "Wang Jianguo" in all_text and any(kw in lower for kw in ["raise", "5%"]):
            score += 0.25
        if "Huang Lijuan" in all_text and any(kw in lower for kw in ["late", "tardiness"]):
            score += 0.25
        if "|" in all_text and "---" in all_text:
            score += 0.20
        return min(score, 1.0)
