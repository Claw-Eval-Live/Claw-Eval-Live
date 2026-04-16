"""CTB_SHELL_18 grader -- Container Restart Analysis.

Ground truth (from fixtures):
  Tickets:
    TK-401: payment-service OOMKilled, 23 restarts, memory limit 512MB, v2.3.1 upgrade +40%
    TK-402: order-service CrashLoopBackOff, Redis connection refused, redis-cluster-01 hardcoded
    TK-403: notification-service health check timeout, 72 restarts, sync email blocks main thread, P99 5s
    TK-404: gateway-service traffic imbalance 78%, readiness probe initialDelay 5s vs 30s boot
  Notes:
    note_401: K8s cluster config (resource limits, probe configs)
    note_402: March incident records (v2.3.1 deploy, Redis failover, node update)

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


EXPECTED_TICKETS = {"TK-401", "TK-402", "TK-403", "TK-404"}
EXPECTED_NOTES = {"note_401", "note_402"}


class Grader(AbstractGrader):
    """Grade container restart root-cause analysis report."""

    # -- Judge rubrics -------------------------------------------------------

    _DIAGNOSTIC_RUBRIC = """\
Evaluate the diagnostic accuracy of the container restart analysis (0.0-1.0).

## Ground Truth -- payment-service (TK-401)
- Root cause: OOMKilled due to memory limit 512MB being too low
- Evidence: 23 restarts in 48 hours, v2.3.1 upgrade increased memory usage by 40%
- JVM: Xms128m / Xmx384m, HikariCP connection pool leak suspected

## Ground Truth -- order-service (TK-402)
- Root cause: CrashLoopBackOff, Redis connection refused
- Evidence: redis-cluster-01 address hardcoded, but Redis primary failed over to redis-cluster-02
- The Sentinel/Cluster discovery mechanism was not used

## Ground Truth -- notification-service (TK-403)
- Root cause: liveness probe timeout (3s threshold) due to synchronous email sending blocking /health
- Evidence: 72 restarts in 24 hours, P99 latency 5 seconds on health endpoint

## Ground Truth -- gateway-service (TK-404)
- Root cause: readiness probe initialDelaySeconds=5s but service boot takes 30s
- Evidence: traffic imbalance (pod-gw-01: 78%, pod-gw-02: 18%, pod-gw-03: 4%)

## Scoring tiers
- 0.9-1.0: All 4 containers diagnosed with correct root cause and supporting evidence
- 0.7-0.8: 3-4 containers diagnosed; most root causes correct
- 0.5-0.6: 2-3 containers with partial root cause identification
- 0.3-0.4: 1-2 containers with minimal analysis
- 0.0-0.2: No meaningful diagnostic analysis
"""

    _REMEDIATION_RUBRIC = """\
Evaluate the quality and actionability of remediation recommendations (0.0-1.0).

## Expected Remediation
1. payment-service: increase memory limit (e.g. to 1GB+), tune JVM heap, investigate connection pool leak
2. order-service: use Redis Sentinel or Cluster discovery instead of hardcoded address, update config to redis-cluster-02
3. notification-service: make email sending async (separate thread/queue), or increase liveness probe timeout
4. gateway-service: increase readiness probe initialDelaySeconds to 30+ seconds to match actual boot time

## Scoring tiers
- 0.9-1.0: All 4 services have specific, actionable remediation with correct technical details
- 0.7-0.8: 3-4 services have reasonable recommendations
- 0.5-0.6: 2-3 services with partial or generic recommendations
- 0.3-0.4: Only 1-2 services addressed, or very generic advice
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
        det_score += 0.30 * self._score_key_findings(clean, all_text)

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
            diag_score = self._fallback_diagnostic(clean, all_text)
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
        # Audit bonus
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
        ticket_cov = len(read_tickets) / len(EXPECTED_TICKETS) if EXPECTED_TICKETS else 0
        note_cov = len(read_notes) / len(EXPECTED_NOTES) if EXPECTED_NOTES else 0
        return 0.6 * ticket_cov + 0.4 * note_cov

    @staticmethod
    def _score_key_findings(clean: str, all_text: str) -> float:
        """Check for verifiable key data points across all 4 containers."""
        checks = [
            # payment-service: OOMKilled + 23 restarts
            bool(re.search(r"OOMKilled|OOM|out.of.memory", clean, re.IGNORECASE)),
            bool(re.search(r"(?<!\d)23(?!\d).*restart|restart.*(?<!\d)23(?!\d)", clean, re.IGNORECASE)),
            # order-service: CrashLoopBackOff + Redis
            bool(re.search(r"CrashLoopBackOff", clean, re.IGNORECASE)),
            bool(re.search(r"redis-cluster-01|redis.*hardcod|hardcod.*redis", clean, re.IGNORECASE)),
            # notification-service: 72 restarts + health check
            bool(re.search(r"(?<!\d)72(?!\d).*restart|restart.*(?<!\d)72(?!\d)", clean, re.IGNORECASE)),
            # gateway-service: traffic imbalance 78%
            bool(re.search(r"(?<!\d)78\s*%|traffic.*imbalanc|uneven.*distribut", clean, re.IGNORECASE)),
        ]
        return sum(checks) / len(checks)

    # -- Fallback scorers (no judge) -----------------------------------------

    def _fallback_diagnostic(self, clean: str, all_text: str) -> float:
        """Keyword-based scoring for diagnostic accuracy."""
        score = 0.0

        # payment-service OOMKilled
        if re.search(r"OOMKilled|OOM|out.of.memory|insufficient.*memory", clean, re.IGNORECASE):
            score += 0.08
        if re.search(r"512\s*M|memory.*limit|memory.*512", clean, re.IGNORECASE):
            score += 0.06
        if re.search(r"v2\.3\.1|memory.*increas.*40|memory.*grow.*40", clean, re.IGNORECASE):
            score += 0.06

        # order-service Redis CrashLoopBackOff
        if re.search(r"CrashLoopBackOff|crash.*loop", clean, re.IGNORECASE):
            score += 0.08
        if re.search(r"redis-cluster-01.*hardcod|hardcod.*redis|hard.?coded.*redis", clean, re.IGNORECASE):
            score += 0.06
        if re.search(r"redis-cluster-02|failover|Sentinel|primary.*switch", clean, re.IGNORECASE):
            score += 0.06

        # notification-service health check
        if re.search(r"(?<!\d)72(?!\d).*restart|restart.*(?<!\d)72(?!\d)|liveness.*timeout|health.*timeout", clean, re.IGNORECASE):
            score += 0.08
        if re.search(r"sync.*email|synchronous.*email|block.*main.*thread|email.*block", clean, re.IGNORECASE):
            score += 0.06
        if re.search(r"P99.*5\s*s|P99.*latency|5\s*second.*latency", clean, re.IGNORECASE):
            score += 0.06

        # gateway-service readiness
        if re.search(r"(?<!\d)78\s*%|traffic.*imbalanc|traffic.*uneven|unbalanced", clean, re.IGNORECASE):
            score += 0.06
        if re.search(r"initialDelay.*5|initial.*delay.*5s|boot.*30\s*s|startup.*30", clean, re.IGNORECASE):
            score += 0.06

        return min(score, 1.0)

    @staticmethod
    def _fallback_remediation(clean: str) -> float:
        """Keyword-based scoring for remediation quality."""
        score = 0.0
        remediation_patterns = [
            r"increase.*memory|memory.*1\s*G|expand.*memory|raise.*memory.*limit",
            r"Sentinel|cluster.*discovery|service.*discovery|dynamic.*config",
            r"async|asynchronous|separate.*thread|message.*queue|increase.*timeout",
            r"initialDelay.*3[05]|initial.*delay.*3[05]|increase.*readiness|readiness.*30",
        ]
        for pat in remediation_patterns:
            if re.search(pat, clean, re.IGNORECASE):
                score += 0.25
        return min(score, 1.0)
