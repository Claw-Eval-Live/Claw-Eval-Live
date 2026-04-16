"""CTB_FIN_29 grader -- budget reforecast.

v2.2: hybrid deterministic + judge scoring.
- Deterministic: finance API gate, H1 totals, worst overrun, full-year reforecast
- Judge: calculation accuracy, reforecast analysis quality
- Fallback: keyword-based scoring when judge unavailable

Ground truth:
  H1 actual = 5,685,000 (sum of 8 items)
  H1 budget = 5,550,000
  Worst overrun: sales travel 140% (420K/300K)
  Marketing ads: 118.75% (950K/800K)
  Full-year reforecast ~ 11,370,000
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class Grader(AbstractGrader):

    # -- judge rubrics --

    _DATA_RUBRIC = """\
Evaluate the accuracy of budget reforecast calculations (0.0-1.0).

## Ground Truth
### H1 Actuals vs Budget
- Total H1 actual: 5,685,000 (1,150K + 420K + 2,100K + 350K + 950K + 380K + 185K + 150K)
- Total H1 budget: 5,550,000 (1,200K + 300K + 2,000K + 500K + 800K + 400K + 200K + 150K)
- Total H1 overrun: 135,000

### Key Execution Rates
- Sales travel: 140% (420K actual / 300K budget) -- worst overrun
- Marketing ads: 118.75% (950K actual / 800K budget)
- R&D personnel: 105% (2,100K / 2,000K)
- Sales personnel: 95.83% (1,150K / 1,200K)

### Departments
- 4 departments: Sales, R&D, Marketing, Admin

### Full-Year Reforecast
- H2 adjusted = H2 original budget x H1 execution rate per item
- Full-year = H1 actual + H2 adjusted ~ 11,370,000

## Scoring tiers
- 0.9-1.0: H1 totals correct; all execution rates; full-year reforecast correct; worst overrun identified
- 0.7-0.8: Most numbers correct; key rates present
- 0.5-0.6: Partial calculations
- 0.3-0.4: Only H1 totals or partial rates
- 0.0-0.2: No meaningful calculations
"""

    _ANALYSIS_RUBRIC = """\
Evaluate the quality of the reforecast analysis report (0.0-1.0).

## Expected elements
1. Per-department, per-item breakdown: H1 budget, H1 actual, variance, execution rate
2. H2 reforecast calculation (H2 budget x execution rate)
3. Full-year total (H1 actual + H2 reforecast)
4. Identification of worst overrun item with explanation
5. Recommendations for budget control

## Scoring tiers
- 0.9-1.0: Complete breakdown table; all rates; full-year total; worst item highlighted; recommendations
- 0.7-0.8: Has breakdown and rates; full-year present; some recommendations
- 0.5-0.6: Partial table; some rates
- 0.3-0.4: Minimal structure
- 0.0-0.2: No meaningful report
"""

    def grade(self, messages, dispatches, task, audit_data=None, judge=None, media_events=None, env_snapshot=None):
        scores = DimensionScores()
        scores.safety = 1.0
        final_text = self._get_final_assistant_text(messages)
        clean = final_text.replace(",", "").replace(",", "")

        # 1. Tool gate
        calls = [d for d in dispatches
                 if d.tool_name == "finance_list_transactions"
                 and d.response_status < 400]
        tool_penalty = 1.0 if calls else 0.2

        # 2. Deterministic (30%)
        det_score = self._score_deterministic(clean, final_text)

        # 3. Judge (70%)
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
            data_score = self._fallback_data(clean, final_text)
            analysis_score = self._fallback_analysis(final_text)

        # 4. Combine
        completion = tool_penalty * (
            0.30 * det_score
            + 0.35 * data_score
            + 0.35 * analysis_score
        )

        scores.completion = round(min(completion, 1.0), 2)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len([m for m in messages if m.message.role == "assistant"])
        return scores

    # -- helpers --

    @staticmethod
    def _has_bounded(text: str, num: str) -> bool:
        return bool(re.search(rf'(?<!\d){re.escape(num)}(?!\d)', text))

    def _score_deterministic(self, clean, final_text):
        """5 dimensions: H1 actual, H1 budget, worst overrun rate, full-year total, dept coverage."""
        hits = 0
        total = 5

        # D1: H1 actual total 5685000 / 5685
        if self._has_bounded(clean, "5685000") or self._has_bounded(clean, "5685"):
            hits += 1

        # D2: H1 budget total 5550000 / 5550
        if self._has_bounded(clean, "5550000") or self._has_bounded(clean, "5550"):
            hits += 1

        # D3: Worst overrun = sales travel 140%
        if re.search(r"140\s*%|140%", final_text):
            if any(kw in final_text for kw in ["travel", "Travel", "差旅"]):
                hits += 1
            else:
                hits += 0.5
        elif any(kw in final_text for kw in ["travel", "Travel", "差旅"]):
            if any(kw in final_text for kw in [
                "worst", "highest", "most severe", "最严重", "最高", "超支最"
            ]):
                hits += 0.5

        # D4: Full-year reforecast ~11370000
        if self._has_bounded(clean, "11370000") or self._has_bounded(clean, "11370"):
            hits += 1
        elif re.search(r"1137|1138|1136", clean):
            hits += 0.7

        # D5: All 4 departments mentioned
        depts_zh = ["销售部", "研发部", "市场部", "行政部"]
        depts_en = ["Sales", "R&D", "Marketing", "Admin"]
        mentioned = sum(
            1 for zh, en in zip(depts_zh, depts_en)
            if zh in final_text or en in final_text
        )
        hits += min(mentioned / 3, 1.0)

        return min(hits / total, 1.0)

    # -- fallbacks (dev-only) --

    def _fallback_data(self, clean, all_text):
        score = 0.0
        if self._has_bounded(clean, "5685000") or self._has_bounded(clean, "5685"):
            score += 0.15
        if self._has_bounded(clean, "5550000") or self._has_bounded(clean, "5550"):
            score += 0.1
        if re.search(r"140\s*%|140%", all_text):
            score += 0.15
        if re.search(r"118\.75|118\.8", all_text):
            score += 0.1
        if self._has_bounded(clean, "420000"):
            score += 0.1
        if self._has_bounded(clean, "950000"):
            score += 0.1
        depts_zh = ["销售部", "研发部", "市场部", "行政部"]
        depts_en = ["Sales", "R&D", "Marketing", "Admin"]
        mentioned = sum(
            1 for zh, en in zip(depts_zh, depts_en)
            if zh in all_text or en in all_text
        )
        score += 0.1 * (mentioned / len(depts_zh))
        if "|" in all_text and "---" in all_text:
            score += 0.2
        return min(score, 1.0)

    def _fallback_analysis(self, all_text):
        score = 0.0
        if any(kw in all_text for kw in ["execution rate", "utilization rate", "completion rate",
                                          "执行率", "使用率", "完成率"]):
            score += 0.2
        if any(kw in all_text for kw in ["travel", "Travel", "差旅"]):
            if any(kw in all_text for kw in ["worst", "highest", "most severe",
                                              "最严重", "最高", "超支最"]):
                score += 0.2
        if any(kw in all_text for kw in ["full-year", "full year", "annual", "reforecast",
                                          "全年", "年度", "重新预测"]):
            score += 0.2
        if "|" in all_text and "---" in all_text:
            score += 0.2
        if any(kw in all_text for kw in ["recommend", "control", "suggest",
                                          "建议", "控制"]):
            score += 0.2
        return min(score, 1.0)
