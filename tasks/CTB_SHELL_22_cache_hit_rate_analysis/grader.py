"""CTB_SHELL_22 grader -- Cache Hit Rate Analysis.

Ground truth (from fixtures):
  Tickets:
    TK-801: Redis hit rate 92% -> 58%, 61wan evictions, product-catalog TTL misconfigured to 1h (was 24h)
    TK-802: CDN hit rate 96% -> 41%, origin bandwidth 200Mbps -> 1.8Gbps, webpack contenthash+timestamp
    TK-803: Caffeine local cache 88% -> 35%, maximumSize=10000 insufficient after v2.5.0 added 3 new endpoints
    TK-804: MySQL query_cache 75% -> 12%, 400 DDL/min from ETL task invalidating cache
  Notes:
    note_801: cache architecture doc (Redis 4GB allkeys-lru, CDN URL hash, Caffeine, MySQL query_cache)

v2 hybrid:
  - Deterministic 35%: tool coverage, ticket/note ID coverage, key findings
  - Judge 65%: 2 rubrics (diagnostic accuracy + optimization recommendations)
  - Fallback: English-first keyword scoring when judge unavailable
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


EXPECTED_TICKETS = {"TK-801", "TK-802", "TK-803", "TK-804"}
EXPECTED_NOTES = {"note_801"}


class Grader(AbstractGrader):
    """Grade cache hit rate analysis and optimization report."""

    # -- Judge rubrics -------------------------------------------------------

    _DIAGNOSTIC_RUBRIC = """\
Evaluate the diagnostic accuracy of the cache hit rate analysis (0.0-1.0).

## Ground Truth -- TK-801: Redis hit rate drop (92% -> 58%)
- Root cause: product-catalog TTL was changed to 1 hour (from 24 hours) by a script on March 26
- Evidence: 61wan (610k) key evictions, maxmemory 4GB nearly full at 3.9GB
- Impact: product-catalog keys evicted user-session keys under allkeys-lru policy

## Ground Truth -- TK-802: CDN hit rate drop (96% -> 41%)
- Root cause: March 26 release changed webpack config to use contenthash + timestamp in URLs
- Evidence: origin bandwidth surged from 200Mbps to 1.8Gbps, nginx CPU at 92%
- Every deployment generates new URLs, making CDN cache useless

## Ground Truth -- TK-803: Caffeine local cache drop (88% -> 35%)
- Root cause: order-service v2.5.0 added 3 new endpoints, doubling cache key space
- Evidence: maximumSize=10000 insufficient, eviction rate 200+/second
- Cache key distribution: order-detail 45%, user-preference 30%, inventory 25%

## Ground Truth -- TK-804: MySQL query_cache drop (75% -> 12%)
- Root cause: ETL task executes ~400 DDL (ALTER TABLE) per minute, invalidating entire query cache
- Evidence: Qcache_hits/Com_select = 0.12, ETL was changed from batch to real-time on March 26

## Scoring tiers
- 0.9-1.0: All 4 cache layers diagnosed with correct root cause and evidence
- 0.7-0.8: 3-4 layers diagnosed; most root causes correct
- 0.5-0.6: 2-3 layers with partial root cause identification
- 0.3-0.4: 1-2 layers with minimal analysis
- 0.0-0.2: No meaningful diagnostic analysis
"""

    _OPTIMIZATION_RUBRIC = """\
Evaluate the quality of cache optimization recommendations (0.0-1.0).

## Expected Optimization
1. Redis: revert product-catalog TTL to 24 hours, increase maxmemory (e.g. 8GB), separate key namespaces
2. CDN: rollback webpack config to use contenthash only (without timestamp), wait 2-3 hours for CDN cache rebuild
3. Caffeine: increase maximumSize to 30000+, consider separate cache instances per endpoint type
4. MySQL: disable query_cache for DDL-heavy workload, revert ETL from real-time to batch mode
5. Prioritization: address CDN first (highest traffic impact), then Redis, then Caffeine, then MySQL

## Scoring tiers
- 0.9-1.0: All 4 layers have specific, actionable optimizations with correct technical details; prioritization included
- 0.7-0.8: 3-4 layers have reasonable recommendations
- 0.5-0.6: 2-3 layers with partial or generic recommendations
- 0.3-0.4: Only 1-2 layers addressed, or very generic advice
- 0.0-0.2: No meaningful optimization recommendations
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
            optim_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._OPTIMIZATION_RUBRIC
            ).score
        else:
            diag_score = self._fallback_diagnostic(clean)
            optim_score = self._fallback_optimization(clean)

        completion = 0.35 * det_score + 0.40 * diag_score + 0.25 * optim_score
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
            tool_score += 0.3 * min(len(nd) / 1, 1.0)
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
        """Check for verifiable key data points across all 4 cache layers."""
        checks = [
            # Redis: 58% or 92% hit rate + eviction
            bool(re.search(r"(?<!\d)58\s*%|92\s*%.*58\s*%|Redis.*hit.*rate.*drop|Redis.*hit.*rate.*declin", clean, re.IGNORECASE)),
            bool(re.search(r"61|eviction|TTL.*product.catalog", clean, re.IGNORECASE)),
            # CDN: 41% hit rate + contenthash
            bool(re.search(r"(?<!\d)41\s*%|CDN.*hit.*rate|CDN.*cache.*miss", clean, re.IGNORECASE)),
            bool(re.search(r"contenthash|webpack.*timestamp|URL.*hash", clean, re.IGNORECASE)),
            # Caffeine: 35% + maximumSize
            bool(re.search(r"(?<!\d)35\s*%|Caffeine.*hit.*rate|local.*cache.*drop", clean, re.IGNORECASE)),
            # MySQL query_cache: 12%
            bool(re.search(r"(?<!\d)12\s*%|query.?cache.*hit|query.?cache.*drop", clean, re.IGNORECASE)),
        ]
        return sum(checks) / len(checks)

    # -- Fallback scorers (no judge) -----------------------------------------

    @staticmethod
    def _fallback_diagnostic(clean: str) -> float:
        """Keyword-based scoring for diagnostic accuracy."""
        score = 0.0

        # Redis
        if re.search(r"92\s*%.*58\s*%|58\s*%|Redis.*hit.*rate.*drop|Redis.*hit.*rate.*declin", clean, re.IGNORECASE):
            score += 0.06
        if re.search(r"61|610k|610000|eviction", clean, re.IGNORECASE):
            score += 0.05
        if re.search(r"TTL.*24\s*h|TTL.*misconfigur|TTL.*wrong|product.catalog.*TTL", clean, re.IGNORECASE):
            score += 0.07

        # CDN
        if re.search(r"96\s*%.*41\s*%|41\s*%|CDN.*hit.*rate|CDN.*cache.*rate", clean, re.IGNORECASE):
            score += 0.06
        if re.search(r"1\.8\s*Gbps|origin.*surge|origin.*bandwidth|origin.*traffic.*spike", clean, re.IGNORECASE):
            score += 0.05
        if re.search(r"contenthash.*timestamp|webpack.*config|webpack.*setting|URL.*hash.*timestamp", clean, re.IGNORECASE):
            score += 0.07

        # Caffeine
        if re.search(r"88\s*%.*35\s*%|35\s*%|Caffeine.*hit.*rate|Caffeine.*cache", clean, re.IGNORECASE):
            score += 0.06
        if re.search(r"v2\.5\.0|new.*endpoint|new.*API|key.*space.*doubl|key.*doubl", clean, re.IGNORECASE):
            score += 0.05
        if re.search(r"maximumSize.*10000|capacity.*insufficient|capacity.*too.*small|increase.*30000", clean, re.IGNORECASE):
            score += 0.06

        # MySQL query_cache
        if re.search(r"75\s*%.*12\s*%|12\s*%|query.?cache.*hit.*rate|query.?cache.*ratio", clean, re.IGNORECASE):
            score += 0.05
        if re.search(r"400.*DDL|DDL.*frequent|DDL.*excessive|ETL.*real.time|ETL.*real-time", clean, re.IGNORECASE):
            score += 0.06

        return min(score, 1.0)

    @staticmethod
    def _fallback_optimization(clean: str) -> float:
        """Keyword-based scoring for optimization quality."""
        score = 0.0
        optimization_patterns = [
            r"revert.*TTL|fix.*TTL|correct.*TTL|TTL.*24\s*h|increase.*maxmemory",
            r"rollback.*webpack|revert.*webpack|contenthash.*only|remove.*timestamp",
            r"increase.*maximumSize|increase.*capacity|maximumSize.*30000|separate.*cache",
            r"disable.*query.?cache|revert.*ETL|batch.*mode|batch.*ETL",
            r"priorit|triage|severity|business.*impact|CDN.*first",
        ]
        for pat in optimization_patterns:
            if re.search(pat, clean, re.IGNORECASE):
                score += 0.20
        return min(score, 1.0)
