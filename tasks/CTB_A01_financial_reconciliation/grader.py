"""CTB_A01 grader — reconcile three finance tables into an exception report.

v2.2: hybrid deterministic + judge scoring.
- Deterministic 35%: TX IDs, key amounts, total impact, matched count, table structure
- Judge 65%: exception categorization, recommendations, report structure
- Fallback: _fallback_ prefix, dev-only
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class FinancialReconciliationGrader(AbstractGrader):
    """Grade a structured reconciliation analysis."""

    # ── Judge rubrics (each asks ONE question) ─────────────────────

    _EXCEPTION_RUBRIC = """\
Evaluate whether each exception is correctly CATEGORIZED with the right type and amounts (0.0-1.0).

## Ground Truth — 5 exceptions, each with a specific type
1. TX-1002: "amount discrepancy" — CRM/bank 980, invoice 890, difference 90
2. TX-1003: "missing bank entry" — CRM/invoice 1499, no bank record
3. TX-1004: "missing invoice" — CRM/bank 730, no invoice
4. TX-1005: "amount discrepancy" — CRM/invoice 2200, bank 2150, difference 50 (reserve/holdback)
5. UNMAPPED-7781: "orphan bank entry" — bank-only 560, no CRM match

## Scoring tiers
- 0.9-1.0: All 5 correctly categorized with amounts and types
- 0.7-0.8: 4-5 identified, most types correct
- 0.5-0.6: 3-4 identified, some type confusion
- 0.3-0.4: Only 1-2 identified
- 0.0-0.2: No meaningful categorization
"""

    _RECOMMENDATION_RUBRIC = """\
Evaluate the quality and specificity of action recommendations (0.0-1.0).

## Expected recommendations (one per exception)
- TX-1002: Verify invoice amount (890 vs 980), likely billing error
- TX-1003: Trace missing bank settlement for 1,499
- TX-1004: Issue or locate missing invoice for 730
- TX-1005: Investigate 50 holdback/reserve difference
- UNMAPPED-7781: Identify source of orphan bank entry (560)

## Scoring tiers
- 0.9-1.0: All 5 exceptions have specific, actionable recommendations with priority
- 0.7-0.8: Most exceptions have recommendations, some generic
- 0.5-0.6: Generic recommendations, not per-exception
- 0.3-0.4: Minimal recommendations
- 0.0-0.2: No recommendations
"""

    _REPORT_RUBRIC = """\
Evaluate the overall report structure and clarity (0.0-1.0).

## Expected structure
1. Summary overview: matched count (2), exception count (5), total impact (2929)
2. Exception detail table with columns: TX ID, type, CRM/bank/invoice amounts, variance, cause
3. Action section with prioritized recommendations

## Scoring tiers
- 0.9-1.0: All 3 sections present; professional tone suitable for finance director
- 0.7-0.8: Has structure but gaps in one section
- 0.5-0.6: Partial structure, disorganized
- 0.3-0.4: Minimal structure
- 0.0-0.2: No coherent report
"""

    # ── Grading ────────────────────────────────────────────────────

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
        compact = (
            final_text.replace(",", "").replace("，", "")
            .replace("￥", "").replace("¥", "")
            .replace("cny", "").replace("CNY", "")
        )

        # 1. No tool penalty (pure attachment analysis, no API)

        # 2. Deterministic completion items (35% total)
        det = 0.0
        det += 0.10 * self._check_tx_ids(final_text)         # TX IDs mentioned
        det += 0.10 * self._check_key_amounts(compact)        # key amounts present
        det += 0.05 * self._check_total_impact(compact)       # total 2929
        det += 0.05 * self._check_summary_counts(final_text)  # matched=2, exceptions=5
        det += 0.05 * self._check_table_structure(final_text) # has markdown table

        # 3. Judge quality items (65% total, 3 rubrics)
        #    No try/except — Judge failure propagates to runner for retry/abort
        judge_score = 0.0
        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            judge_score += 0.25 * judge.evaluate(task.prompt.text, conversation, actions, self._EXCEPTION_RUBRIC).score
            judge_score += 0.20 * judge.evaluate(task.prompt.text, conversation, actions, self._RECOMMENDATION_RUBRIC).score
            judge_score += 0.20 * judge.evaluate(task.prompt.text, conversation, actions, self._REPORT_RUBRIC).score
        else:
            judge_score = self._fallback_judge(compact, final_text)

        # 4. Combine
        completion = det + judge_score

        scores.completion = min(round(completion, 2), 1.0)
        scores.robustness = 1.0
        scores.communication = self._score_communication(final_text)
        scores.efficiency_turns = len([m for m in messages if m.message.role == "assistant"])
        return scores

    # ── Deterministic items ────────────────────────────────────────

    @staticmethod
    def _has_bounded(text: str, num: str) -> bool:
        return bool(re.search(rf'(?<!\d){re.escape(num)}(?!\d)', text))

    @staticmethod
    def _check_tx_ids(text):
        """Count how many of the 5 exception IDs are mentioned."""
        ids = ["TX-1002", "TX-1003", "TX-1004", "TX-1005", "UNMAPPED-7781"]
        found = sum(1 for tx in ids if tx in text or tx.lower() in text.lower())
        return min(found / 4, 1.0)  # at least 4 of 5

    @classmethod
    def _check_key_amounts(cls, compact):
        """Count how many key amounts appear (bounded match)."""
        amounts = ["980", "890", "1499", "730", "2200", "2150", "560"]
        found = sum(1 for a in amounts if cls._has_bounded(compact, a))
        return min(found / 5, 1.0)  # at least 5 of 7

    @staticmethod
    def _check_total_impact(compact):
        return 1.0 if re.search(r"\b2929(?:\.0+)?\b", compact) else 0.0

    @staticmethod
    def _check_summary_counts(text):
        """Check if matched=2 and exceptions=5 are stated."""
        score = 0.0
        if re.search(r"(?:full match|完全匹配).{0,8}2", text, re.IGNORECASE):
            score += 0.5
        if re.search(r"(?:exception|异常).{0,8}5", text, re.IGNORECASE):
            score += 0.5
        return min(score, 1.0)

    @staticmethod
    def _check_table_structure(text):
        return 1.0 if ("|" in text and "---" in text) else 0.0

    # ── Fallback (dev-only) ────────────────────────────────────────

    @classmethod
    def _fallback_judge(cls, compact, text):
        """_fallback_: keyword-based, only for --no-judge dev mode."""
        score = 0.0
        if "TX-1002" in text.upper() and cls._has_bounded(compact, "90"):
            score += 0.08
        if "TX-1003" in text.upper() and any(kw in text.lower() for kw in ["missing bank", "bank missing", "no bank", "缺少银行", "银行缺失"]):
            score += 0.08
        if "TX-1004" in text.upper() and any(kw in text.lower() for kw in ["missing invoice", "invoice missing", "no invoice", "缺少发票", "发票缺失"]):
            score += 0.08
        if "TX-1005" in text.upper() and cls._has_bounded(compact, "50"):
            score += 0.08
        if "UNMAPPED-7781" in text and cls._has_bounded(compact, "560"):
            score += 0.08
        rec_hits = sum(1 for kw in ["trace", "issue", "verify", "investigate", "recommend", "补查", "核对", "建议"] if kw in text.lower())
        score += 0.15 * min(rec_hits / 3, 1.0)
        if len(text) > 500:
            score += 0.10
        return min(score, 0.65)

    def _score_communication(self, text: str) -> float:
        entities = ["TX-1002", "TX-1003", "TX-1004", "TX-1005", "UNMAPPED-7781", "2929"]
        return self.compute_communication_substance(text, entities, 1.0)
