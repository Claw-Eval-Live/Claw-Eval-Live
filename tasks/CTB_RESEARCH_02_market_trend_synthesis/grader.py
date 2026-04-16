"""CTB_RESEARCH_02 grader -- AI market trend synthesis report.

Ground truth source: task.yaml + fixtures/web/pages.json + fixtures/gmail/inbox.json
Rubric ground truth derived from fixture data.

v2.2: Claw-Eval mode (research report).
- Deterministic 35%: tool gate, market size numbers, trend keywords, ROI data
- Judge 65%: market data accuracy, trend analysis quality, strategic insight
- Fallback: keyword-based scoring when judge unavailable

Ground truth (from mock fixtures):
  Market size: Global AI $3200B (4250B yuan), China $480B (3200B yuan)
  Growth: 33.6%
  Key trends: LLM/large models, AI Agent, multimodal, SaaS+AI, edge AI
  ROI: average 3.5x, financial sector 5.2x
  Financial industry AI adoption: 72%
  Talent gap: 750,000
  Customer demands: smart customer service, automated reports, lead scoring
  Sources: iResearch, Gartner, McKinsey, IDC
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class Grader(AbstractGrader):
    """Grade an AI market trend synthesis report."""

    # ── Judge rubrics ──────────────────────────────────────────────

    _MARKET_DATA_RUBRIC = """\
Evaluate the accuracy of AI market data and industry statistics (0.0-1.0).

## Ground Truth
- Global AI market: ~$3,200 billion (or ~4,250 billion yuan)
- China AI market: ~$480 billion (or ~3,200 billion yuan)
- YoY growth rate: 33.6%
- Financial industry AI adoption rate: 72%
- AI talent gap: 750,000

## Scoring tiers
- 0.9-1.0: Market size for both global and China correct; growth rate correct; adoption and talent data present
- 0.7-0.8: Most market figures correct; growth rate present
- 0.5-0.6: Partial market data; some correct numbers
- 0.3-0.4: Minimal market data
- 0.0-0.2: No meaningful market data
"""

    _TREND_ANALYSIS_RUBRIC = """\
Evaluate the quality of technology trend analysis and ROI data (0.0-1.0).

## Ground Truth -- Key Trends
- Large language models (LLM) / foundation models
- AI Agent / autonomous agents
- Multimodal AI
- SaaS+AI integration
- Edge AI

## Ground Truth -- ROI
- Average AI ROI: 3.5x
- Financial sector ROI: 5.2x

## Ground Truth -- Customer Demands
- Smart customer service / intelligent chatbot
- Automated reporting
- Lead scoring / sales scoring

## Scoring tiers
- 0.9-1.0: 3+ key trends identified with explanation; ROI data correct; customer demands covered
- 0.7-0.8: 2-3 trends; some ROI data; customer needs mentioned
- 0.5-0.6: 1-2 trends; partial ROI or demand data
- 0.3-0.4: Minimal trend or ROI data
- 0.0-0.2: No meaningful trend analysis
"""

    _STRATEGIC_RUBRIC = """\
Evaluate the quality of strategic recommendations and report structure (0.0-1.0).

## Expected elements
1. Market size and growth data section
2. Three key technology trends section
3. AI investment ROI analysis section
4. Product recommendations based on customer needs section
5. Talent and competitive landscape analysis
6. References to multiple research sources (iResearch, Gartner, McKinsey, IDC)
7. Clear structure with sections, tables or organized data

## Scoring tiers
- 0.9-1.0: All sections present; well-structured; actionable recommendations; multiple sources cited
- 0.7-0.8: Most sections present; reasonable structure; some recommendations
- 0.5-0.6: Has 3+ sections but missing key analysis; basic structure
- 0.3-0.4: Incomplete sections; no clear structure
- 0.0-0.2: No meaningful report structure
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
        final_text = self._get_final_assistant_text(messages)
        all_text = self._get_all_assistant_text(messages)
        clean = all_text.replace(",", "").replace("\uff0c", "")

        # 1. Tool gate
        tool_penalty = self._tool_gate(dispatches)

        # 2. Deterministic checks (35%)
        det_score = 0.0
        det_score += 0.30 * self._score_market_numbers(clean, all_text)
        det_score += 0.25 * self._score_trend_keywords(all_text)
        det_score += 0.25 * self._score_roi_data(clean, all_text)
        det_score += 0.20 * self._score_source_coverage(all_text)

        # 3. Judge scoring (65%)
        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            market_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._MARKET_DATA_RUBRIC
            ).score
            trend_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._TREND_ANALYSIS_RUBRIC
            ).score
            strategic_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._STRATEGIC_RUBRIC
            ).score
        else:
            market_score = self._fallback_market(clean, all_text)
            trend_score = self._fallback_trends(clean, all_text)
            strategic_score = self._fallback_strategic(all_text)

        # 4. Combine
        completion = tool_penalty * (
            0.35 * det_score
            + 0.25 * market_score
            + 0.20 * trend_score
            + 0.20 * strategic_score
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
        """Penalty multiplier: agent must use both web and gmail."""
        web_calls = [d for d in dispatches
                     if d.tool_name in ("web_search", "web_get_page")
                     and d.response_status < 400]
        gmail_calls = [d for d in dispatches
                       if d.tool_name in ("gmail_list_messages", "gmail_get_message")
                       and d.response_status < 400]
        if not web_calls and not gmail_calls:
            return 0.2
        if not web_calls or not gmail_calls:
            return 0.5
        return 1.0

    def _score_market_numbers(self, clean: str, all_text: str) -> float:
        """Key verifiable market numbers."""
        checks = [
            self._has_bounded(clean, "4250") or self._has_bounded(clean, "3200"),
            self._has_bounded(clean, "480") or self._has_bounded(clean, "48"),
            self._has_bounded(all_text, "33.6%") or self._has_bounded(clean, "33.6"),
            self._has_bounded(all_text, "72%") or self._has_bounded(clean, "72"),
        ]
        return sum(checks) / len(checks)

    def _score_trend_keywords(self, all_text: str) -> float:
        """Check for key AI trend terms."""
        trends_en = ["LLM", "AI Agent", "multimodal", "SaaS", "edge AI"]
        trends_cn = ["\u5927\u6a21\u578b", "AI Agent", "\u591a\u6a21\u6001",
                     "SaaS", "\u8fb9\u7f18AI"]
        found = 0
        for en, cn in zip(trends_en, trends_cn):
            if en.lower() in all_text.lower() or cn in all_text:
                found += 1
        return min(found / 3, 1.0)

    def _score_roi_data(self, clean: str, all_text: str) -> float:
        """Check ROI figures."""
        score = 0.0
        if self._has_bounded(clean, "3.5") and re.search(r'ROI|return|investment', all_text, re.IGNORECASE):
            score += 0.5
        if self._has_bounded(clean, "5.2") and re.search(r'financ|金融', all_text, re.IGNORECASE):
            score += 0.5
        return min(score, 1.0)

    def _score_source_coverage(self, all_text: str) -> float:
        """Check that multiple research sources are referenced."""
        sources_en = ["iResearch", "Gartner", "McKinsey", "IDC"]
        sources_cn = ["\u827e\u745e", "Gartner", "\u9ea6\u80af\u9521", "IDC"]
        found = 0
        for en, cn in zip(sources_en, sources_cn):
            if en in all_text or cn in all_text:
                found += 1
        return min(found / 2, 1.0)

    # ── Fallback scorers ───────────────────────────────────────────

    def _fallback_market(self, clean: str, all_text: str) -> float:
        """_fallback_: dev-only keyword scoring for market data."""
        score = 0.0
        if self._has_bounded(clean, "4250") or self._has_bounded(clean, "3200"):
            score += 0.20
        if self._has_bounded(all_text, "33.6%"):
            score += 0.15
        if self._has_bounded(all_text, "72%"):
            score += 0.15
        if self._has_bounded(clean, "750000") or "75\u4e07" in all_text or "750,000" in all_text:
            score += 0.15
        if self._has_bounded(clean, "480") or self._has_bounded(clean, "48"):
            score += 0.15
        sources = ["iResearch", "\u827e\u745e", "Gartner", "McKinsey", "\u9ea6\u80af\u9521", "IDC"]
        score += 0.20 * min(sum(1 for s in sources if s in all_text) / 2, 1.0)
        return min(score, 1.0)

    def _fallback_trends(self, clean: str, all_text: str) -> float:
        """_fallback_: dev-only keyword scoring for trends."""
        score = 0.0
        trends = ["\u5927\u6a21\u578b", "LLM", "AI Agent", "\u591a\u6a21\u6001",
                  "multimodal", "SaaS", "\u8fb9\u7f18", "edge AI"]
        score += 0.30 * min(sum(1 for t in trends if t in all_text) / 3, 1.0)
        if self._has_bounded(clean, "3.5"):
            score += 0.20
        if self._has_bounded(clean, "5.2"):
            score += 0.20
        demand_kw = ["\u667a\u80fd\u5ba2\u670d", "\u81ea\u52a8\u62a5\u8868",
                     "\u9500\u552e\u8bc4\u5206", "customer service",
                     "automated report", "lead scoring"]
        score += 0.30 * min(sum(1 for k in demand_kw if k in all_text) / 2, 1.0)
        return min(score, 1.0)

    def _fallback_strategic(self, all_text: str) -> float:
        """_fallback_: dev-only keyword scoring for strategic quality."""
        score = 0.0
        sections = ["\u5e02\u573a\u89c4\u6a21", "\u8d8b\u52bf", "ROI",
                    "\u5efa\u8bae", "\u4eba\u624d",
                    "market size", "trend", "recommend", "talent"]
        score += 0.40 * min(sum(1 for s in sections if s.lower() in all_text.lower()) / 3, 1.0)
        if "|" in all_text and "---" in all_text:
            score += 0.25
        if any(k in all_text for k in ["\u603b\u7ed3", "\u5206\u6790",
                                        "summary", "analysis", "conclusion"]):
            score += 0.20
        if any(k in all_text for k in ["\u4ea7\u54c1\u65b9\u5411", "\u4f18\u5148",
                                        "product direction", "prioritize"]):
            score += 0.15
        return min(score, 1.0)
