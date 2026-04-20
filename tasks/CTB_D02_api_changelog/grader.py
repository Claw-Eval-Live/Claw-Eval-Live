"""CTB_D02 grader — compare two API docs and write a migration changelog.

v2.2: analysis mode (document transform).
- Deterministic 30%: API endpoint coverage, change type labels, table structure
- Judge 70%: change accuracy per section, completeness, migration recommendations
"""

from __future__ import annotations

from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class ApiChangelogGrader(AbstractGrader):
    """Grade a structured API version changelog."""

    _ACCURACY_RUBRIC = """\
Evaluate whether each API change is correctly identified with old→new details (0.0-1.0).

## Ground Truth — 6 changes
1. Auth: POST /v1/sessions → POST /v2/auth/tokens; session_id → access_token/refresh_token; Breaking
2. Events: /v1/events → /v2/events; page→cursor, start_date/end_date→from/to, event_count→total; Breaking
3. Orders: customer_id→account_id, amount_cents→amount(decimal), currency now required; Breaking
4. Estimate: NEW POST /v2/orders/estimate; Non-breaking (ADD)
5. Webhook: HMAC-SHA1→HMAC-SHA256, new X-Timestamp, 5-min replay window; Breaking
6. Reports: /v1/reports/daily → /v2/reports/daily-summary, old deprecated; Breaking

## Scoring tiers
- 0.9-1.0: All 6 changes correctly described with old→new field mappings and breaking classification
- 0.7-0.8: 5-6 changes identified, most details correct
- 0.5-0.6: 3-4 changes identified with partial details
- 0.3-0.4: Only 1-2 changes
- 0.0-0.2: No meaningful change identification
"""

    _COMPLETENESS_RUBRIC = """\
Evaluate whether all 6 API sections are covered (0.0-1.0).

## Required sections
1. Authentication (Auth): /v1/sessions → /v2/auth/tokens
2. Event Query (Events): pagination and parameter changes
3. Order Creation (Orders): field renames and type changes
4. Estimate (Estimate): new endpoint
5. Webhook Verification: signature algorithm upgrade
6. Daily Report (Reports): endpoint rename and deprecation

## Scoring tiers
- 0.9-1.0: All 6 sections explicitly covered
- 0.7-0.8: 5 sections covered
- 0.5-0.6: 3-4 sections covered
- 0.3-0.4: Only 1-2 sections
- 0.0-0.2: No structured coverage
"""

    _MIGRATION_RUBRIC = """\
Evaluate the quality of migration priority recommendations (0.0-1.0).

## Expected elements
- Priority ranking: orders schema + webhook verification + event query params should be highest priority (Breaking changes)
- Estimate endpoint is low priority (non-breaking ADD)
- Timeline or phased migration suggestion
- Risk assessment per change

## Scoring tiers
- 0.9-1.0: Clear priority ranking with breaking vs non-breaking reasoning; specific migration steps
- 0.7-0.8: Has priorities and some reasoning
- 0.5-0.6: Generic migration advice
- 0.3-0.4: Minimal recommendation
- 0.0-0.2: No migration guidance
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
        final_text = self._get_final_assistant_text(messages)
        lowered = final_text.lower()

        # 1. No tool gate (pure attachment analysis)

        # 2. Deterministic: 3 dimensions (30%)
        det = 0.0
        det += 0.15 * self._check_endpoint_coverage(final_text)    # dim1: v2 endpoints mentioned
        det += 0.10 * self._check_change_labels(lowered)           # dim2: ADD/MODIFY/Breaking labels
        det += 0.05 * (1.0 if ("|" in final_text and "---" in final_text) else 0.0)  # dim3: has table

        # 3. Judge: 3 rubrics (70%)
        judge_score = 0.0
        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            judge_score += 0.25 * judge.evaluate(task.prompt.text, conversation, actions, self._ACCURACY_RUBRIC).score
            judge_score += 0.25 * judge.evaluate(task.prompt.text, conversation, actions, self._COMPLETENESS_RUBRIC).score
            judge_score += 0.20 * judge.evaluate(task.prompt.text, conversation, actions, self._MIGRATION_RUBRIC).score
        else:
            judge_score = self._fallback_judge(final_text, lowered)

        # 4. Combine
        completion = det + judge_score
        scores.completion = min(round(completion, 2), 1.0)
        scores.robustness = 1.0
        scores.communication = self._score_communication(final_text)
        scores.efficiency_turns = len([m for m in messages if m.message.role == "assistant"])
        return scores

    @staticmethod
    def _check_endpoint_coverage(text):
        """Dim1: how many v2 endpoints are mentioned."""
        endpoints = ["/v2/auth/tokens", "/v2/events", "/v2/orders", "/v2/orders/estimate",
                     "/v2/reports/daily-summary"]
        found = sum(1 for ep in endpoints if ep in text)
        return min(found / 4, 1.0)

    @staticmethod
    def _check_change_labels(lowered):
        """Dim2: are change type labels used?"""
        labels = ["add", "remove", "modify", "breaking", "non-breaking"]
        found = sum(1 for l in labels if l in lowered)
        return min(found / 3, 1.0)

    @staticmethod
    def _fallback_judge(text, lowered):
        """_fallback_: dev-only."""
        score = 0.0
        # Auth change
        if "/v1/sessions" in text and "/v2/auth/tokens" in text:
            score += 0.08
        # Events change
        if "cursor" in lowered and "page" in lowered:
            score += 0.08
        # Orders change
        if "account_id" in lowered and "customer_id" in lowered:
            score += 0.08
        # Estimate new
        if "/v2/orders/estimate" in text:
            score += 0.06
        # Webhook
        if "hmac-sha256" in lowered:
            score += 0.08
        # Reports
        if "daily-summary" in lowered or "deprecated" in lowered:
            score += 0.06
        # Migration
        if "migration priority" in text.lower() or "recommendation" in text.lower():
            score += 0.10
        return min(score, 0.70)

    def _score_communication(self, text: str) -> float:
        lowered = text.lower()
        has_table = "|" in text and "---" in text
        label_hits = sum(1 for item in ["add", "remove", "modify", "breaking"] if item in lowered)
        fmt = 0.0
        if has_table:
            fmt += 0.4
        if "migration priority" in text.lower() or "recommendation" in text.lower():
            fmt += 0.3
        return self.compute_communication_substance(
            text,
            ["/v2/auth/tokens", "/v2/events", "/v2/orders", "HMAC-SHA256", "/v2/reports/daily-summary"],
            min(fmt + min(label_hits / 10, 0.2), 1.0),
        )
