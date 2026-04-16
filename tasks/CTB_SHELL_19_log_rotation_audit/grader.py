"""CTB_SHELL_19 grader -- Log Rotation Audit.

Ground truth (from fixtures):
  Tickets:
    TK-501: payment-service.log 186GB, disk 87%, logrotate config permission 600 (should be 644)
    TK-502: order-service.log 67GB / 42 days, stdout redirect bypasses log4j rotation
    TK-503: notification-service archive compression 23%, DEBUG level + base64 attachment content
    TK-504: ELK Elasticsearch 82% disk, ILM delete policy uses AND instead of OR, payment-2026.01.* 320GB
  Notes:
    note_501: log rotation config standards (permission 644, daily, 30 days, gzip, >= 90% compression)
    note_502: ELK ILM strategy config (delete: min_age:90d AND max_size:500GB, should be OR)

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


EXPECTED_TICKETS = {"TK-501", "TK-502", "TK-503", "TK-504"}
EXPECTED_NOTES = {"note_501", "note_502"}


class Grader(AbstractGrader):
    """Grade log rotation audit analysis report."""

    # -- Judge rubrics -------------------------------------------------------

    _DIAGNOSTIC_RUBRIC = """\
Evaluate the diagnostic accuracy of the log rotation audit (0.0-1.0).

## Ground Truth -- TK-501: payment-service.log permission issue
- Log file: 186GB, disk usage 87% of 500GB
- Root cause: logrotate config file permission 600 (root:root), syslog user cannot read it
- Standard: should be 644 with root:syslog ownership
- Rotation has not executed for 15+ days

## Ground Truth -- TK-502: order-service stdout redirect bypass
- Log file: 67GB, 42 days old (policy is 30 days)
- Root cause: deployment uses stdout redirect (> /var/log/app/order-service.log), bypassing log4j2 rotation
- Last rotation date: 2026-02-13 per logrotate status file

## Ground Truth -- TK-503: notification-service compression anomaly
- Archive compression ratio only 23% (standard >= 90%)
- Root cause: DEBUG level still enabled, logging base64 email attachment content
- DEBUG was enabled 10+ days ago but never reverted to INFO

## Ground Truth -- TK-504: ELK ILM policy logic error
- Elasticsearch data node 82% disk (1.64TB / 2TB)
- 3 months of payment-2026.01.* indices total 320GB not deleted
- Root cause: ILM delete policy uses AND (min_age:90d AND max_size:500GB) instead of OR
- Since total never reaches 500GB, old indices are never deleted

## Scoring tiers
- 0.9-1.0: All 4 issues diagnosed with correct root cause and supporting evidence
- 0.7-0.8: 3-4 issues diagnosed; most root causes correct
- 0.5-0.6: 2-3 issues with partial root cause identification
- 0.3-0.4: 1-2 issues with minimal analysis
- 0.0-0.2: No meaningful diagnostic analysis
"""

    _REMEDIATION_RUBRIC = """\
Evaluate the quality and actionability of remediation recommendations (0.0-1.0).

## Expected Remediation
1. TK-501: Fix logrotate config permission to 644, set ownership to root:syslog, verify cron execution
2. TK-502: Remove stdout redirect in deployment, let log4j2 handle file rotation directly, or add a dedicated logrotate config for the stdout-redirected file
3. TK-503: Revert log level from DEBUG to INFO, clean up base64 attachment logging, re-compress archives
4. TK-504: Fix ILM delete policy from AND to OR (min_age:90d OR max_size:500GB), manually clean old indices, monitor disk recovery

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
            # TK-501: 186GB + permission issue
            bool(re.search(r"186\s*GB|payment-service\.log", clean, re.IGNORECASE)),
            bool(re.search(r"permission.*6[04][04]|6[04][04].*permission|syslog.*cannot|config.*permission", clean, re.IGNORECASE)),
            # TK-502: 67GB + stdout redirect
            bool(re.search(r"67\s*GB|42\s*day", clean, re.IGNORECASE)),
            bool(re.search(r"stdout.*redirect|bypass.*log4j|log4j.*bypass", clean, re.IGNORECASE)),
            # TK-503: compression 23% + DEBUG
            bool(re.search(r"23\s*%.*compress|compress.*23\s*%|compression.*low", clean, re.IGNORECASE)),
            # TK-504: ILM AND vs OR + 320GB
            bool(re.search(r"AND.*OR|OR.*AND|logic.*error|ILM.*delete", clean, re.IGNORECASE)),
        ]
        return sum(checks) / len(checks)

    # -- Fallback scorers (no judge) -----------------------------------------

    @staticmethod
    def _fallback_diagnostic(clean: str) -> float:
        """Keyword-based scoring for diagnostic accuracy."""
        score = 0.0

        # TK-501: logrotate permission
        if re.search(r"186\s*GB|payment-service\.log", clean, re.IGNORECASE):
            score += 0.06
        if re.search(r"87\s*%|disk.*87|disk.*usage.*87", clean, re.IGNORECASE):
            score += 0.05
        if re.search(r"permission.*600|644|syslog.*cannot.*read|config.*permission", clean, re.IGNORECASE):
            score += 0.07

        # TK-502: stdout redirect
        if re.search(r"67\s*GB|42\s*day", clean, re.IGNORECASE):
            score += 0.06
        if re.search(r"stdout.*redirect|bypass.*log4j|log4j.*ineffect", clean, re.IGNORECASE):
            score += 0.07
        if re.search(r"order-service", clean, re.IGNORECASE):
            score += 0.04

        # TK-503: compression anomaly + DEBUG
        if re.search(r"23\s*%|compression.*ratio.*low|compression.*abnormal", clean, re.IGNORECASE):
            score += 0.06
        if re.search(r"base64|binary.*attach|DEBUG.*not.*revert|DEBUG.*still.*enabled", clean, re.IGNORECASE):
            score += 0.07

        # TK-504: ELK ILM
        if re.search(r"320\s*GB|payment-2026\.01|82\s*%", clean, re.IGNORECASE):
            score += 0.06
        if re.search(r"AND.*OR|OR.*AND|logic.*error|condition.*error|ILM.*delete", clean, re.IGNORECASE):
            score += 0.07
        if re.search(r"lifecycle|ILM", clean, re.IGNORECASE):
            score += 0.04

        return min(score, 1.0)

    @staticmethod
    def _fallback_remediation(clean: str) -> float:
        """Keyword-based scoring for remediation quality."""
        score = 0.0
        remediation_patterns = [
            r"fix.*permission|correct.*permission|repair.*permission|chmod.*644",
            r"remove.*stdout.*redirect|fix.*log4j|add.*logrotate.*config|dedicated.*rotation",
            r"revert.*INFO|restore.*INFO|reset.*log.*level|disable.*DEBUG",
            r"fix.*ILM|change.*AND.*OR|correct.*ILM|clean.*index|purge.*index",
        ]
        for pat in remediation_patterns:
            if re.search(pat, clean, re.IGNORECASE):
                score += 0.25
        return min(score, 1.0)
