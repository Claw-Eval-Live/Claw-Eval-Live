"""CTB_IR_07 grader -- vendor comparison report.

Ground truth source: fixtures/web/search_results.json

v2.2: Claw-Eval mode (research report).
- Deterministic 30%: tool gate, vendor coverage, key data points
- Judge 70%: performance/pricing data, security/compliance, recommendation quality
- Fallback: keyword-based scoring when judge unavailable

Ground truth (from web fixtures):
  Vendors: Alibaba Cloud (33.1% share), Huawei Cloud, Tencent Cloud, China Telecom Cloud
  Pricing: 398/375/389/329 per month (2C4G instances)
  SLA: Alibaba Cloud 99.995%
  Response time: Alibaba Cloud 12 min
  Security: Level 3 protection (MLPS), ISO 27701
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class Grader(AbstractGrader):
    """Grade cloud vendor comparison report."""

    _PERF_PRICING_RUBRIC = """\
Evaluate the accuracy of performance and pricing comparison data (0.0-1.0).

## Ground Truth
- Alibaba Cloud: Market share 33.1%, SLA 99.995%, 2C4G price ~398/month, response time 12 min
- Huawei Cloud: 2C4G price ~375/month
- Tencent Cloud: 2C4G price ~389/month
- China Telecom Cloud: 2C4G price ~329/month (cheapest)

## Scoring tiers
- 0.9-1.0: All 4 vendors with correct pricing; SLA and response time data present
- 0.7-0.8: 3-4 vendors; most pricing correct; some SLA data
- 0.5-0.6: 2-3 vendors; partial pricing
- 0.3-0.4: Minimal vendor data
- 0.0-0.2: No meaningful data
"""

    _SECURITY_RUBRIC = """\
Evaluate the accuracy of security compliance and support data (0.0-1.0).

## Ground Truth
- Security certifications: MLPS Level 3 (classified protection), ISO 27701
- Alibaba Cloud response time: 12 minutes (fastest)
- Overall recommendation/ranking across compute, pricing, security, and support dimensions

## Scoring tiers
- 0.9-1.0: Security certifications mentioned; support response times; clear dimensional ranking
- 0.7-0.8: Most security data; some support metrics
- 0.5-0.6: Partial security/support data
- 0.3-0.4: Minimal coverage
- 0.0-0.2: No security/support analysis
"""

    _RECOMMENDATION_RUBRIC = """\
Evaluate the quality of the recommendation and overall report structure (0.0-1.0).

## Expected elements
- Clear ranking across 4 dimensions (compute, pricing, security, support)
- Overall vendor recommendation with justification
- Comparative analysis highlighting strengths/weaknesses per vendor
- Well-structured report with tables or structured comparisons

## Scoring tiers
- 0.9-1.0: Clear multi-dimensional ranking; well-justified recommendation; excellent structure
- 0.7-0.8: Good ranking; reasonable recommendation; decent structure
- 0.5-0.6: Partial ranking; some recommendation
- 0.3-0.4: Minimal structure
- 0.0-0.2: No recommendation
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
        det_score += 0.40 * self._score_vendor_coverage(all_text, lower)
        det_score += 0.30 * self._score_key_numbers(all_text)
        det_score += 0.30 * self._score_recommendation_present(lower)

        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            perf_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._PERF_PRICING_RUBRIC
            ).score
            sec_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._SECURITY_RUBRIC
            ).score
            rec_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._RECOMMENDATION_RUBRIC
            ).score
        else:
            perf_score = self._fallback_perf(all_text, lower)
            sec_score = self._fallback_security(all_text, lower)
            rec_score = self._fallback_rec(lower)

        completion = tool_penalty * (
            0.30 * det_score
            + 0.25 * perf_score
            + 0.20 * sec_score
            + 0.25 * rec_score
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
        search = [d for d in dispatches if d.tool_name == "web_search" and d.response_status < 400]
        fetch = [d for d in dispatches if d.tool_name == "web_get_page" and d.response_status < 400]
        if not search:
            return 0.2
        if not fetch and len(search) < 2:
            return 0.5
        return 1.0

    def _score_vendor_coverage(self, all_text: str, lower: str) -> float:
        vendors = ["Alibaba Cloud", "Huawei Cloud", "Tencent Cloud", "China Telecom Cloud"]
        found = sum(1 for v in vendors if v.lower() in lower)
        return min(found / 3, 1.0)

    def _score_key_numbers(self, all_text: str) -> float:
        numbers = [
            self._has_bounded(all_text, "33.1%") or self._has_bounded(all_text, "33.1"),
            self._has_bounded(all_text, "99.995%"),
            any(p in all_text for p in ["398", "375", "389", "329"]),
        ]
        return sum(1 for n in numbers if n) / len(numbers)

    def _score_recommendation_present(self, lower: str) -> float:
        rec_kw = ["recommend", "ranking", "overall", "conclusion"]
        found = sum(1 for kw in rec_kw if kw in lower)
        return min(found / 2, 1.0)

    def _fallback_perf(self, all_text: str, lower: str) -> float:
        """_fallback_: dev-only."""
        score = 0.0
        prices = ["398", "375", "389", "329"]
        score += 0.50 * min(sum(1 for p in prices if p in all_text) / 2, 1.0)
        if "33.1%" in all_text:
            score += 0.25
        if "99.995%" in all_text:
            score += 0.25
        return min(score, 1.0)

    def _fallback_security(self, all_text: str, lower: str) -> float:
        """_fallback_: dev-only."""
        score = 0.0
        if any(kw in lower for kw in ["level 3 protection", "mlps", "classified protection", "iso 27701"]):
            score += 0.50
        if "12 min" in lower or "12-minute" in lower:
            score += 0.30
        if len(all_text.strip()) >= 300:
            score += 0.20
        return min(score, 1.0)

    def _fallback_rec(self, lower: str) -> float:
        """_fallback_: dev-only."""
        score = 0.0
        rec_kw = ["recommend", "ranking", "overall", "conclusion", "suggest"]
        score += 0.60 * min(sum(1 for kw in rec_kw if kw in lower) / 2, 1.0)
        if "table" in lower or "|" in lower:
            score += 0.40
        return min(score, 1.0)
