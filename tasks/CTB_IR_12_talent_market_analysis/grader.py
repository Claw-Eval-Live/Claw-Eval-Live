"""CTB_IR_12 grader -- talent market analysis.

Ground truth source: fixtures/web/search_results.json

v2.2: Claw-Eval mode (research report).
- Deterministic 30%: tool gate, sub-domain coverage, city coverage, key numbers
- Judge 70%: supply/demand data, salary data, trend analysis quality
- Fallback: keyword-based scoring when judge unavailable

Ground truth (from web fixtures):
  Talent gap: 1.5 million
  Sub-domains: LLM, multimodal, embodied intelligence (25% salary growth), AI safety
  Cities: Beijing 32.5%, Shanghai, Shenzhen, Hangzhou
  New first-tier cities: 45% talent inflow (Chengdu, Wuhan)
  Trends: inference deployment, cross-functional, AI safety, remote work
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class Grader(AbstractGrader):
    """Grade AI talent market analysis report."""

    _SUPPLY_DEMAND_RUBRIC = """\
Evaluate the accuracy of AI talent supply/demand and salary data (0.0-1.0).

## Ground Truth
- Overall talent gap: 1.5 million people
- Sub-domain salaries: large language models (LLM), multimodal AI, embodied intelligence, AI safety
- Embodied intelligence salary growth: 25%
- Beijing has 32.5% of AI talent
- New first-tier cities (Chengdu, Wuhan) showing 45% talent inflow growth

## Scoring tiers
- 0.9-1.0: Talent gap correct; all sub-domains with salary data; city distribution accurate
- 0.7-0.8: Most data correct; key numbers present
- 0.5-0.6: Partial data; some numbers
- 0.3-0.4: Minimal data
- 0.0-0.2: No meaningful data
"""

    _TREND_RUBRIC = """\
Evaluate the quality of hiring trends and recommendations (0.0-1.0).

## Ground Truth -- Hiring Trends
1. Inference deployment engineers in high demand
2. Cross-functional (compound) talent preferred
3. AI safety roles growing
4. Remote work becoming more accepted

## Scoring tiers
- 0.9-1.0: All 4 trends identified; actionable recruitment recommendations
- 0.7-0.8: 3+ trends; some recommendations
- 0.5-0.6: 2 trends; partial recommendations
- 0.3-0.4: 1 trend
- 0.0-0.2: No trends
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
        det_score += 0.35 * self._score_subdomain_coverage(lower)
        det_score += 0.35 * self._score_city_coverage(all_text, lower)
        det_score += 0.30 * self._score_key_numbers(all_text, lower)

        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            sd_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._SUPPLY_DEMAND_RUBRIC
            ).score
            trend_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._TREND_RUBRIC
            ).score
        else:
            sd_score = self._fallback_supply(all_text, lower)
            trend_score = self._fallback_trend(lower)

        completion = tool_penalty * (
            0.30 * det_score
            + 0.35 * sd_score
            + 0.35 * trend_score
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
        if not search:
            return 0.2
        return 1.0

    def _score_subdomain_coverage(self, lower: str) -> float:
        domains = ["large language model", "llm", "multimodal", "embodied intelligence",
                     "embodied ai", "ai safety"]
        found = sum(1 for d in domains if d in lower)
        return min(found / 3, 1.0)

    def _score_city_coverage(self, all_text: str, lower: str) -> float:
        cities = ["Beijing", "Shanghai", "Shenzhen", "Hangzhou"]
        found = sum(1 for c in cities if c in all_text or c.lower() in lower)
        return min(found / 3, 1.0)

    def _score_key_numbers(self, all_text: str, lower: str) -> float:
        score = 0.0
        if "1.5 million" in lower or self._has_bounded(all_text, "1500000"):
            score += 0.33
        if self._has_bounded(all_text, "32.5%"):
            score += 0.33
        if "25%" in all_text and any(kw in lower for kw in ["embodied", "salary"]):
            score += 0.34
        return min(score, 1.0)

    def _fallback_supply(self, all_text: str, lower: str) -> float:
        """_fallback_: dev-only."""
        score = 0.0
        if "1.5 million" in lower or "gap" in lower or "shortage" in lower:
            score += 0.25
        domains = ["llm", "multimodal", "embodied", "ai safety"]
        score += 0.30 * min(sum(1 for d in domains if d in lower) / 3, 1.0)
        cities = ["beijing", "shanghai", "shenzhen", "hangzhou"]
        score += 0.25 * min(sum(1 for c in cities if c in lower) / 3, 1.0)
        if "32.5%" in all_text:
            score += 0.20
        return min(score, 1.0)

    def _fallback_trend(self, lower: str) -> float:
        """_fallback_: dev-only."""
        score = 0.0
        trends = ["inference", "cross-functional", "ai safety", "remote work"]
        score += 0.50 * min(sum(1 for t in trends if t in lower) / 3, 1.0)
        if any(kw in lower for kw in ["recommend", "suggest", "advise"]):
            score += 0.50
        return min(score, 1.0)
