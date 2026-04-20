"""CTB_COMM_25 grader -- stakeholder update compilation from 5 departments.

Ground truth source: task.yaml reference_solution + fixtures.

v2.2: analysis mode (synthesis report).
- Deterministic 35%: tool gate, department data coverage, key metrics
- Judge 65%: data accuracy, synthesis quality
- Fallback: keyword-based scoring when judge unavailable

Ground truth (5 departments):
  Marketing: CAC $6.50, 18000 acquired, conversion 3.2%
  Technology: uptime 99.9%, response 80ms, DB sharding incomplete
  Product: 12 new features, satisfaction 4.3, DAU +15%
  Customer Success: renewal 92%, NPS 42, 1 churned, 3 new enterprise
  Finance: revenue $4M (+22%), gross margin 68%, cash $7.5M, AR days 52
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class Grader(AbstractGrader):
    """Grade stakeholder update compilation report."""

    @staticmethod
    def _has_bounded(text: str, num: str) -> bool:
        return bool(re.search(rf'(?<!\d){re.escape(num)}(?!\d)', text))

    # ── Judge rubrics ──────────────────────────────────────────────

    _DATA_ACCURACY_RUBRIC = """\
Evaluate the accuracy of extracted data from all 5 department updates (0.0-1.0).

## Ground Truth
1. Marketing: Customer acquisition cost $6.50/person, 18,000 new customers acquired, conversion rate 3.2%
2. Technology: System uptime 99.9%, average response time 80ms, database sharding project not completed
3. Product: 12 new features launched, satisfaction score 4.3/5, DAU increased 15%
4. Customer Success: Renewal rate 92%, NPS score 42, 1 customer churned, 3 new enterprise accounts
5. Finance: Revenue $4M (YoY +22%), gross margin 68%, cash position $7.5M, accounts receivable days 52

## Scoring tiers
- 0.9-1.0: All 5 departments covered with correct numbers
- 0.7-0.8: 4-5 departments covered; most numbers correct
- 0.5-0.6: 3-4 departments; some numbers correct
- 0.3-0.4: 2-3 departments; few correct numbers
- 0.0-0.2: Fewer than 2 departments
"""

    _SYNTHESIS_RUBRIC = """\
Evaluate the quality of the compiled progress summary report (0.0-1.0).

## Expected elements
1. All 5 departments covered in a unified format
2. Key metrics highlighted with context (not just raw numbers)
3. Cross-department insights or patterns identified
4. Structured format suitable for executive review

## Scoring tiers
- 0.9-1.0: All departments unified; insightful cross-department analysis; executive-ready format
- 0.7-0.8: All departments present; reasonable structure; some analysis
- 0.5-0.6: Most departments; basic structure
- 0.3-0.4: Partial coverage; poor structure
- 0.0-0.2: No meaningful compilation
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

        # 1. Tool gate
        tool_penalty = self._tool_gate(dispatches)

        # 2. Deterministic checks (35%)
        det_score = 0.0
        det_score += 0.25 * self._score_department_coverage(all_text)
        det_score += 0.45 * self._score_key_metrics(clean, all_text)
        det_score += 0.30 * self._score_data_retrieval(dispatches)

        # 3. Judge scoring (65%)
        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            data_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._DATA_ACCURACY_RUBRIC
            ).score
            synthesis_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._SYNTHESIS_RUBRIC
            ).score
        else:
            data_score = self._fallback_data(clean, all_text)
            synthesis_score = self._fallback_synthesis(all_text)

        # 4. Combine
        completion = tool_penalty * (
            0.35 * det_score
            + 0.35 * data_score
            + 0.30 * synthesis_score
        )

        scores.completion = min(round(completion, 2), 1.0)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len(
            [m for m in messages if m.message.role == "assistant"]
        )
        return scores

    # ── Deterministic helpers ──────────────────────────────────────

    def _tool_gate(self, dispatches: list[ToolDispatch]) -> float:
        list_calls = [d for d in dispatches
                      if d.tool_name == "gmail_list_messages" and d.response_status < 400]
        get_calls = [d for d in dispatches
                     if d.tool_name == "gmail_get_message" and d.response_status < 400]
        if not list_calls and not get_calls:
            return 0.2
        if len(get_calls) < 3:
            return 0.5
        return 1.0

    def _score_department_coverage(self, all_text: str) -> float:
        """Check how many of 5 departments are mentioned."""
        lower = all_text.lower()
        depts = ["marketing", "technology", "product", "customer success", "finance"]
        found = sum(1 for d in depts if d in lower)
        return min(found / 4, 1.0)

    def _score_key_metrics(self, clean: str, all_text: str) -> float:
        """Check for key verifiable numbers across departments."""
        checks = [
            self._has_bounded(clean, "6.50") or self._has_bounded(clean, "6.5"),
            self._has_bounded(clean, "18000") or "18,000" in all_text,
            self._has_bounded(all_text, "3.2%"),
            self._has_bounded(all_text, "99.9%"),
            "80ms" in all_text or self._has_bounded(clean, "80") and "ms" in all_text,
            self._has_bounded(all_text, "4.3"),
            self._has_bounded(all_text, "15%"),
            self._has_bounded(all_text, "92%"),
            self._has_bounded(clean, "42") and "NPS" in all_text.upper(),
            self._has_bounded(all_text, "22%"),
            self._has_bounded(all_text, "68%"),
        ]
        found = sum(1 for c in checks if c)
        return min(found / 7, 1.0)

    def _score_data_retrieval(self, dispatches: list[ToolDispatch]) -> float:
        list_calls = [d for d in dispatches
                      if d.tool_name == "gmail_list_messages" and d.response_status < 400]
        get_calls = [d for d in dispatches
                     if d.tool_name == "gmail_get_message" and d.response_status < 400]
        score = 0.20 * (1.0 if list_calls else 0.0)
        score += 0.80 * min(len(get_calls) / 5, 1.0)
        return min(score, 1.0)

    # ── Fallback scorers ───────────────────────────────────────────

    def _fallback_data(self, clean: str, all_text: str) -> float:
        """_fallback_: dev-only keyword scoring for data accuracy."""
        score = 0.0
        if self._has_bounded(clean, "6.50") or self._has_bounded(clean, "6.5"):
            score += 0.08
        if self._has_bounded(all_text, "99.9%"):
            score += 0.08
        if self._has_bounded(all_text, "92%"):
            score += 0.08
        if self._has_bounded(all_text, "22%"):
            score += 0.08
        if self._has_bounded(all_text, "68%"):
            score += 0.08
        lower = all_text.lower()
        if "sharding" in lower or "database" in lower:
            score += 0.08
        if "12" in clean and any(k in lower for k in ["feature", "new"]):
            score += 0.06
        if "NPS" in all_text and "42" in clean:
            score += 0.06
        return min(score, 1.0)

    def _fallback_synthesis(self, all_text: str) -> float:
        """_fallback_: dev-only keyword scoring for synthesis quality."""
        score = 0.0
        if len(all_text.strip()) >= 400:
            score += 0.25
        lower = all_text.lower()
        depts = ["marketing", "technology", "product", "customer success", "finance"]
        score += 0.40 * min(sum(1 for d in depts if d in lower) / 4, 1.0)
        if any(k in lower for k in ["summary", "overview", "highlight", "progress"]):
            score += 0.20
        return min(score, 1.0)
