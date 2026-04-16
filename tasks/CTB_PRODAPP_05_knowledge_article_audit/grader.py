"""CTB_PRODAPP_05 grader -- knowledge article audit.

Ground truth source: task.yaml reference_solution is empty for this task.
Rubric ground truth derived from fixture data:
  - fixtures/notes/meetings.json
  - fixtures/todo/tasks.json
  - fixtures/calendar/events.json

v2.2: WildClawBench mode (operation + analysis).
- Deterministic 55%: tool gate, article status accuracy, responsible person assignment
- Judge 45%: audit analysis quality, update plan quality
- Fallback: keyword-based scoring when judge unavailable

Ground truth (from fixtures):
  - note_p05a Payment System Deploy: outdated (v2.0, but v3.0 migration in progress)
  - note_p05b Monitoring System Guide: outdated (Zabbix 4.0, new platform being built)
  - note_p05c API Spec v3.0: current (updated 2026-03-01)
  - note_p05d New Employee Onboarding: needs update (refs payment-v2, zabbix-old)
  - Responsible: Wang Qiang for payment, Chen Lei for monitoring, Zhang Wei for onboarding
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage

ARTICLES = {
    "\u652f\u4ed8\u7cfb\u7edf\u90e8\u7f72": {
        "note_id": "note_p05a",
        "status": "outdated",
        "status_kws": ["\u8fc7\u65f6", "outdated", "\u8fc7\u671f", "\u9700\u66f4\u65b0",
                       "needs update", "v2.0", "v3.0", "stale", "deprecated"],
        "responsible": "\u738b\u5f3a",
        "alt_names": ["payment system deploy", "payment deployment"],
    },
    "\u76d1\u63a7\u7cfb\u7edf": {
        "note_id": "note_p05b",
        "status": "outdated",
        "status_kws": ["\u8fc7\u65f6", "outdated", "Zabbix", "\u65e7\u7248",
                       "needs update", "4.0", "stale", "deprecated"],
        "responsible": "\u9648\u78ca",
        "alt_names": ["monitoring system", "monitoring guide"],
    },
    "API\u63a5\u53e3\u89c4\u8303": {
        "note_id": "note_p05c",
        "status": "current",
        "status_kws": ["\u6700\u65b0", "current", "\u65e0\u9700\u66f4\u65b0",
                       "no update needed", "\u6b63\u5e38", "up to date", "valid"],
        "alt_names": ["API specification", "API interface"],
    },
    "\u5165\u804c": {
        "note_id": "note_p05d",
        "status": "outdated",
        "status_kws": ["\u8fc7\u65f6", "outdated", "\u9700\u66f4\u65b0",
                       "needs update", "payment-v2", "zabbix-old", "stale", "deprecated"],
        "responsible": "\u5f20\u4f1f",
        "alt_names": ["onboarding", "new employee"],
    },
}


def _match_article(article_key: str, text: str) -> bool:
    if article_key in text:
        return True
    info = ARTICLES.get(article_key, {})
    text_lower = text.lower()
    for alt in info.get("alt_names", []):
        if alt.lower() in text_lower:
            return True
    return False


class Grader(AbstractGrader):
    """Grade knowledge article audit report."""

    # -- Judge rubrics --

    _AUDIT_ANALYSIS_RUBRIC = """\
Evaluate the accuracy of the knowledge article audit assessment (0.0-1.0).

## Ground Truth
- Payment System Deploy Manual (note_p05a): OUTDATED -- v2.0 but v3.0 migration in progress
- Monitoring System Guide (note_p05b): OUTDATED -- Zabbix 4.0, new monitoring platform being built
- API Spec v3.0 (note_p05c): CURRENT -- recently updated (2026-03-01)
- New Employee Onboarding Guide (note_p05d): OUTDATED -- references payment-v2 and zabbix-old URLs

## Scoring tiers
- 0.9-1.0: All 4 articles correctly assessed with specific issues described
- 0.7-0.8: 3-4 articles correct; issues mostly described
- 0.5-0.6: 2-3 articles correct; partial issue descriptions
- 0.3-0.4: 1-2 articles; minimal detail
- 0.0-0.2: No meaningful audit assessment
"""

    _UPDATE_PLAN_RUBRIC = """\
Evaluate the quality of the update plan with responsible owners and priorities (0.0-1.0).

## Ground Truth
- Wang Qiang should own payment system doc update (high priority)
- Chen Lei should own monitoring system doc update (high priority)
- Zhang Wei should own onboarding guide update (medium priority)
- API Spec needs no update
- Priority reasoning should reference current work directions from todo

## Scoring tiers
- 0.9-1.0: Correct owners for all 3 outdated docs; priority levels with rationale; action plan
- 0.7-0.8: 2-3 correct owners; priorities present; reasonable plan
- 0.5-0.6: Some owners; partial priorities
- 0.3-0.4: Minimal ownership or plan
- 0.0-0.2: No meaningful update plan
"""

    # -- Main grading --

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

        # 1. Tool gate
        tool_penalty = self._tool_gate(dispatches)

        # 2. Deterministic checks (55%)
        det_score = 0.0
        det_score += 0.20 * self._score_data_retrieval(dispatches, audit_data)
        det_score += 0.45 * self._score_audit_accuracy(all_text)
        det_score += 0.35 * self._score_responsible_assignment(all_text)

        # 3. Judge scoring (45%)
        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            audit_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._AUDIT_ANALYSIS_RUBRIC
            ).score
            plan_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._UPDATE_PLAN_RUBRIC
            ).score
        else:
            audit_score = self._fallback_audit(all_text)
            plan_score = self._fallback_plan(all_text)

        # 4. Combine
        completion = tool_penalty * (
            0.55 * det_score
            + 0.25 * audit_score
            + 0.20 * plan_score
        )

        scores.completion = min(round(completion, 2), 1.0)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len(
            [m for m in messages if m.message.role == "assistant"]
        )
        return scores

    # -- Deterministic helpers --

    def _tool_gate(self, dispatches: list[ToolDispatch]) -> float:
        notes_calls = [d for d in dispatches
                       if d.tool_name in ("notes_list", "notes_get")
                       and d.response_status < 400]
        todo_calls = [d for d in dispatches
                      if d.tool_name == "todo_list_tasks" and d.response_status < 400]
        cal_calls = [d for d in dispatches
                     if d.tool_name == "calendar_list_events" and d.response_status < 400]
        if not notes_calls:
            return 0.2
        api_count = sum(1 for c in [notes_calls, todo_calls, cal_calls] if c)
        if api_count <= 1:
            return 0.5
        return 1.0

    def _score_data_retrieval(self, dispatches: list[ToolDispatch],
                              audit_data: dict | None) -> float:
        notes_list_ok = any(d.tool_name == "notes_list" and d.response_status < 400
                            for d in dispatches)
        notes_get_calls = [d for d in dispatches
                           if d.tool_name == "notes_get" and d.response_status < 400]
        read_ids = {str(d.request_body.get("note_id", "")) for d in notes_get_calls}
        expected = {"note_p05a", "note_p05b", "note_p05c", "note_p05d"}

        score = 0.0
        if notes_list_ok:
            score += 0.25
        if read_ids:
            score += 0.35 * (len(read_ids & expected) / len(expected))
        todo_ok = any(d.tool_name == "todo_list_tasks" and d.response_status < 400
                      for d in dispatches)
        cal_ok = any(d.tool_name == "calendar_list_events" and d.response_status < 400
                     for d in dispatches)
        if todo_ok:
            score += 0.20
        if cal_ok:
            score += 0.20
        return min(score, 1.0)

    def _score_audit_accuracy(self, text: str) -> float:
        """Check correct status per article using context-aware matching."""
        score = 0.0
        per_article = 1.0 / len(ARTICLES)

        for article_key, info in ARTICLES.items():
            if not _match_article(article_key, text):
                continue

            # Gather context around article mentions
            ctx = ""
            search_terms = [article_key] + info.get("alt_names", [])
            for term in search_terms:
                for m in re.finditer(re.escape(term), text, re.IGNORECASE):
                    start = max(0, m.start() - 200)
                    end = min(len(text), m.end() + 200)
                    ctx += text[start:end]

            if any(kw in ctx for kw in info["status_kws"]):
                score += per_article

        return min(score, 1.0)

    def _score_responsible_assignment(self, text: str) -> float:
        """Check correct person is paired with correct article."""
        responsible_articles = {k: v for k, v in ARTICLES.items() if v.get("responsible")}
        if not responsible_articles:
            return 1.0
        per_resp = 1.0 / len(responsible_articles)
        score = 0.0

        for article_key, info in responsible_articles.items():
            person = info["responsible"]
            if person not in text or not _match_article(article_key, text):
                continue
            # Check proximity
            art_positions = [m.start() for m in re.finditer(re.escape(article_key), text)]
            for alt in info.get("alt_names", []):
                art_positions += [m.start() for m in re.finditer(re.escape(alt), text, re.IGNORECASE)]
            per_positions = [m.start() for m in re.finditer(re.escape(person), text)]
            if any(abs(ap - pp) < 300 for ap in art_positions for pp in per_positions):
                score += per_resp

        return min(score, 1.0)

    # -- Fallback scorers --

    def _fallback_audit(self, text: str) -> float:
        """_fallback_: dev-only keyword scoring for audit analysis."""
        score = 0.0
        for article_key, info in ARTICLES.items():
            if _match_article(article_key, text):
                if any(kw in text for kw in info["status_kws"]):
                    score += 0.20
        return min(score, 1.0)

    def _fallback_plan(self, text: str) -> float:
        """_fallback_: dev-only keyword scoring for update plan."""
        score = 0.0
        plan_kw = ["\u66f4\u65b0", "\u4fee\u6539", "\u8ba1\u5212", "plan",
                   "\u6392\u671f", "\u4f18\u5148", "\u5efa\u8bae",
                   "update", "revise", "schedule", "priority", "recommend"]
        found = sum(1 for kw in plan_kw if kw in text)
        score += min(found / 3, 1.0)
        return min(score, 1.0)
