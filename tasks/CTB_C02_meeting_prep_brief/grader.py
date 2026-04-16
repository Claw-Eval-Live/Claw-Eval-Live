"""CTB_C02 grader -- meeting prep brief synthesis from calendar, email, notes.

Ground truth source: task.yaml reference_solution + fixtures.

v2.2: Claw-Eval mode (research/synthesis report).
- Deterministic 35%: tool gate, basic info coverage, topic coverage
- Judge 65%: content accuracy, brief structure + analysis
- Fallback: keyword-based scoring when judge unavailable

Ground truth:
  Meeting: StarCare Health Pilot Prep Meeting, 2026-03-19 15:00-16:00, Zoom
  Attendees: Jing Li, Tao Wang, Ning Zhou
  Concerns: SOC 2/compliance, HIS read-only access, April pilot timeline
  Risks: procurement materials, API boundary, de-identified sample data
  Next steps: implementation plan, owners, materials checklist
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class MeetingPrepBriefGrader(AbstractGrader):
    """Grade meeting prep brief synthesis quality."""

    # ── Judge rubrics ──────────────────────────────────────────────

    _CONTENT_RUBRIC = """\
Evaluate the accuracy of meeting information and client concerns in the brief (0.0-1.0).

## Ground Truth -- Meeting Info
- Event: StarCare Health Pilot Prep Meeting
- Date/Time: 2026-03-19, 15:00-16:00
- Format: Online (Zoom)
- Key attendees: Jing Li (StarCare), Tao Wang, Ning Zhou

## Ground Truth -- Client Concerns
- SOC 2 compliance certification is a must-have
- HIS (hospital information system) read-only access requirement
- Pilot timeline target: April
- Procurement process and materials needed
- De-identified sample data requirement

## Scoring tiers
- 0.9-1.0: Meeting details correct (date, time, attendees); all 5 client concerns accurately captured
- 0.7-0.8: Meeting details correct; 3-4 concerns captured
- 0.5-0.6: Meeting details mostly correct; 2-3 concerns
- 0.3-0.4: Partial meeting info; 1-2 concerns
- 0.0-0.2: No meaningful meeting or concern information
"""

    _BRIEF_STRUCTURE_RUBRIC = """\
Evaluate the structure and actionability of the pre-meeting brief (0.0-1.0).

## Expected brief structure
1. Meeting basic information section (time, attendees, location)
2. Client current concerns section (SOC 2, HIS, pilot timeline)
3. Risks / open issues section (procurement materials TBD, API boundary, sample data)
4. Recommended talking points / next steps (implementation plan, owners, checklist)
5. Based only on retrieved data, not fabricated

## Ground Truth -- Key risks/open items
- Security questionnaire / procurement materials not yet prepared
- API boundary definition pending
- De-identified sample data pack needed

## Scoring tiers
- 0.9-1.0: All 4 sections present and clearly structured; actionable next steps with owners; risk items specific
- 0.7-0.8: 3-4 sections present; some actionable items
- 0.5-0.6: 2-3 sections; vague next steps
- 0.3-0.4: Minimal structure; no clear next steps
- 0.0-0.2: No meaningful brief structure
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

        # 1. Tool gate
        tool_penalty = self._tool_gate(dispatches)

        # 2. Deterministic checks (35%)
        det_score = 0.0
        det_score += 0.30 * self._score_basic_info(final_text)
        det_score += 0.40 * self._score_topic_coverage(final_text)
        det_score += 0.30 * self._score_next_steps(final_text)

        # 3. Judge scoring (65%)
        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            content_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._CONTENT_RUBRIC
            ).score
            structure_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._BRIEF_STRUCTURE_RUBRIC
            ).score
        else:
            content_score = self._fallback_content(final_text)
            structure_score = self._fallback_structure(final_text)

        # 4. Combine
        completion = tool_penalty * (
            0.35 * det_score
            + 0.35 * content_score
            + 0.30 * structure_score
        )

        scores.completion = min(round(completion, 2), 1.0)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len(
            [m for m in messages if m.message.role == "assistant"]
        )
        return scores

    # ── Deterministic helpers ──────────────────────────────────────

    def _tool_gate(self, dispatches: list[ToolDispatch]) -> float:
        """Penalty multiplier: did the agent read calendar, emails, AND notes?"""
        cal_calls = [d for d in dispatches
                     if d.tool_name in ("calendar_list_events", "calendar_get_event")
                     and d.response_status < 400]
        email_calls = [d for d in dispatches
                       if d.tool_name in ("gmail_list_messages", "gmail_get_message")
                       and d.response_status < 400]
        notes_calls = [d for d in dispatches
                       if d.tool_name in ("notes_list", "notes_get")
                       and d.response_status < 400]
        sources = sum([bool(cal_calls), bool(email_calls), bool(notes_calls)])
        if sources == 0:
            return 0.2
        if sources == 1:
            return 0.4
        if sources == 2:
            return 0.7
        return 1.0

    def _score_basic_info(self, final_text: str) -> float:
        """Check for meeting basic info entities."""
        entities = ["2026-03-19", "15:00", "16:00", "Zoom",
                    "StarCare Health", "StarCare"]
        found = sum(1 for e in entities if e in final_text)
        attendees = ["Jing Li", "Tao Wang", "Ning Zhou"]
        att_found = sum(1 for a in attendees if a in final_text)
        score = 0.55 * min(found / 4, 1.0) + 0.45 * min(att_found / 2, 1.0)
        return min(score, 1.0)

    def _score_topic_coverage(self, final_text: str) -> float:
        """Check for key concern topics."""
        concerns = ["SOC 2", "compliance", "HIS", "read-only", "pilot",
                    "procurement", "de-identified data", "sample data"]
        found = sum(1 for c in concerns if c.lower() in final_text.lower())
        return min(found / 4, 1.0)

    def _score_next_steps(self, final_text: str) -> float:
        """Check for next steps / action items section."""
        lower = final_text.lower()
        kw = ["next step", "action", "talking point", "recommendation",
              "timeline", "materials", "checklist", "owner", "advance"]
        found = sum(1 for k in kw if k in lower)
        section_like = bool(re.search(
            r"[Rr]isk|[Oo]pen issue|[Nn]ext step|[Aa]ction|[Tt]alking point", final_text))
        score = 0.60 * min(found / 3, 1.0)
        if section_like:
            score += 0.40
        return min(score, 1.0)

    # ── Fallback scorers ───────────────────────────────────────────

    def _fallback_content(self, final_text: str) -> float:
        """_fallback_: dev-only keyword scoring for content accuracy."""
        score = 0.0
        if "2026-03-19" in final_text:
            score += 0.10
        if "15:00" in final_text:
            score += 0.08
        if "SOC 2" in final_text:
            score += 0.12
        if "HIS" in final_text:
            score += 0.10
        if "pilot" in final_text.lower():
            score += 0.10
        if "procurement" in final_text.lower():
            score += 0.10
        if "de-identified" in final_text.lower():
            score += 0.10
        attendees = ["Jing Li", "Tao Wang", "Ning Zhou"]
        score += 0.20 * min(sum(1 for a in attendees if a in final_text) / 2, 1.0)
        return min(score, 1.0)

    def _fallback_structure(self, final_text: str) -> float:
        """_fallback_: dev-only keyword scoring for brief structure."""
        score = 0.0
        lower = final_text.lower()
        sections = ["meeting", "concern", "risk", "next step", "action", "talking point"]
        score += 0.40 * min(sum(1 for s in sections if s in lower) / 3, 1.0)
        if len(final_text.strip()) >= 300:
            score += 0.20
        if "starcare" in lower:
            score += 0.15
        if any(k in lower for k in ["timeline", "materials", "owner"]):
            score += 0.15
        return min(score, 1.0)
