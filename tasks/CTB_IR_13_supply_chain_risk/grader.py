"""CTB_IR_13 grader -- supply chain risk assessment report.

Ground truth source: task.yaml reference_solution is empty for this task.
Rubric ground truth derived from fixture data:   - fixtures/web/search_results.json,  - fixtures/gmail/inbox.json,completion

v2.2: hybrid deterministic + judge scoring.
- Deterministic: tool gate (web search + email), key data points from fixtures
- Judge: risk assessment accuracy, report quality
- Fallback: keyword-based scoring when judge unavailable

Ground truth (from fixtures):
  Market: 7800 billion USD, AI chips 28%, TSMC 62%, HBM supply tight 24 weeks
  Export: 300 TOPS limit, ASML DUV suspended, GAA EDA restricted
  Email 1: H200 50 units delayed 15 weeks (Apr->Jul), Ascend 910C alternative 3 weeks
  Email 2: Risk ratings: NVIDIA extreme-high, AMD high, Ascend low, Cambricon medium
           Recommended split: Ascend 60%, NVIDIA 25%, others 15%
           Budget impact: +20M yuan
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class Grader(AbstractGrader):

    # ── judge rubrics ──

    _DATA_RUBRIC = """\
Evaluate the accuracy of the supply chain risk assessment data (0.0-1.0).

## Ground Truth
### Global Semiconductor Market
- Market size: 7,800 billion USD (7800亿美元)
- AI chip share: 28%
- TSMC advanced process share: 62%; Samsung 15%; Intel 8%
- HBM supply tight: 24-week lead time; SK Hynix + Samsung = 95%

### Export Controls
- US AI chip limit: 300 TOPS
- EDA tool restriction extended to GAA process
- HBM export requires case-by-case approval
- ASML suspended DUV lithography maintenance for China
- Japan added 23 semiconductor equipment types to export control

### H200 Delivery Delay (from supplier email)
- 50 H200 servers (PO-2026-0315) delayed from April 15 to July 30 (~15 weeks)
- Cause: HBM shortage + export approval changes
- Alternative: Huawei Ascend 910C, 3-week delivery, in stock

### Supplier Risk Ratings (from internal email)
- NVIDIA: high -> extreme-high risk (export uncertainty)
- AMD: high risk (limited alternatives)
- Huawei Ascend: medium -> low risk (stable domestic supply)
- Cambricon: medium risk (performance gap)
- Recommended procurement ratio: Ascend 60%, NVIDIA 25%, others 15%
- Budget impact: +20M yuan for full-year GPU procurement

## Scoring tiers
- 0.9-1.0: All major data points present from both web search and emails
- 0.7-0.8: Most data points; emails integrated
- 0.5-0.6: Web data present; partial email integration
- 0.3-0.4: Only surface-level data
- 0.0-0.2: No meaningful data
"""

    _REPORT_RUBRIC = """\
Evaluate the quality of the supply chain risk report (0.0-1.0).

## Expected sections
1. Global chip supply-demand situation (market size, capacity, lead times)
2. Key supplier risk ratings with justification
3. Geopolitical impact analysis (export controls, restrictions)
4. Risk mitigation measures and alternative sourcing recommendations

## Scoring tiers
- 0.9-1.0: All 4 sections; specific data; supplier ratings; budget impact; actionable recommendations
- 0.7-0.8: 3-4 sections; most data present; some recommendations
- 0.5-0.6: 2-3 sections; partial analysis
- 0.3-0.4: Minimal structure; surface-level
- 0.0-0.2: No meaningful report
"""

    def grade(self, messages, dispatches, task, audit_data=None, judge=None, media_events=None, env_snapshot=None):
        scores = DimensionScores()
        scores.safety = 1.0
        final_text = self._get_final_assistant_text(messages)
        clean = final_text.replace(",", "").replace("\uff0c", "")

        # 1. Tool gate
        tool_penalty = self._tool_gate(dispatches)

        # 2. Deterministic (30%)
        det_score = self._score_deterministic(clean, final_text)

        # 3. Judge (70%)
        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            data_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._DATA_RUBRIC
            ).score
            report_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._REPORT_RUBRIC
            ).score
        else:
            data_score = self._fallback_data(clean, final_text)
            report_score = self._fallback_report(final_text)

        # 4. Combine
        completion = tool_penalty * (
            0.30 * det_score
            + 0.35 * data_score
            + 0.35 * report_score
        )

        scores.completion = round(min(completion, 1.0), 2)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len([m for m in messages if m.message.role == "assistant"])
        return scores

    # ── helpers ──

    @staticmethod
    def _has_bounded(text: str, num: str) -> bool:
        return bool(re.search(rf'(?<!\d){re.escape(num)}(?!\d)', text))

    def _tool_gate(self, dispatches):
        web = any(d.tool_name in ("web_search", "web_get_page")
                  and d.response_status < 400 for d in dispatches)
        email = any(d.tool_name in ("gmail_list_messages", "gmail_get_message")
                    and d.response_status < 400 for d in dispatches)
        if web and email:
            return 1.0
        if web or email:
            return 0.5
        return 0.15

    def _score_deterministic(self, clean, final_text):
        """5 dimensions: market data, export controls, H200 delay, risk ratings, budget impact."""
        hits = 0
        total = 5

        # D1: Market size (7800) + TSMC share (62%)
        mkt = 0
        if self._has_bounded(clean, "7800") or "7,800" in final_text:
            mkt += 0.5
        if "\u53f0\u79ef\u7535" in final_text and "62%" in final_text:
            mkt += 0.5
        hits += mkt

        # D2: Export controls (300 TOPS or ASML DUV)
        exp = 0
        if "300 TOPS" in final_text or "300TOPS" in final_text:
            exp += 0.5
        if "ASML" in final_text and ("DUV" in final_text or "\u5149\u523b\u673a" in final_text):
            exp += 0.5
        hits += exp

        # D3: H200 delivery delay (15 weeks or July)
        if "H200" in final_text:
            if any(kw in final_text for kw in [
                "\u5ef6\u8fdf", "\u5ef6\u8fdf", "15\u5468", "7\u6708"
            ]):
                hits += 1
            else:
                hits += 0.3

        # D4: Risk ratings (Ascend low risk or NVIDIA extreme-high)
        rating = 0
        if "\u6607\u817e" in final_text:
            if any(kw in final_text for kw in ["\u4f4e\u98ce\u9669", "60%"]):
                rating += 0.5
        if any(kw in final_text for kw in ["\u6781\u9ad8\u98ce\u9669"]) and \
           any(kw in final_text for kw in ["\u82f1\u4f1f\u8fbe", "NVIDIA"]):
            rating += 0.5
        hits += rating

        # D5: Budget impact 2000万
        if "2000\u4e07" in final_text or self._has_bounded(clean, "2000"):
            hits += 1
        elif any(kw in final_text for kw in ["\u9884\u7b97\u5f71\u54cd", "\u589e\u52a0\u9884\u7b97"]):
            hits += 0.3

        return min(hits / total, 1.0)

    # ── fallbacks (dev-only) ──

    def _fallback_data(self, clean, all_text):
        score = 0.0
        if self._has_bounded(clean, "7800") or "7,800\u4ebf" in all_text:
            score += 0.1
        if "\u53f0\u79ef\u7535" in all_text and "62%" in all_text:
            score += 0.1
        if "HBM" in all_text and any(kw in all_text for kw in ["\u7d27\u5f20", "24\u5468"]):
            score += 0.1
        if "300 TOPS" in all_text or "300TOPS" in all_text:
            score += 0.1
        if "ASML" in all_text and ("DUV" in all_text or "\u5149\u523b\u673a" in all_text):
            score += 0.1
        if "H200" in all_text and any(kw in all_text for kw in ["\u5ef6\u8fdf", "15\u5468"]):
            score += 0.15
        if "\u6607\u817e" in all_text and any(kw in all_text for kw in ["\u4f4e\u98ce\u9669", "60%"]):
            score += 0.15
        if "2000\u4e07" in all_text:
            score += 0.1
        return min(score, 1.0)

    def _fallback_report(self, all_text):
        score = 0.0
        sections = [
            ["\u4f9b\u9700", "\u5e02\u573a\u89c4\u6a21", "\u4ea7\u80fd"],
            ["\u98ce\u9669\u8bc4\u7ea7", "\u98ce\u9669\u7b49\u7ea7", "\u4f9b\u5e94\u5546"],
            ["\u5730\u7f18\u653f\u6cbb", "\u51fa\u53e3\u7ba1\u5236", "\u9650\u5236"],
            ["\u66ff\u4ee3", "\u7f13\u89e3", "\u5efa\u8bae"],
        ]
        for section_kws in sections:
            if any(kw in all_text for kw in section_kws):
                score += 0.2
        if len(all_text) > 500:
            score += 0.2
        return min(score, 1.0)
