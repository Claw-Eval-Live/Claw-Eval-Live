"""CTB_SALES_09 grader -- quarterly forecast vs actual analysis.

Ground truth source: task.yaml reference_solution + fixtures.

v2.2: analysis mode (data analysis report).
- Deterministic 40%: tool gate, regional actuals, variances, totals
- Judge 60%: data accuracy, analysis quality
- Fallback: keyword-based scoring when judge unavailable

Ground truth:
  North China: forecast 1200K, actual 1080K, variance -120K (-10%)
  East China: forecast 1800K, actual 1630K, variance -170K (-9.44%)
  South China: forecast 900K, actual 630K, variance -270K (-30%)
  Southwest: forecast 600K, actual 770K, variance +170K (+28.33%)
  Northwest: forecast 300K, actual 275K, variance -25K (-8.33%)
  Total: forecast 4800K, actual 4385K, variance -415K (-8.65%)
  Best accuracy: Northwest (-8.33%), Worst: South China (-30%)
  Exceeded: Southwest; Below: North/East/South/Northwest
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class Grader(AbstractGrader):
    """Grade quarterly forecast vs actual analysis report."""

    _DATA_RUBRIC = """\
Evaluate the accuracy of regional forecast vs actual data (0.0-1.0).

## Ground Truth -- Per Region
- North China: forecast 1,200K, actual 1,080K, variance -120K, -10%
- East China: forecast 1,800K, actual 1,630K, variance -170K, -9.44%
- South China: forecast 900K, actual 630K, variance -270K, -30%
- Southwest: forecast 600K, actual 770K, variance +170K, +28.33%
- Northwest: forecast 300K, actual 275K, variance -25K, -8.33%

## Ground Truth -- Totals
- Total forecast: 4,800K, total actual: 4,385K
- Total variance: -415K, -8.65%
- Best accuracy: Northwest (-8.33%)
- Worst accuracy: South China (-30%)
- Exceeded forecast: Southwest only

## Scoring tiers
- 0.9-1.0: All 5 regions with correct forecast, actual, and variance; totals correct; rankings correct
- 0.7-0.8: 4-5 regions correct; totals mostly right
- 0.5-0.6: 3+ regions; some correct numbers
- 0.3-0.4: Partial data; significant errors
- 0.0-0.2: No meaningful data
"""

    _ANALYSIS_RUBRIC = """\
Evaluate the quality of variance analysis and recommendations (0.0-1.0).

## Expected elements
1. Structured comparison table per region
2. Identification of best/worst forecast accuracy
3. Analysis of why South China underperformed (-30%) and Southwest overperformed (+28.33%)
4. Identification of exceeded vs below-forecast regions
5. Overall team performance assessment

## Scoring tiers
- 0.9-1.0: Complete structured report; insightful analysis of outliers; actionable recommendations
- 0.7-0.8: Good structure; identifies outliers; basic analysis
- 0.5-0.6: Has per-region data but missing analysis; basic structure
- 0.3-0.4: Incomplete; poor structure
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
        all_text = self._get_all_assistant_text(messages)
        clean = all_text.replace(",", "").replace("\uff0c", "").replace("\uffe5", "").replace("\u00a5", "")

        # 1. Tool gate
        tool_penalty = self._tool_gate(dispatches)

        # 2. Deterministic checks (40%)
        det_score = 0.0
        det_score += 0.35 * self._score_regional_actuals(clean, all_text)
        det_score += 0.25 * self._score_variances(clean, all_text)
        det_score += 0.20 * self._score_totals(clean)
        det_score += 0.20 * self._score_rankings(all_text)

        # 3. Judge scoring (60%)
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
            data_score = self._fallback_data(clean, all_text)
            analysis_score = self._fallback_analysis(all_text)

        # 4. Combine
        completion = tool_penalty * (
            0.40 * det_score
            + 0.35 * data_score
            + 0.25 * analysis_score
        )

        scores.completion = min(round(completion, 2), 1.0)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len(
            [m for m in messages if m.message.role == "assistant"]
        )
        return scores

    # ── Deterministic helpers ──────────────────────────────────────

    @staticmethod
    def _has_bounded(text: str, num: str) -> bool:
        return bool(re.search(rf'(?<!\d){re.escape(num)}(?!\d)', text))

    def _tool_gate(self, dispatches: list[ToolDispatch]) -> float:
        crm_calls = [d for d in dispatches
                     if d.tool_name in ("crm_list_customers", "crm_get_customer")
                     and d.response_status < 400]
        fin_calls = [d for d in dispatches
                     if d.tool_name == "finance_list_transactions"
                     and d.response_status < 400]
        if not crm_calls and not fin_calls:
            return 0.2
        if not crm_calls or not fin_calls:
            return 0.5
        return 1.0

    def _score_regional_actuals(self, clean: str, all_text: str) -> float:
        """Check regional actual amounts."""
        regions = {
            "North": ["1080", "1080000"],
            "East": ["1630", "1630000"],
            "South": ["630", "630000"],
            "Southwest": ["770", "770000"],
            "Northwest": ["275", "275000"],
        }
        # Accept both English and Chinese region names
        region_cn = {
            "North": "\u534e\u5317", "East": "\u534e\u4e1c",
            "South": "\u534e\u5357", "Southwest": "\u897f\u5357",
            "Northwest": "\u897f\u5317"
        }
        found = 0
        for region, vals in regions.items():
            region_present = region in all_text or region_cn[region] in all_text
            if region_present and any(self._has_bounded(clean, v) for v in vals):
                found += 1
        return found / len(regions)

    def _score_variances(self, clean: str, all_text: str) -> float:
        """Check variance percentages."""
        checks = [
            bool(re.search(r'-10(?:\.0)?%', all_text)),
            bool(re.search(r'-9\.4', clean)),
            bool(re.search(r'-30%', all_text)),
            bool(re.search(r'\+?28\.3', clean)),
            bool(re.search(r'-8\.3', clean)),
        ]
        return sum(checks) / len(checks)

    def _score_totals(self, clean: str) -> float:
        """Check team totals."""
        checks = [
            self._has_bounded(clean, "4800") or "4800000" in clean,
            self._has_bounded(clean, "4385") or "4385000" in clean,
            bool(re.search(r'-8\.6', clean)),
        ]
        return sum(checks) / len(checks)

    def _score_rankings(self, all_text: str) -> float:
        """Check best/worst accuracy and exceeded regions."""
        lower = all_text.lower()
        score = 0.0
        # Best accuracy: Northwest
        nw_kw = ["Northwest", "\u897f\u5317"]
        best_kw = ["best", "most accurate", "smallest variance",
                   "\u51c6\u786e", "\u6700\u9ad8\u51c6\u786e", "\u504f\u5dee\u6700\u5c0f"]
        if any(k in all_text for k in nw_kw) and any(k.lower() in lower for k in best_kw):
            score += 0.30
        # Worst: South China
        sc_kw = ["South China", "\u534e\u5357"]
        worst_kw = ["worst", "least accurate", "largest variance",
                    "\u6700\u4f4e", "\u504f\u5dee\u6700\u5927", "\u6700\u4e0d\u51c6\u786e"]
        if any(k in all_text for k in sc_kw) and any(k.lower() in lower for k in worst_kw):
            score += 0.30
        # Southwest exceeded
        sw_kw = ["Southwest", "\u897f\u5357"]
        exceed_kw = ["exceeded", "over-performed", "surpassed",
                     "\u8d85\u989d", "\u8d85\u51fa\u9884\u6d4b"]
        if any(k in all_text for k in sw_kw) and any(k.lower() in lower for k in exceed_kw):
            score += 0.40
        return min(score, 1.0)

    # ── Fallback scorers ───────────────────────────────────────────

    def _fallback_data(self, clean: str, all_text: str) -> float:
        """_fallback_: dev-only keyword scoring."""
        score = 0.0
        regions = ["\u534e\u5317", "\u534e\u4e1c", "\u534e\u5357", "\u897f\u5357", "\u897f\u5317",
                   "North", "East", "South", "Southwest", "Northwest"]
        score += 0.25 * min(sum(1 for r in regions if r in all_text) / 4, 1.0)
        vals = ["1080", "1630", "630", "770", "275"]
        score += 0.35 * min(sum(1 for v in vals if self._has_bounded(clean, v)) / 3, 1.0)
        if "4385" in clean or "4800" in clean:
            score += 0.20
        if any(k.lower() in all_text.lower() for k in ["forecast", "actual", "variance"]):
            score += 0.20
        return min(score, 1.0)

    def _fallback_analysis(self, all_text: str) -> float:
        """_fallback_: dev-only keyword scoring."""
        score = 0.0
        if "|" in all_text and "---" in all_text:
            score += 0.30
        analysis_kw = ["exceed", "below", "over-perform", "under-perform",
                       "\u8d85\u989d", "\u672a\u8fbe"]
        score += 0.35 * min(sum(1 for k in analysis_kw if k.lower() in all_text.lower()) / 2, 1.0)
        if any(k.lower() in all_text.lower() for k in ["best", "worst", "recommend"]):
            score += 0.35
        return min(score, 1.0)
