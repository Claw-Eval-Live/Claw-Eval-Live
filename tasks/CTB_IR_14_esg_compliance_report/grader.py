"""CTB_IR_14 grader -- ESG compliance gap analysis report.

Ground truth source: task.yaml reference_solution is empty for this task.
Rubric ground truth derived from fixture data:   - fixtures/web/search_results.json,  - fixtures/notes/meetings.json,completion

v2.2: analysis mode (research report).
- Deterministic 30%: tool gate, key ESG numbers, gap indicators
- Judge 70%: regulatory+company data, benchmark comparison, gap analysis+roadmap
- Fallback: keyword-based scoring when judge unavailable

Ground truth (from fixtures):
  Regulatory: mandatory ESG disclosure from 2026, PUE <= 1.25, Scope 3 required
  Company: PUE 1.38, Scope 2 = 18500t, Scope 3 ~45000t, renewable 32%
  Gaps: MSCI BB vs industry BBB, female tech 18% vs 22%, no AI ethics committee
  Carbon neutral target: 2035
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class ESGComplianceGrader(AbstractGrader):
    """Grade an ESG compliance gap report."""

    # -- Judge rubrics ----------------------------------------------------

    _REGULATORY_COMPANY_RUBRIC = """\
Evaluate the accuracy of regulatory requirements and company ESG status data (0.0-1.0).

## Ground Truth -- Regulatory Requirements
- Mandatory ESG disclosure for all listed companies from 2026 annual reports (CSRC)
- Carbon accounting upgraded to ISO 14064-2026, new Scope 3 supply chain requirement
- Tech industry: data center PUE must not exceed 1.25
- New requirement: AI ethics assessment
- Penalty: up to 5 million yuan for non-compliance

## Ground Truth -- Company Current Status (from internal notes)
- Scope 1: 1,200t CO2e
- Scope 2: 18,500t CO2e (electricity)
- Scope 3: ~45,000t CO2e (not yet precisely calculated)
- Data center PUE: 1.38 (above standard 1.25)
- Renewable energy: 32%
- Female employees: 35%, female tech: 18% (below industry avg 22%)
- Training: 48h/person/year; CSR spend: 0.3% of revenue
- Independent directors: 33%
- No ESG committee; no AI ethics framework
- ISO 27001 certified; MSCI ESG rating: BB
- Carbon neutral target: 2035

## Scoring tiers
- 0.9-1.0: All regulatory items and company metrics covered with correct numbers
- 0.7-0.8: Most regulatory and company data correct; 1-2 minor details missing
- 0.5-0.6: Partial coverage of both dimensions
- 0.3-0.4: Significant gaps or inaccuracies
- 0.0-0.2: No meaningful regulatory or company ESG data
"""

    _BENCHMARK_RUBRIC = """\
Evaluate the accuracy of industry benchmark comparisons (0.0-1.0).

## Ground Truth -- Industry Benchmarks
- Tencent: carbon neutral by 2030, PUE 1.15
- Alibaba: 100% renewable, PUE 1.18
- Huawei: published AI ethics white paper, AI ethics committee
- Industry average MSCI rating: BBB (company is BB)
- Industry female tech ratio: 22% (company is 18%)

## Scoring tiers
- 0.9-1.0: All benchmark companies cited with correct metrics; company vs industry gaps quantified
- 0.7-0.8: Most benchmarks correct; key gaps identified
- 0.5-0.6: Some benchmarks present; partial gap identification
- 0.3-0.4: Minimal benchmark data
- 0.0-0.2: No meaningful benchmark comparison
"""

    _GAP_ROADMAP_RUBRIC = """\
Evaluate the quality of gap analysis and improvement roadmap (0.0-1.0).

## Expected Gap Analysis
1. PUE gap: company 1.38 vs standard 1.25 -- needs reduction
2. MSCI rating gap: company BB vs industry BBB -- needs improvement
3. Female tech ratio: 18% vs industry 22% -- diversity gap
4. AI ethics: no framework vs industry leaders have committees
5. Scope 3: not precisely calculated yet -- need to complete
6. ESG committee: not established -- governance gap

## Expected Roadmap Elements
- Short-term: establish ESG committee, start AI ethics framework, begin Scope 3 calculation
- Medium-term: PUE reduction plan, increase renewable energy beyond 32%
- Long-term: carbon neutral by 2035, MSCI BBB target

## Scoring tiers
- 0.9-1.0: All 6 gaps identified with numbers; phased roadmap with specific timelines; prioritized actions
- 0.7-0.8: Most gaps identified; has roadmap with some priorities
- 0.5-0.6: Some gaps identified; generic roadmap
- 0.3-0.4: Minimal gap analysis; vague recommendations
- 0.0-0.2: No meaningful analysis
"""

    # -- Main grading -----------------------------------------------------

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
        clean = final_text.replace(",", "").replace("\uff0c", "")

        # 1. Tool gate
        tool_penalty = self._tool_gate(dispatches)

        # 2. Deterministic checks (30%)
        det_score = 0.0
        det_score += 0.45 * self._score_key_numbers(clean, final_text)  # ESG numbers
        det_score += 0.30 * self._score_gap_indicators(final_text)      # gap keywords
        det_score += 0.25 * self._score_sections(final_text)            # 4 required sections

        # 3. Judge scoring (70%)
        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            reg_company_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._REGULATORY_COMPANY_RUBRIC
            ).score
            benchmark_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._BENCHMARK_RUBRIC
            ).score
            gap_roadmap_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._GAP_ROADMAP_RUBRIC
            ).score
        else:
            reg_company_score = self._fallback_data(clean, final_text)
            benchmark_score = self._fallback_data(clean, final_text) * 0.7
            gap_roadmap_score = self._fallback_analysis(final_text)

        # 4. Combine
        completion = tool_penalty * (
            0.30 * det_score
            + 0.25 * reg_company_score
            + 0.20 * benchmark_score
            + 0.25 * gap_roadmap_score
        )

        scores.completion = min(round(completion, 2), 1.0)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len(
            [m for m in messages if m.message.role == "assistant"]
        )
        return scores

    # -- Deterministic helpers --------------------------------------------

    @staticmethod
    def _has_bounded(text: str, num: str) -> bool:
        return bool(re.search(rf'(?<!\d){re.escape(num)}(?!\d)', text))

    def _tool_gate(self, dispatches: list[ToolDispatch]) -> float:
        """Penalty multiplier: did the agent search web AND read notes?"""
        search_calls = [d for d in dispatches
                        if d.tool_name == "web_search" and d.response_status < 400]
        notes_calls = [d for d in dispatches
                       if d.tool_name in ("notes_list", "notes_get") and d.response_status < 400]
        if not search_calls and not notes_calls:
            return 0.2
        if not search_calls or not notes_calls:
            return 0.5
        return 1.0

    def _score_key_numbers(self, clean: str, final_text: str) -> float:
        """Check for specific verifiable ESG numbers."""
        checks = [
            (self._has_bounded(final_text, "1.38") and "PUE" in final_text, 1.0),
            (self._has_bounded(final_text, "1.25") and "PUE" in final_text, 0.8),
            (self._has_bounded(clean, "18500") or "18,500" in final_text, 0.8),
            (self._has_bounded(clean, "45000") or "45,000" in final_text, 0.6),
            (self._has_bounded(final_text, "32%") and
             any(k in final_text for k in ["renewable", "energy",
                                            "\u53ef\u518d\u751f", "\u80fd\u6e90"]), 0.7),
            ("BB" in final_text and "BBB" in final_text and "MSCI" in final_text, 0.8),
            (self._has_bounded(final_text, "18%") and self._has_bounded(final_text, "22%"), 0.7),
        ]
        total_weight = sum(w for _, w in checks)
        found_weight = sum(w for hit, w in checks if hit)
        return min(found_weight / total_weight, 1.0) if total_weight > 0 else 0.0

    def _score_gap_indicators(self, final_text: str) -> float:
        """Check that key gap categories are discussed."""
        gaps = [
            "PUE" in final_text and any(
                k in final_text for k in ["exceed", "above", "gap", "non-compliant",
                                          "\u8d85\u8fc7", "\u9ad8\u4e8e", "\u5dee\u8ddd",
                                          "\u4e0d\u8fbe\u6807"]),
            "MSCI" in final_text and any(
                k in final_text for k in ["below", "gap", "BB",
                                          "\u4f4e\u4e8e", "\u5dee\u8ddd"]),
            any(k in final_text for k in ["AI ethics", "AI\u4f26\u7406"]),
            any(k in final_text for k in ["female tech", "diversity",
                                          "\u5973\u6027\u6280\u672f",
                                          "\u591a\u5143\u5316"]),
        ]
        return sum(1 for g in gaps if g) / 4.0

    def _score_sections(self, final_text: str) -> float:
        """Check that the 4 required report sections exist."""
        sections = [
            any(k in final_text for k in ["regulatory requirement", "mandatory disclosure", "CSRC",
                                          "\u76d1\u7ba1\u8981\u6c42", "\u5f3a\u5236\u62ab\u9732",
                                          "\u8bc1\u76d1\u4f1a"]),
            any(k in final_text for k in ["current performance", "status assessment", "carbon emission",
                                          "\u5f53\u524d\u8868\u73b0", "\u73b0\u72b6\u8bc4\u4f30",
                                          "\u78b3\u6392\u653e"]),
            any(k in final_text for k in ["gap analysis", "benchmarking", "benchmark",
                                          "\u5dee\u8ddd\u5206\u6790", "\u5bf9\u6807",
                                          "\u6807\u6746"]),
            any(k in final_text for k in ["roadmap", "improvement", "recommendation",
                                          "\u8def\u7ebf\u56fe", "\u6539\u8fdb",
                                          "\u5efa\u8bae"]),
        ]
        return sum(1 for s in sections if s) / 4.0

    # -- Fallback scorers -------------------------------------------------

    def _fallback_data(self, clean: str, final_text: str) -> float:
        """_fallback_: dev-only keyword scoring for ESG data accuracy."""
        score = 0.0
        if self._has_bounded(final_text, "1.38"):
            score += 0.10
        if self._has_bounded(final_text, "1.25"):
            score += 0.08
        if self._has_bounded(clean, "18500") or "18,500" in final_text:
            score += 0.08
        if self._has_bounded(clean, "45000") or "45,000" in final_text:
            score += 0.06
        if "32%" in final_text:
            score += 0.06
        if "BB" in final_text and "BBB" in final_text:
            score += 0.08
        if "18%" in final_text and "22%" in final_text:
            score += 0.08
        if any(k in final_text for k in ["mandatory disclosure", "CSRC",
                                          "\u5f3a\u5236\u62ab\u9732", "\u8bc1\u76d1\u4f1a"]):
            score += 0.08
        if any(k in final_text for k in ["AI ethics", "AI\u4f26\u7406"]):
            score += 0.06
        # Benchmarks
        benchmarks = ["Tencent", "Alibaba", "Huawei",
                       "\u817e\u8baf", "\u963f\u91cc", "\u534e\u4e3a"]
        score += 0.09 * (sum(1 for b in benchmarks if b in final_text) / 3)
        if "2035" in final_text:
            score += 0.05
        if "Scope" in final_text or "\u8303\u56f4" in final_text:
            score += 0.05
        return min(score, 1.0)

    def _fallback_analysis(self, final_text: str) -> float:
        """_fallback_: dev-only keyword scoring for gap analysis quality."""
        score = 0.0
        gap_keywords = ["gap", "deficiency", "missing", "needs improvement", "below",
                        "\u5dee\u8ddd", "\u4e0d\u8db3", "\u7f3a\u5931",
                        "\u5f85\u6539\u8fdb", "\u4f4e\u4e8e"]
        score += 0.20 * min(sum(1 for k in gap_keywords if k in final_text) / 3, 1.0)
        if any(k in final_text for k in ["roadmap", "improvement plan",
                                          "\u8def\u7ebf\u56fe", "\u6539\u8fdb\u8ba1\u5212"]):
            score += 0.20
        if any(k in final_text for k in ["short-term", "medium-term", "long-term",
                                          "\u77ed\u671f", "\u4e2d\u671f", "\u957f\u671f"]):
            score += 0.15
        if any(k in final_text for k in ["recommendation", "measure", "action",
                                          "\u5efa\u8bae", "\u63aa\u65bd", "\u884c\u52a8"]):
            score += 0.15
        if any(k in final_text for k in ["ESG committee", "dedicated committee",
                                          "ESG\u59d4\u5458\u4f1a", "\u4e13\u9879\u59d4\u5458\u4f1a"]):
            score += 0.10
        if "|" in final_text and "---" in final_text:
            score += 0.10
        if any(k in final_text for k in ["priority", "urgency", "progress",
                                          "\u4f18\u5148\u7ea7", "\u7d27\u8feb\u6027",
                                          "\u8fdb\u5ea6"]):
            score += 0.10
        return min(score, 1.0)
