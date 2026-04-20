"""CTB_D01 grader -- merge three documents into a structured handoff report.

Ground truth source: task.yaml reference_solution + fixtures.

v2.2: analysis mode (document synthesis).
- Deterministic 35%: file written, scope coverage, milestone coverage
- Judge 65%: content accuracy + merge quality, report structure
- Fallback: keyword-based scoring when judge unavailable

Ground truth:
  Project: North Star Store Analytics, 120 stores pilot
  Scope: dashboard, anomaly alerting, weekly operations summary
  Stakeholders: Grace Lin, Kevin Wu, Ada Chen, Leo Zhang, procurement
  Blockers: security questionnaire TBD, KPI definitions pending
  Milestones: 2026-03-26, 2026-03-28, 2026-04-07 (latest kickoff)
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class MultiDocMergeGrader(AbstractGrader):
    """Grade a merged cross-document handoff report."""

    REPORT_CMD = "cmd:test -f /workspace/project_handover_report.md && cat /workspace/project_handover_report.md || echo MISSING"

    # ── Judge rubrics ──────────────────────────────────────────────

    _CONTENT_RUBRIC = """\
Evaluate the accuracy and completeness of the merged report content (0.0-1.0).

## Ground Truth
- Project: "North Star Store Analytics Project", piloting across 120 stores
- Scope: store operations dashboard, anomaly alerting, weekly operations summary
- Key stakeholders: Grace Lin, Kevin Wu, Ada Chen, Leo Zhang, procurement team
- Blockers: security questionnaire owner TBD, KPI definitions pending Kevin Wu
- Latest kickoff: 2026-04-07 (NOT 2026-04-01 from earlier docs -- action tracker is authoritative)
- Key milestones: 2026-03-26, 2026-03-28, 2026-04-07

## Scoring tiers
- 0.9-1.0: All scope items, stakeholders, blockers, and milestones correct; kickoff date is 04-07
- 0.7-0.8: Most content correct; kickoff date correct; minor omissions
- 0.5-0.6: Partial content; some key items missing
- 0.3-0.4: Minimal content accuracy
- 0.0-0.2: No meaningful content
"""

    _STRUCTURE_RUBRIC = """\
Evaluate the report structure and suitability for a new team member (0.0-1.0).

## Expected structure (4 sections minimum)
1. Project Goals and Scope
2. Key Stakeholders and Their Requirements
3. Current Blockers / Open Items
4. Key Milestones for the Next Two Weeks

## Quality criteria
- Organized by topic, NOT by original document order
- Action tracker data takes priority over older documents when conflicts exist
- Clear, readable structure with no duplication or filler
- Suitable for a new team member taking over

## Scoring tiers
- 0.9-1.0: All 4 sections; organized by topic; conflicts resolved (04-07 not 04-01); clean prose
- 0.7-0.8: All 4 sections; mostly organized; minor issues
- 0.5-0.6: 3 sections; some organization
- 0.3-0.4: 2 sections; poor organization
- 0.0-0.2: No meaningful structure
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
        report_text = self._get_report_text(env_snapshot)
        scoring_text = report_text or all_text

        # 1. Deterministic checks (35%)
        det_score = 0.0
        det_score += 0.30 * self._score_scope(scoring_text)
        det_score += 0.25 * self._score_stakeholders(scoring_text)
        det_score += 0.25 * self._score_blockers(scoring_text)
        det_score += 0.20 * self._score_milestones(scoring_text)

        # 2. Judge scoring (65%)
        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            content_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._CONTENT_RUBRIC
            ).score
            structure_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._STRUCTURE_RUBRIC
            ).score
        else:
            content_score = self._fallback_content(scoring_text)
            structure_score = self._fallback_structure(scoring_text)

        # 3. Combine (no tool gate -- attachment task, no tools needed)
        completion = (
            0.35 * det_score
            + 0.35 * content_score
            + 0.30 * structure_score
        )

        scores.completion = min(round(completion, 2), 1.0)
        scores.robustness = 1.0
        scores.efficiency_turns = len(
            [m for m in messages if m.message.role == "assistant"]
        )
        return scores

    # ── Deterministic helpers ──────────────────────────────────────

    def _get_report_text(self, env_snapshot: dict | None) -> str:
        if not env_snapshot:
            return ""
        entry = env_snapshot.get(self.REPORT_CMD, {})
        stdout = entry.get("stdout", "")
        if "MISSING" in stdout:
            return ""
        return stdout.strip()

    @staticmethod
    def _any_in(variants: list[str], text: str) -> bool:
        lowered = text.lower()
        return any(v in text or v.lower() in lowered for v in variants)

    def _score_scope(self, text: str) -> float:
        entities = [
            ["North Star Store Analytics", "North Star"],
            ["120 stores", "120"],
            ["store operations dashboard", "dashboard"],
            ["anomaly alert"],
            ["weekly operations summary", "weekly summary"],
        ]
        found = sum(1 for e in entities if self._any_in(e, text))
        return min(found / 4, 1.0)

    def _score_stakeholders(self, text: str) -> float:
        names = ["Grace Lin", "Kevin Wu", "Ada Chen", "Leo Zhang", "procurement"]
        found = sum(1 for n in names if n.lower() in text.lower())
        return min(found / 3, 1.0)

    def _score_blockers(self, text: str) -> float:
        blockers = ["security questionnaire", "TBD", "KPI", "read-only", "sample data"]
        found = sum(1 for b in blockers if b.lower() in text.lower())
        score = min(found / 3, 1.0)
        if "2026-04-07" in text:
            score = min(score + 0.15, 1.0)
        return score

    def _score_milestones(self, text: str) -> float:
        milestones = ["2026-03-26", "2026-03-28", "2026-04-07"]
        found = sum(1 for m in milestones if m in text)
        return found / len(milestones)

    # ── Fallback scorers ───────────────────────────────────────────

    def _fallback_content(self, text: str) -> float:
        """_fallback_: dev-only keyword scoring for content accuracy."""
        score = 0.0
        lower = text.lower()
        if "north star" in lower:
            score += 0.12
        if "120" in text:
            score += 0.08
        if "grace lin" in lower:
            score += 0.10
        if "kevin wu" in lower:
            score += 0.10
        if "security questionnaire" in lower:
            score += 0.10
        if "2026-04-07" in text:
            score += 0.15
        if "tbd" in lower:
            score += 0.08
        if "kpi" in lower:
            score += 0.08
        return min(score, 1.0)

    def _fallback_structure(self, text: str) -> float:
        """_fallback_: dev-only keyword scoring for structure."""
        score = 0.0
        lower = text.lower()
        sections = ["goal", "scope", "stakeholder", "blocker", "open item", "milestone"]
        score += 0.50 * min(sum(1 for s in sections if s in lower) / 3, 1.0)
        if len(text.strip()) >= 400:
            score += 0.20
        return min(score, 1.0)
