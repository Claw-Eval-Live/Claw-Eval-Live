"""CTB_MGMT_01 grader -- quarterly OKR review.

Ground truth source: fixtures/notes + fixtures/todo

v2.2: Claw-Eval mode (management analysis report).
- Deterministic 35%: tool gate, department coverage, key achievement values
- Judge 65%: KR achievement accuracy, department assessments, Q2 recommendations
- Fallback: keyword-based scoring when judge unavailable

Ground truth:
  Engineering: O1-KR1 99.97%, O1-KR2 miss(66.7%), O1-KR3 120%
  Marketing: O1-KR1 124%, O1-KR2 100%, O1-KR3 125%
  Sales: O1-KR1 112.5%, O1-KR2 112%, O1-KR3 103.5%
  Best: Sales. Needs improvement: Engineering (KR2 miss)
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class Grader(AbstractGrader):
    """Grade quarterly OKR review report."""

    _KR_ACCURACY_RUBRIC = """\
Evaluate the accuracy of KR achievement rate calculations (0.0-1.0).

## Ground Truth
Engineering:
- KR1 (Uptime 99.9%): Actual 99.92%, achievement ~100%
- KR2 (P0 incidents <=3): Actual ~5, achievement ~66.7% (MISS)
- KR3 (Deploy time <15min): Actual ~12min, achievement ~120% (exceeded)

Marketing:
- KR1 (Website traffic +20%): Actual +24.8%, achievement ~124%
- KR2 (Leads >=500): Actual 500, achievement 100%
- KR3 (NPS +8pts): Actual +10pts, achievement ~125%

Sales:
- KR1 (Revenue 8M): Actual 9M, achievement 112.5%
- KR2 (New customers 25): Actual 28, achievement 112%
- KR3 (Customer retention 90%): Actual 93.1%, achievement ~103.5%

## Scoring tiers
- 0.9-1.0: All departments with correct KR achievement rates and correct identification of misses
- 0.7-0.8: Most KR data correct; Engineering KR2 miss identified
- 0.5-0.6: Some KR data correct; partial department coverage
- 0.3-0.4: Minimal KR data
- 0.0-0.2: No meaningful KR analysis
"""

    _ASSESSMENT_RUBRIC = """\
Evaluate the quality of department assessments and Q2 recommendations (0.0-1.0).

## Ground Truth
- Best performing: Sales (all KRs exceeded)
- Needs improvement: Engineering (KR2 miss -- too many P0 incidents)
- Root cause for Engineering: reliability/incident management issues
- Q2 recommendations: Engineering should focus on incident prevention, SRE practices

## Scoring tiers
- 0.9-1.0: Correct best/worst identification; root cause analysis; specific Q2 recommendations
- 0.7-0.8: Best/worst correct; some root cause; general recommendations
- 0.5-0.6: Partial identification; vague recommendations
- 0.3-0.4: Minimal assessment
- 0.0-0.2: No assessment
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
        det_score += 0.35 * self._score_key_values(all_text)
        det_score += 0.35 * self._score_assessments(lower)

        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            kr_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._KR_ACCURACY_RUBRIC
            ).score
            assess_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._ASSESSMENT_RUBRIC
            ).score
        else:
            kr_score = self._fallback_kr(all_text, lower)
            assess_score = self._fallback_assess(lower)

        completion = tool_penalty * (
            0.35 * det_score
            + 0.35 * kr_score
            + 0.30 * assess_score
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
        notes = [d for d in dispatches if d.tool_name in ("notes_list", "notes_get", "notes_list_documents", "notes_get_document") and d.response_status < 400]
        todo = [d for d in dispatches if d.tool_name in ("todo_list_tasks", "todo_get_task") and d.response_status < 400]
        if not notes and not todo:
            return 0.2
        if not notes or not todo:
            return 0.5
        return 1.0

    def _score_dept_coverage(self, lower: str) -> float:
        depts = ["engineering", "marketing", "sales"]
        found = sum(1 for d in depts if d in lower)
        return found / len(depts)

    def _score_key_values(self, all_text: str) -> float:
        values = ["99.92", "112.5", "112%", "124", "125"]
        found = sum(1 for v in values if v in all_text)
        return min(found / 3, 1.0)

    def _score_assessments(self, lower: str) -> float:
        score = 0.0
        if "sales" in lower and any(kw in lower for kw in ["best", "top", "excellent", "exceed", "outstanding"]):
            score += 0.5
        if "engineering" in lower and any(kw in lower for kw in ["improv", "miss", "below", "gap", "underperform"]):
            score += 0.5
        return min(score, 1.0)

    def _fallback_kr(self, all_text: str, lower: str) -> float:
        """_fallback_: dev-only."""
        score = 0.0
        values = ["99.92", "112", "124", "125", "103"]
        score += 0.50 * min(sum(1 for v in values if v in all_text) / 3, 1.0)
        kr_kw = ["achievement", "okr", "kr", "key result"]
        score += 0.30 * min(sum(1 for kw in kr_kw if kw in lower) / 2, 1.0)
        if "miss" in lower or "below" in lower or "gap" in lower:
            score += 0.20
        return min(score, 1.0)

    def _fallback_assess(self, lower: str) -> float:
        """_fallback_: dev-only."""
        score = 0.0
        if "sales" in lower and any(kw in lower for kw in ["best", "top"]):
            score += 0.30
        if "engineering" in lower and any(kw in lower for kw in ["improv", "miss"]):
            score += 0.30
        if "q2" in lower and any(kw in lower for kw in ["recommend", "improv"]):
            score += 0.20
        if "incident" in lower or "p0" in lower:
            score += 0.20
        return min(score, 1.0)
