"""CTB_CRM_08 grader -- customer segment profitability analysis.

Ground truth source: task.yaml reference_solution + fixtures.

v2.2: Claw-Eval mode (financial analysis report).
- Deterministic 35%: tool gate, segment numbers, key conclusions
- Judge 65%: financial accuracy, analysis quality
- Fallback: keyword-based scoring when judge unavailable

Ground truth:
  VIP: Revenue 1.45M, Cost 520K, Profit 930K, Margin 64.1%
  Standard: Revenue 600K, Cost 150K, Profit 450K, Margin 75% (most profitable)
  Basic: Revenue 245K, Cost 105K, Profit 140K, Margin 57.1% (needs optimization)
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class Grader(AbstractGrader):
    """Grade customer segment profitability analysis."""

    @staticmethod
    def _has_bounded(text: str, num: str) -> bool:
        return bool(re.search(rf'(?<!\d){re.escape(num)}(?!\d)', text))

    # ── Judge rubrics ──────────────────────────────────────────────

    _FINANCIAL_ACCURACY_RUBRIC = """\
Evaluate the accuracy of segment financial data (0.0-1.0).

## Ground Truth
1. VIP Segment (Huading + Dongfang):
   - Revenue: 1,450,000 (1.45M)
   - Cost: 520,000 (520K)
   - Profit: 930,000 (930K)
   - Margin: ~64.1%

2. Standard Segment (Xinyuan + Qingsong):
   - Revenue: 600,000 (600K)
   - Cost: 150,000 (150K)
   - Profit: 450,000 (450K)
   - Margin: 75%

3. Basic Segment (Qihang + Langshi):
   - Revenue: 245,000 (245K)
   - Cost: 105,000 (105K)
   - Profit: 140,000 (140K)
   - Margin: ~57.1%

## Scoring tiers
- 0.9-1.0: All 3 segments with correct revenue, cost, profit, and margin
- 0.7-0.8: All segments present; most numbers correct (within 5%)
- 0.5-0.6: 2-3 segments; some numbers correct
- 0.3-0.4: 1-2 segments; few correct numbers
- 0.0-0.2: No meaningful financial analysis
"""

    _ANALYSIS_RUBRIC = """\
Evaluate the quality of the profitability analysis and conclusions (0.0-1.0).

## Expected conclusions
1. Standard segment has the highest profit margin (75%) -- most profitable per dollar
2. Basic segment has the lowest margin (57.1%) -- most in need of optimization
3. VIP segment generates the most absolute profit but not the highest margin
4. Cross-reference CRM tier data with financial transactions

## Scoring tiers
- 0.9-1.0: Both key conclusions correct (Standard=best margin, Basic=needs optimization); insightful analysis
- 0.7-0.8: Key conclusions present; reasonable analysis
- 0.5-0.6: Partial conclusions; basic analysis
- 0.3-0.4: Minimal conclusions
- 0.0-0.2: No meaningful analysis
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

        # 2. Deterministic checks (35%)
        det_score = 0.0
        det_score += 0.35 * self._score_segment_numbers(clean, lower)
        det_score += 0.35 * self._score_key_conclusions(all_text, clean, lower)
        det_score += 0.30 * self._score_data_retrieval(dispatches)

        # 3. Judge scoring (65%)
        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            financial_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._FINANCIAL_ACCURACY_RUBRIC
            ).score
            analysis_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._ANALYSIS_RUBRIC
            ).score
        else:
            financial_score = self._fallback_financial(clean, lower)
            analysis_score = self._fallback_analysis(all_text, lower)

        # 4. Combine
        completion = tool_penalty * (
            0.35 * det_score
            + 0.35 * financial_score
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
        crm_calls = [d for d in dispatches
                     if d.tool_name in ("crm_list_customers", "crm_get_customer")
                     and d.response_status < 400]
        fin_calls = [d for d in dispatches
                     if d.tool_name == "finance_list_transactions" and d.response_status < 400]
        if not crm_calls and not fin_calls:
            return 0.2
        if not crm_calls or not fin_calls:
            return 0.5
        return 1.0

    def _score_segment_numbers(self, clean: str, lower: str) -> float:
        """Check for key financial numbers across 3 segments."""
        checks = [
            any(v in clean for v in ["1450000", "1450K", "145万"]) or "1.45m" in lower,
            any(v in clean for v in ["600000", "600K", "60万"]) or "600k" in lower,
            any(v in clean for v in ["245000", "245K"]) or "24.5万" in clean,
            self._has_bounded(clean, "75") and "%" in clean,
            self._has_bounded(clean, "64") or self._has_bounded(clean, "64.1"),
            self._has_bounded(clean, "57") or self._has_bounded(clean, "57.1"),
        ]
        found = sum(1 for c in checks if c)
        return min(found / 4, 1.0)

    def _score_key_conclusions(self, all_text: str, clean: str, lower: str) -> float:
        score = 0.0
        if (re.search(r"[Ss]tandard.{0,30}(most|highest|profitable|best|margin)", all_text)
                or "75%" in clean):
            score += 0.50
        if (re.search(r"[Bb]asic.{0,30}(optimi|improv|low|worst|lowest)", all_text)
                or re.search(r"57", clean)):
            score += 0.50
        return min(score, 1.0)

    def _score_data_retrieval(self, dispatches: list[ToolDispatch]) -> float:
        crm_calls = [d for d in dispatches
                     if d.tool_name in ("crm_list_customers", "crm_get_customer")
                     and d.response_status < 400]
        fin_calls = [d for d in dispatches
                     if d.tool_name == "finance_list_transactions" and d.response_status < 400]
        return 0.50 * min(len(crm_calls) / 2, 1.0) + 0.50 * (1.0 if fin_calls else 0.0)

    # ── Fallback scorers ───────────────────────────────────────────

    def _fallback_financial(self, clean: str, lower: str) -> float:
        """_fallback_: dev-only keyword scoring."""
        score = 0.0
        if "vip" in lower:
            score += 0.10
        if "standard" in lower:
            score += 0.10
        if "basic" in lower:
            score += 0.10
        if "75" in clean:
            score += 0.10
        if any(v in clean for v in ["1450000", "1.45m", "1450k"]):
            score += 0.10
        if any(v in clean for v in ["600000", "600k"]):
            score += 0.10
        return min(score, 1.0)

    def _fallback_analysis(self, all_text: str, lower: str) -> float:
        """_fallback_: dev-only keyword scoring for analysis."""
        score = 0.0
        if "profitable" in lower or "profitability" in lower:
            score += 0.20
        if "optimization" in lower or "improve" in lower:
            score += 0.20
        if "margin" in lower:
            score += 0.15
        if len(all_text.strip()) >= 300:
            score += 0.15
        return min(score, 1.0)
