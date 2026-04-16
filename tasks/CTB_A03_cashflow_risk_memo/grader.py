"""CTB_A03 grader — evaluate a cashflow forecast risk memo.

v2.2: hybrid deterministic + judge scoring.
- Deterministic: monthly balance ranges, starting balance 7.4, safety line 5.0, minimum 4.1
- Judge: forecast accuracy & methodology, risk analysis & recommendations
- Fallback: keyword-based scoring when judge unavailable
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class CashflowRiskMemoGrader(AbstractGrader):
    """Grade a structured cashflow forecast memo."""

    MONTH_WINDOWS = {
        "2026-01": ["2026-01", "1月", "01月", "jan", "january"],
        "2026-02": ["2026-02", "2月", "02月", "feb", "february"],
        "2026-03": ["2026-03", "3月", "03月", "mar", "march"],
        "2026-04": ["2026-04", "4月", "04月", "apr", "april"],
        "2026-05": ["2026-05", "5月", "05月", "may"],
        "2026-06": ["2026-06", "6月", "06月", "jun", "june"],
    }

    _DATA_RUBRIC = """\
Evaluate the accuracy of the cashflow forecast data (0.0-1.0).

## Ground Truth
- Starting cash balance (2025-12-31): 7.4 million CNY
- Safety line: 5.0 million CNY
- Forecast method: use 2025 same-month values as forecast for 2026-01 to 2026-06

### Monthly ending balances (approximate)
- 2026-01: ~6.4M
- 2026-02: ~5.5M
- 2026-03: ~4.7M (below safety line)
- 2026-04: ~4.1M (minimum point, below safety line)
- 2026-05: ~4.2M (below safety line)
- 2026-06: ~4.5M (below safety line)

### Key findings
- Risk months: 2026-03 through 2026-06 (all below 5.0M safety line)
- Minimum cash point: 2026-04 at ~4.1M
- Peak funding gap: ~0.9M (in April: 5.0 - 4.1)

## Scoring tiers
- 0.9-1.0: All 6 monthly balances within reasonable range; correct risk months; correct minimum and gap
- 0.7-0.8: Most balances correct; risk months identified; minor calculation differences
- 0.5-0.6: Correct trend but some balance errors; partial risk identification
- 0.3-0.4: Only a few months correct; missing key risk identification
- 0.0-0.2: No meaningful forecast data
"""

    _ANALYSIS_RUBRIC = """\
Evaluate the quality of risk analysis and action recommendations (0.0-1.0).

## Expected elements
1. Methodology explanation: using 2025 same-month data, seasonal pattern, safety line at 5.0M
2. Risk month identification: March through June below safety line
3. Minimum point: April 2026 at ~4.1M, gap ~0.9M
4. Action recommendations aligned with risk timeline:
   - Secure short-term credit/bridge financing before March
   - Accelerate accounts receivable collection in Feb-April

## Scoring tiers
- 0.9-1.0: Clear methodology; all risk months identified; specific timeline-aligned actions
- 0.7-0.8: Risk months identified; recommendations present but less specific
- 0.5-0.6: Some risk identification; generic recommendations
- 0.3-0.4: Minimal risk analysis
- 0.0-0.2: No meaningful analysis
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
        normalized = final_text.replace(",", "").replace(",", "")

        # 1. No tool gate (pure attachment analysis)

        # 2. Deterministic: key numbers
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
            analysis_score = self._fb_analysis(final_text)

        # 4. Combine: deterministic (30%) + judge data (40%) + judge analysis (30%)
        completion = (
            0.30 * det_score
            + 0.40 * data_score
            + 0.30 * analysis_score
        )

        scores.completion = round(min(completion, 1.0), 2)
        scores.robustness = 1.0
        scores.communication = self._score_communication(final_text)
        scores.efficiency_turns = len([m for m in messages if m.message.role == "assistant"])
        return scores

    def _has_number(self, text: str, low: float, high: float) -> bool:
        for match in re.finditer(r"-?\d+(?:\.\d+)?", text):
            value = float(match.group(0))
            if low <= value <= high:
                return True
        return False

    def _month_window(self, text: str, labels: list[str]) -> str:
        lines = text.splitlines()
        for line in lines:
            lower = line.lower()
            if "|" in line and any(label in lower for label in labels):
                return lower
        for idx, line in enumerate(lines):
            lower = line.lower()
            if any(label in lower for label in labels):
                start = max(0, idx - 1)
                end = min(len(lines), idx + 4)
                return " ".join(lines[start:end]).lower()
        return text.lower()

    def _score_deterministic(self, normalized: str, text: str) -> float:
        score = 0.0
        lower = text.lower()

        # Starting balance 7.4 and safety line 5.0 mentioned
        if self._has_number(normalized, 7.3, 7.5):
            score += 0.15
        if self._has_number(normalized, 4.9, 5.1) and any(
            kw in text or kw in lower
            for kw in ["safety line", "safety threshold", "安全线"]
        ):
            score += 0.15

        # Monthly balances in correct ranges (check at least 4 of 6)
        expected = {
            "2026-01": (6.25, 6.55),
            "2026-02": (5.35, 5.65),
            "2026-03": (4.55, 4.85),
            "2026-04": (3.95, 4.25),
            "2026-05": (4.05, 4.35),
            "2026-06": (4.35, 4.65),
        }
        months_ok = 0
        for month, (low, high) in expected.items():
            window = self._month_window(normalized, self.MONTH_WINDOWS[month])
            if self._has_number(window, low, high):
                months_ok += 1
        score += 0.40 * min(months_ok / 4, 1.0)

        # Minimum point ~4.1 and gap ~0.9 mentioned
        if self._has_number(normalized, 3.95, 4.25) and any(
            kw in text or kw in lower
            for kw in ["最低", "峰值", "缺口", "2026-04", "4月",
                       "minimum", "lowest", "peak", "gap", "april", "shortfall"]
        ):
            score += 0.15
        if self._has_number(normalized, 0.85, 0.95):
            score += 0.15

        return min(score, 1.0)

    def _fb_data(self, normalized: str, text: str) -> float:
        score = 0.0
        lower = text.lower()
        if "2025" in text and any(
            kw in text or kw in lower
            for kw in ["同月", "去年同月", "同口径",
                       "same month", "same-month", "prior year", "prior-year"]
        ):
            score += 0.15
        if any(kw in lower for kw in ["季节", "season", "seasonal"]):
            score += 0.1
        # Risk months
        risk_months = 0
        for month in ["2026-03", "2026-04", "2026-05", "2026-06"]:
            labels = [m.lower() for m in self.MONTH_WINDOWS[month]]
            if any(token in lower for token in labels):
                risk_months += 1
        if any(kw in lower for kw in ["风险", "低于安全线", "risk", "below safety", "below threshold"]) and risk_months >= 2:
            score += 0.3
        # Has forecast table
        if "|" in text and "---" in text:
            score += 0.2
        # Has sections
        if any(kw in text or kw in lower for kw in ["执行摘要", "executive summary"]):
            score += 0.1
        return min(score, 1.0)

    def _fb_analysis(self, text: str) -> float:
        score = 0.0
        lower = text.lower()
        if any(kw in text or kw in lower
               for kw in ["短期融资", "过桥", "授信", "信用额度",
                          "short-term financing", "bridge financing", "credit facility",
                          "credit line", "bridge loan"]):
            score += 0.3
        if any(kw in text or kw in lower
               for kw in ["应收账款", "回款", "催收",
                          "accounts receivable", "receivable", "collection"]):
            score += 0.25
        if any(kw in text or kw in lower
               for kw in ["3月前", "2-4月", "二季度前", "4月前",
                          "before march", "feb-april", "february-april",
                          "before q2", "before april"]):
            score += 0.15
        if any(kw in lower for kw in ["风险", "risk"]) and any(
            kw in lower for kw in ["建议", "recommend", "action", "suggest"]
        ):
            score += 0.15
        if any(kw in text or kw in lower
               for kw in ["资金缺口", "funding gap", "cash shortfall"]):
            score += 0.15
        return min(score, 1.0)

    def _score_communication(self, text: str) -> float:
        lower = text.lower()
        has_table = "|" in text and "---" in text
        section_hits = sum(
            1 for kw in ["执行摘要", "预测表", "风险", "建议", "资金缺口", "最低现金",
                         "executive summary", "forecast table", "risk", "recommend",
                         "funding gap", "minimum cash", "cash shortfall", "action plan"]
            if kw in text or kw in lower
        )
        format_score = 0.0
        if has_table:
            format_score += 0.35
        format_score += 0.35 * min(section_hits / 4, 1.0)
        entities = ["2026-03", "2026-04", "7.4", "5.0", "4.1", "0.9", "应收账款", "receivable"]
        return self.compute_communication_substance(text, entities, min(format_score, 1.0))
