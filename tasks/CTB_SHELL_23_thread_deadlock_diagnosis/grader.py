"""CTB_SHELL_23 grader -- Thread Deadlock Diagnosis.

Ground truth (from fixtures):
  Tickets:
    TK-901: payment-service thread pool exhausted, 187/200 WAITING, HikariCP maxPoolSize=20 all ACTIVE,
            monthly-settlement task holds exclusive lock on account_balance for 47 minutes
    TK-902: order-service database deadlock 23 times in 6 hours, orders vs inventory lock order inconsistency,
            v2.5.0 changed ordering from productId to orderAmount
    TK-903: notification-service Java deadlock: Thread-EmailSender holds EmailTemplateCache.lock, waits RateLimiter.lock;
            Thread-RateLimitUpdater holds RateLimiter.lock, waits EmailTemplateCache.lock (classic AB-BA)
    TK-904: Redis distributed lock TTL 3600s (1h), task normally takes 5 min, OOM crash left lock unreleased,
            5 scheduled tasks failed, 75 minute reconciliation delay
  Notes:
    note_901: thread/lock configuration standards (tryLock with timeout, lock ordering, TTL = 2x exec time)
    note_902: March incident records (monthly-settlement, v2.5.0 release, RateLimitUpdater config)

v2 hybrid:
  - Deterministic 35%: tool coverage, ticket/note ID coverage, key findings
  - Judge 65%: 2 rubrics (diagnostic accuracy + remediation recommendations)
  - Fallback: English-first keyword scoring when judge unavailable
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


EXPECTED_TICKETS = {"TK-901", "TK-902", "TK-903", "TK-904"}
EXPECTED_NOTES = {"note_901", "note_902"}


class Grader(AbstractGrader):
    """Grade thread deadlock diagnosis report."""

    # -- Judge rubrics -------------------------------------------------------

    _DIAGNOSTIC_RUBRIC = """\
Evaluate the diagnostic accuracy of the thread/deadlock analysis (0.0-1.0).

## Ground Truth -- TK-901: Connection pool exhaustion
- Root cause: monthly-settlement scheduled task holds exclusive lock (SELECT ... FOR UPDATE) on account_balance for 47 minutes
- Evidence: 187 of 200 threads in WAITING state, HikariCP maxPoolSize=20 with all 20 connections ACTIVE
- The long transaction blocks all other threads waiting for database connections

## Ground Truth -- TK-902: Database deadlock (23 occurrences)
- Root cause: inconsistent lock ordering -- Transaction A locks orders then inventory, Transaction B locks inventory then orders
- Evidence: v2.5.0 changed sort order from productId to orderAmount, causing different lock acquisition sequence
- Impact: ~2% of order creation requests rolled back

## Ground Truth -- TK-903: Java thread deadlock (classic AB-BA pattern)
- Thread-EmailSender holds EmailTemplateCache.lock, waiting for RateLimiter.lock
- Thread-RateLimitUpdater holds RateLimiter.lock, waiting for EmailTemplateCache.lock
- Evidence: deadlock lasted 35 minutes, email notification feature completely blocked

## Ground Truth -- TK-904: Distributed lock not released
- Root cause: Redis lock TTL set to 3600s (1 hour), task normally takes 5 minutes, process crashed (OOM) without releasing lock
- Evidence: 5 subsequent scheduled tasks failed, reconciliation data delayed 75 minutes
- Missing watchdog/renewal mechanism for lock lifecycle management

## Scoring tiers
- 0.9-1.0: All 4 issues diagnosed with correct root cause, lock chain, and supporting evidence
- 0.7-0.8: 3-4 issues diagnosed; most root causes and lock patterns correct
- 0.5-0.6: 2-3 issues with partial root cause identification
- 0.3-0.4: 1-2 issues with minimal analysis
- 0.0-0.2: No meaningful diagnostic analysis
"""

    _REMEDIATION_RUBRIC = """\
Evaluate the quality and actionability of remediation recommendations (0.0-1.0).

## Expected Remediation
1. TK-901: Add transaction timeout for monthly-settlement (e.g. 60s), kill the 47-minute long transaction, implement connection pool monitoring with alerts
2. TK-902: Enforce consistent lock ordering (always lock by productId, not orderAmount), add deadlock retry logic, consider optimistic locking
3. TK-903: Replace synchronized with ReentrantLock.tryLock(timeout), enforce fixed lock acquisition order (always AccountLock before InventoryLock pattern), or use lock-free design
4. TK-904: Set Redis lock TTL to 2x expected execution time (10 minutes), implement watchdog/renewal mechanism, add lock release in finally block

## Scoring tiers
- 0.9-1.0: All 4 issues have specific, actionable remediation with correct technical details
- 0.7-0.8: 3-4 issues have reasonable recommendations
- 0.5-0.6: 2-3 issues with partial or generic recommendations
- 0.3-0.4: Only 1-2 issues addressed, or very generic advice
- 0.0-0.2: No meaningful remediation recommendations
"""

    # -- Main grading --------------------------------------------------------

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

        # 1. Deterministic: tool and data coverage (35%)
        det_score = 0.0
        det_score += 0.35 * self._score_tool_coverage(dispatches, audit_data)
        det_score += 0.35 * self._score_id_coverage(dispatches)
        det_score += 0.30 * self._score_key_findings(clean)

        # 2. Judge scoring (65%)
        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            diag_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._DIAGNOSTIC_RUBRIC
            ).score
            remed_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._REMEDIATION_RUBRIC
            ).score
        else:
            diag_score = self._fallback_diagnostic(clean)
            remed_score = self._fallback_remediation(clean)

        completion = 0.35 * det_score + 0.40 * diag_score + 0.25 * remed_score
        scores.completion = min(round(completion, 2), 1.0)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len(
            [m for m in messages if m.message.role == "assistant"]
        )
        return scores

    # -- Deterministic helpers -----------------------------------------------

    def _score_tool_coverage(self, dispatches: list[ToolDispatch],
                             audit_data: dict | None) -> float:
        """Did the agent call both helpdesk and notes tools?"""
        hd = [d for d in dispatches
              if d.tool_name in ("helpdesk_list_tickets", "helpdesk_get_ticket")
              and d.response_status < 400]
        nd = [d for d in dispatches
              if d.tool_name in ("notes_list_documents", "notes_get_document",
                                 "notes_list", "notes_get")
              and d.response_status < 400]
        tool_score = 0.0
        if hd:
            tool_score += 0.5 * min(len(hd) / 4, 1.0)
        if nd:
            tool_score += 0.3 * min(len(nd) / 2, 1.0)
        if audit_data:
            hd_audit = audit_data.get("helpdesk", {}).get("calls", [])
            nd_audit = audit_data.get("notes", {}).get("calls", [])
            if len(hd_audit) >= 1 and len(nd_audit) >= 1:
                tool_score += 0.2
        elif hd and nd:
            tool_score += 0.1
        return min(tool_score, 1.0)

    def _score_id_coverage(self, dispatches: list[ToolDispatch]) -> float:
        """Check how many expected ticket and note IDs were retrieved."""
        read_tickets = set()
        read_notes = set()
        for d in dispatches:
            body = d.request_body if isinstance(d.request_body, dict) else {}
            if d.tool_name == "helpdesk_get_ticket" and d.response_status < 400:
                tid = body.get("ticket_id", "")
                if tid in EXPECTED_TICKETS:
                    read_tickets.add(tid)
            if d.tool_name in ("notes_get_document", "notes_get") and d.response_status < 400:
                nid = body.get("note_id", "")
                if nid in EXPECTED_NOTES:
                    read_notes.add(nid)
        ticket_cov = len(read_tickets) / len(EXPECTED_TICKETS)
        note_cov = len(read_notes) / len(EXPECTED_NOTES)
        return 0.6 * ticket_cov + 0.4 * note_cov

    @staticmethod
    def _score_key_findings(clean: str) -> float:
        """Check for verifiable key data points across all 4 issues."""
        checks = [
            # TK-901: connection pool exhaustion + WAITING threads
            bool(re.search(r"187.*WAITING|WAITING.*187|connection.*pool.*exhaust|pool.*exhaust", clean, re.IGNORECASE)),
            bool(re.search(r"47\s*min|monthly.settlement|account_balance", clean, re.IGNORECASE)),
            # TK-902: database deadlock + lock ordering
            bool(re.search(r"(?<!\d)23(?!\d).*deadlock|deadlock.*(?<!\d)23(?!\d)|database.*deadlock", clean, re.IGNORECASE)),
            bool(re.search(r"orders.*inventory|inventory.*orders|lock.*order.*inconsist|inconsist.*lock", clean, re.IGNORECASE)),
            # TK-903: Java deadlock AB-BA
            bool(re.search(r"EmailSender|RateLimitUpdater|EmailTemplateCache|Java.*deadlock", clean, re.IGNORECASE)),
            # TK-904: distributed lock TTL
            bool(re.search(r"3600\s*s|TTL.*1\s*h|lock.*not.*releas|lock.*unreleased|watchdog", clean, re.IGNORECASE)),
        ]
        return sum(checks) / len(checks)

    # -- Fallback scorers (no judge) -----------------------------------------

    @staticmethod
    def _fallback_diagnostic(clean: str) -> float:
        """Keyword-based scoring for diagnostic accuracy."""
        score = 0.0

        # TK-901: connection pool exhaustion
        if re.search(r"187.*WAITING|thread.*block|thread.*stuck|connection.*pool.*exhaust", clean, re.IGNORECASE):
            score += 0.06
        if re.search(r"47\s*min|long.*transaction|long.*running.*transaction|monthly.settlement", clean, re.IGNORECASE):
            score += 0.07
        if re.search(r"account_balance|maxPoolSize.*20|FOR UPDATE|exclusive.*lock", clean, re.IGNORECASE):
            score += 0.06

        # TK-902: database deadlock
        if re.search(r"(?<!\d)23(?!\d).*deadlock|deadlock.*(?<!\d)23(?!\d)|database.*deadlock|DB.*deadlock", clean, re.IGNORECASE):
            score += 0.06
        if re.search(r"orders.*inventory|inventory.*orders|lock.*order.*inconsist|inconsist.*lock.*order", clean, re.IGNORECASE):
            score += 0.07
        if re.search(r"v2\.5\.0|orderAmount.*sort|productId.*sort|lock.*ordering", clean, re.IGNORECASE):
            score += 0.05

        # TK-903: Java AB-BA deadlock
        if re.search(r"EmailSender|RateLimitUpdater|Java.*deadlock|JVM.*deadlock", clean, re.IGNORECASE):
            score += 0.06
        if re.search(r"EmailTemplateCache.*lock|RateLimiter.*lock|reverse.*order|opposite.*order|AB.BA", clean, re.IGNORECASE):
            score += 0.07

        # TK-904: distributed lock
        if re.search(r"3600\s*s|TTL.*1\s*hour|TTL.*too.*long|TTL.*excessive|lock.*TTL", clean, re.IGNORECASE):
            score += 0.06
        if re.search(r"watchdog|renewal|lock.*renew|renew.*lock", clean, re.IGNORECASE):
            score += 0.05
        if re.search(r"75\s*min|5.*dispatch|5.*schedul|reconcili.*delay", clean, re.IGNORECASE):
            score += 0.05

        return min(score, 1.0)

    @staticmethod
    def _fallback_remediation(clean: str) -> float:
        """Keyword-based scoring for remediation quality."""
        score = 0.0
        remediation_patterns = [
            r"transaction.*timeout|kill.*long.*transaction|terminat.*long.*transaction|add.*timeout",
            r"consistent.*lock.*order|fixed.*lock.*order|unif.*lock.*order|always.*lock.*same.*order",
            r"tryLock|ReentrantLock|lock.*timeout|lock.free|optimistic.*lock",
            r"watchdog|renewal.*mechanism|reduce.*TTL|TTL.*10\s*min|finally.*release",
        ]
        for pat in remediation_patterns:
            if re.search(pat, clean, re.IGNORECASE):
                score += 0.25
        return min(score, 1.0)
