"""CTB_MGMT_02 grader -- budget allocation proposal.

Ground truth source: fixtures/finance + fixtures/gmail

v2.2: Claw-Eval mode (management analysis report).
- Deterministic 35%: tool gate, Q1 spend data, budget constraint, CEO priorities
- Judge 65%: Q1 analysis accuracy, Q2 allocation rationale, report quality
- Fallback: keyword-based scoring when judge unavailable

Ground truth:
  Q1 actual: Engineering 850K, Marketing 320K, Sales 180K, Admin 150K
  Q2 total budget: 1.8M, Admin+HR fixed 245K, remaining 1.555M
  CEO priorities: Engineering full budget (AI strategy), Marketing reduction
  Over-budget situation: total requests exceed 1.8M
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class Grader(AbstractGrader):
    """Grade budget allocation proposal."""

    _Q1_ANALYSIS_RUBRIC = """\
Evaluate the accuracy of Q1 spending analysis (0.0-1.0).

## Ground Truth -- Q1 Actual Spend
- Engineering: 850,000 CNY
- Marketing: 320,000 CNY
- Sales: 180,000 CNY
- Administration: 150,000 CNY
- HR: ~95,000 CNY
- Total Q1 actual: ~1,595,000 CNY

## Scoring tiers
- 0.9-1.0: All department Q1 figures correct; total calculated
- 0.7-0.8: Most departments correct; total approximately right
- 0.5-0.6: Some figures correct
- 0.3-0.4: Minimal Q1 data
- 0.0-0.2: No Q1 analysis
"""

    _ALLOCATION_RUBRIC = """\
Evaluate the quality of Q2 allocation rationale and plan (0.0-1.0).

## Ground Truth
- Q2 total budget: 1,800,000 CNY
- Admin + HR fixed at Q1 levels (~245K combined)
- Remaining 1,555K for Engineering, Marketing, Sales
- CEO priorities: Engineering gets full budget request (AI strategy); Marketing must be reduced
- Total Q2 requests exceed budget -- need to cut Marketing or Sales
- Must explain reductions with justification

## Scoring tiers
- 0.9-1.0: Budget constraint respected; CEO priorities reflected; clear reduction rationale; complete allocation per department
- 0.7-0.8: Budget mostly balanced; priorities mentioned; some justification
- 0.5-0.6: Budget acknowledged; partial allocation plan
- 0.3-0.4: Mentions budget but incomplete plan
- 0.0-0.2: No allocation plan
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
        clean = all_text.replace(",", "").replace(" ", "")

        tool_penalty = self._tool_gate(dispatches)

        det_score = 0.0
        det_score += 0.30 * self._score_q1_data(lower, clean)
        det_score += 0.35 * self._score_budget_constraint(lower, clean)
        det_score += 0.35 * self._score_ceo_priorities(lower)

        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            q1_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._Q1_ANALYSIS_RUBRIC
            ).score
            alloc_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._ALLOCATION_RUBRIC
            ).score
        else:
            q1_score = self._fallback_q1(lower, clean)
            alloc_score = self._fallback_alloc(lower)

        completion = tool_penalty * (
            0.35 * det_score
            + 0.30 * q1_score
            + 0.35 * alloc_score
        )

        scores.completion = min(round(completion, 2), 1.0)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len(
            [m for m in messages if m.message.role == "assistant"]
        )
        return scores

    def _tool_gate(self, dispatches: list[ToolDispatch]) -> float:
        fin = [d for d in dispatches if d.tool_name == "finance_list_transactions" and d.response_status < 400]
        gmail = [d for d in dispatches if d.tool_name in ("gmail_list_messages", "gmail_get_message") and d.response_status < 400]
        if not fin and not gmail:
            return 0.2
        if not fin or not gmail:
            return 0.5
        return 1.0

    def _score_q1_data(self, lower: str, clean: str) -> float:
        pairs = [("engineer", ["850000", "850"]), ("market", ["320000", "320"]),
                  ("sales", ["180000", "180"]), ("admin", ["150000", "150"])]
        found = sum(1 for dept, vals in pairs if dept in lower and any(v in clean for v in vals))
        return min(found / 3, 1.0)

    def _score_budget_constraint(self, lower: str, clean: str) -> float:
        score = 0.0
        if "180" in clean and any(kw in lower for kw in ["budget", "total", "allocation", "million"]):
            score += 0.5
        if any(kw in lower for kw in ["exceed", "over budget", "reduce", "cut", "shortfall"]):
            score += 0.5
        return min(score, 1.0)

    def _score_ceo_priorities(self, lower: str) -> float:
        score = 0.0
        if re.search(r'engineer.*(?:priorit|full|ai|strateg)', lower):
            score += 0.5
        if re.search(r'market.*(?:reduc|cut|adjust|lower)', lower):
            score += 0.5
        return min(score, 1.0)

    def _fallback_q1(self, lower: str, clean: str) -> float:
        """_fallback_: dev-only."""
        score = 0.0
        pairs = [("engineer", "850"), ("market", "320"), ("sales", "180"), ("admin", "150")]
        found = sum(1 for dept, val in pairs if dept in lower and val in clean)
        score += 0.60 * min(found / 3, 1.0)
        if "q1" in lower:
            score += 0.20
        if "|" in clean or "table" in lower:
            score += 0.20
        return min(score, 1.0)

    def _fallback_alloc(self, lower: str) -> float:
        """_fallback_: dev-only."""
        score = 0.0
        alloc_kw = ["allocat", "proposal", "recommend", "q2", "rationale", "budget"]
        score += 0.40 * min(sum(1 for kw in alloc_kw if kw in lower) / 3, 1.0)
        if any(kw in lower for kw in ["priorit", "ceo", "strateg"]):
            score += 0.30
        if any(kw in lower for kw in ["reduce", "cut", "exceed"]):
            score += 0.30
        return min(score, 1.0)
