"""CTB_COMM_09 grader -- project retrospective meeting preparation.

Ground truth source: task.yaml reference_solution + fixtures.

v2.2: analysis mode (analysis/synthesis report).
- Deterministic 35%: tool gate, key data points coverage, sections
- Judge 65%: data accuracy, analysis quality
- Fallback: keyword-based scoring when judge unavailable

Ground truth data points:
  Delay: 3 weeks (3/7 -> 3/28), Budget overrun: 15% (800K -> 920K)
  Test coverage: 45% (target 80%), Customer satisfaction: 7/10
  Blocking bugs: 15, Requirement changes: 3
  API approval delay: 2 weeks, Manual deployment steps: 12
  Undocumented modules: 3
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class ProjectRetroPrepGrader(AbstractGrader):
    """Grade project retrospective meeting material preparation."""

    DATA_POINTS = {
        "delay": ["3 weeks", "three weeks", "21 days", "3-week"],
        "budget": ["15%", "920K", "920000", "920,000"],
        "test_coverage": ["45%"],
        "satisfaction": ["7/10", "7 out of 10"],
        "bugs": ["15 blocking", "15 bug"],
        "req_changes": ["3 requirement", "3 change", "three requirement"],
        "api_delay": ["2 weeks", "two weeks", "2-week"],
        "deploy_steps": ["12 step", "12 manual", "twelve step"],
        "undoc_modules": ["3 module", "3 critical module", "three module"],
    }

    # ── Judge rubrics ──────────────────────────────────────────────

    _DATA_ACCURACY_RUBRIC = """\
Evaluate the accuracy of extracted data points in the retrospective materials (0.0-1.0).

## Ground Truth Data Points
1. Schedule: 3 weeks delay (original 3/7 -> actual 3/28)
2. Budget: 15% overrun (800K planned -> 920K actual)
3. Test coverage: 45% (target was 80%)
4. Customer satisfaction: 7/10
5. Blocking bugs at launch: 15
6. Requirement changes during project: 3 items
7. API third-party approval delay: 2 weeks
8. Manual deployment steps: 12
9. Undocumented critical modules: 3

## Scoring tiers
- 0.9-1.0: All 9 data points present with correct numbers
- 0.7-0.8: 7-8 data points present and correct
- 0.5-0.6: 5-6 data points present
- 0.3-0.4: 3-4 data points present
- 0.0-0.2: Fewer than 3 data points
"""

    _ANALYSIS_QUALITY_RUBRIC = """\
Evaluate the structure and analytical depth of the retrospective materials (0.0-1.0).

## Expected sections
1. Project overview (planned vs actual: timeline, budget, personnel)
2. Delay cause analysis (sorted by impact level)
3. Technical debt list (from Zhang Wei's email: test coverage, deployment, documentation)
4. Customer satisfaction section (rating + specific feedback)
5. Improvement recommendations (synthesized from all sources)

## Scoring tiers
- 0.9-1.0: All 5 sections present; causes sorted by impact; recommendations are specific and actionable
- 0.7-0.8: 4-5 sections; reasonable analysis depth
- 0.5-0.6: 3-4 sections; surface-level analysis
- 0.3-0.4: 1-2 sections; minimal analysis
- 0.0-0.2: No meaningful structure or analysis
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
        lower = all_text.lower()

        # 1. Tool gate
        tool_penalty = self._tool_gate(dispatches)

        # 2. Deterministic checks (35%)
        det_score = 0.0
        det_score += 0.45 * self._score_data_points(lower)
        det_score += 0.30 * self._score_sections(lower)
        det_score += 0.25 * self._score_data_retrieval(dispatches)

        # 3. Judge scoring (65%)
        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            data_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._DATA_ACCURACY_RUBRIC
            ).score
            analysis_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._ANALYSIS_QUALITY_RUBRIC
            ).score
        else:
            data_score = self._fallback_data(lower)
            analysis_score = self._fallback_analysis(lower)

        # 4. Combine
        completion = tool_penalty * (
            0.35 * det_score
            + 0.35 * data_score
            + 0.30 * analysis_score
        )

        scores.completion = min(round(completion, 2), 1.0)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len(
            [m for m in messages if m.message.role == "assistant"]
        )
        return scores

    # ── Deterministic helpers ──────────────────────────────────────

    def _tool_gate(self, dispatches: list[ToolDispatch]) -> float:
        """Penalty: must read calendar, emails, AND notes."""
        cal = any(d.tool_name == "calendar_list_events" and d.response_status < 400
                  for d in dispatches)
        email = any(d.tool_name == "gmail_get_message" and d.response_status < 400
                    for d in dispatches)
        notes = any(d.tool_name == "notes_get" and d.response_status < 400
                    for d in dispatches)
        sources = sum([cal, email, notes])
        if sources == 0:
            return 0.2
        if sources == 1:
            return 0.4
        if sources == 2:
            return 0.7
        return 1.0

    def _score_data_points(self, lower: str) -> float:
        """Check coverage of the 9 key data points."""
        found = 0
        for _name, values in self.DATA_POINTS.items():
            if any(v.lower() in lower for v in values):
                found += 1
        return min(found / 7, 1.0)

    def _score_sections(self, lower: str) -> float:
        """Check for expected section types."""
        sections = [
            any(k in lower for k in ["overview", "summary", "schedule", "timeline", "delay"]),
            any(k in lower for k in ["budget", "cost", "overrun"]),
            any(k in lower for k in ["technical debt", "test coverage", "coverage"]),
            any(k in lower for k in ["satisfaction", "customer feedback", "7/10"]),
            any(k in lower for k in ["recommendation", "improvement", "action"]),
        ]
        found = sum(1 for s in sections if s)
        return min(found / 4, 1.0)

    def _score_data_retrieval(self, dispatches: list[ToolDispatch]) -> float:
        """Check that agent read emails and notes."""
        email_calls = [d for d in dispatches
                       if d.tool_name == "gmail_get_message" and d.response_status < 400]
        read_ids = {str(d.request_body.get("message_id")) for d in email_calls}
        email_score = min(len(read_ids & {"msg_1501", "msg_1502", "msg_1503"}) / 3, 1.0)

        note_calls = [d for d in dispatches
                      if d.tool_name == "notes_get" and d.response_status < 400]
        note_ids = {str(d.request_body.get("note_id")) for d in note_calls}
        note_score = min(len(note_ids & {"note_801", "note_802"}) / 2, 1.0)

        return 0.50 * email_score + 0.50 * note_score

    # ── Fallback scorers ───────────────────────────────────────────

    def _fallback_data(self, lower: str) -> float:
        """_fallback_: dev-only keyword scoring for data accuracy."""
        score = 0.0
        if "3 week" in lower or "three week" in lower:
            score += 0.12
        if "15%" in lower:
            score += 0.12
        if "45%" in lower:
            score += 0.10
        if "7/10" in lower:
            score += 0.10
        if "15 block" in lower or "15 bug" in lower:
            score += 0.10
        if "2 week" in lower and "api" in lower:
            score += 0.10
        if "12 step" in lower or "12 manual" in lower:
            score += 0.10
        if "3 module" in lower:
            score += 0.10
        return min(score, 1.0)

    def _fallback_analysis(self, lower: str) -> float:
        """_fallback_: dev-only keyword scoring for analysis quality."""
        score = 0.0
        sections = ["overview", "delay", "technical debt", "satisfaction", "recommendation"]
        score += 0.40 * min(sum(1 for s in sections if s in lower) / 3, 1.0)
        if any(k in lower for k in ["impact", "root cause", "cause"]):
            score += 0.20
        if any(k in lower for k in ["action", "improvement", "next"]):
            score += 0.20
        if len(lower) >= 500:
            score += 0.15
        return min(score, 1.0)
