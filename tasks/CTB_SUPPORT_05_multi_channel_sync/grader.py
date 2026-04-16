"""CTB_SUPPORT_05 grader -- multi-channel ticket sync.

v2.2: WildClawBench mode (operation + analysis).
- Deterministic 55%: tool gate, linked pairs, unlinked emails, stats
- Judge 45%: sync analysis quality, action recommendations
- Fallback: keyword-based scoring when judge unavailable

Ground truth:
  Linked: msg_1001<->MC-001 (zhangsan/login), msg_1003<->MC-002 (liuqi/report), msg_1004<->MC-003 (chenba/mobile)
  Unlinked: msg_1002 (wangwu/data import), msg_1005 (zhaojiu/API timeout)
  Stats: 3 linked, 2 unlinked
"""

from __future__ import annotations
import re
from typing import Any
from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class Grader(AbstractGrader):
    _SYNC_RUBRIC = """\
Evaluate the accuracy of email-ticket sync analysis (0.0-1.0).

## Ground Truth
- LINKED (3 pairs):
  1. Email msg_1001 (zhangsan, Stellar, login locked) <-> Ticket MC-001
  2. Email msg_1003 (liuqi, Grand, Q1 report) <-> Ticket MC-002
  3. Email msg_1004 (chenba, Vision, mobile crash) <-> Ticket MC-003
- UNLINKED (2 missed emails):
  1. msg_1002 (wangwu, Evergreen, data import failure) -- no ticket created
  2. msg_1005 (zhaojiu, Smart Link, API timeout) -- no ticket created
- Statistics: 3 linked, 2 unlinked

## Scoring tiers
- 0.9-1.0: All linked pairs correctly mapped; both unlinked emails identified with urgency; stats correct
- 0.7-0.8: Most pairs correct; at least 1 unlinked identified; stats present
- 0.5-0.6: Some pairs identified; partial unlinked detection
- 0.3-0.4: Few mappings
- 0.0-0.2: No meaningful sync analysis
"""

    _ACTION_RUBRIC = """\
Evaluate the quality of follow-up action recommendations (0.0-1.0).

## Expected elements
1. Urgency assessment for unlinked emails
2. Recommendation to create tickets for missed complaints
3. Sync process improvement suggestions
4. Clear linked/unlinked listing format

## Scoring tiers
- 0.9-1.0: Urgency assessed; specific ticket creation recommended; process improvements; clear format
- 0.7-0.8: Some urgency assessment; ticket creation suggested
- 0.5-0.6: Basic recommendations
- 0.3-0.4: Minimal recommendations
- 0.0-0.2: No recommendations
"""

    def grade(self, messages, dispatches, task, audit_data=None, judge=None, media_events=None, env_snapshot=None):
        scores = DimensionScores(safety=1.0)
        all_text = self._get_all_assistant_text(messages)
        lower = all_text.lower()
        clean = all_text.replace(",", "")

        tool_penalty = self._tool_gate(dispatches)

        det_score = 0.0
        # Linked pairs
        linked_pairs = [
            (["zhangsan", "Stellar", "login", "locked"], "MC-001"),
            (["liuqi", "Grand", "report", "Q1"], "MC-002"),
            (["chenba", "Vision", "mobile", "crash"], "MC-003"),
        ]
        linked = 0
        for email_kw, ticket_id in linked_pairs:
            email_found = any(k.lower() in lower for k in email_kw)
            ticket_found = ticket_id in all_text
            if email_found and ticket_found: linked += 1
            elif email_found or ticket_found: linked += 0.4
        det_score += 0.25 * min(linked / 3, 1.0)

        # Unlinked emails
        unlinked_markers = ["unlinked", "missed", "no ticket", "missing", "not synced", "not created"]
        unlinked_checks = [
            ["wangwu", "Wang Wu", "Evergreen", "data import"],
            ["zhaojiu", "Zhao Jiu", "Smart Link", "API timeout"],
        ]
        unlinked = 0
        for kw_list in unlinked_checks:
            found_kw = None
            for kw in kw_list:
                if kw.lower() in lower:
                    found_kw = kw
                    break
            if found_kw:
                idx = lower.index(found_kw.lower())
                region = all_text[max(0, idx - 100):idx + 400].lower()
                if any(m in region for m in unlinked_markers): unlinked += 1
                else: unlinked += 0.4
        det_score += 0.25 * min(unlinked / 2, 1.0)

        # Stats
        stats = 0.0
        if re.search(r'3.*linked|linked.*3', clean, re.IGNORECASE): stats += 0.5
        if re.search(r'2.*unlinked|unlinked.*2|2.*missed|missed.*2', clean, re.IGNORECASE): stats += 0.5
        det_score += 0.20 * min(stats, 1.0)

        # Urgency and recommendations
        urgency_kw = ["urgent", "priority", "recommend creating", "should create"]
        det_score += 0.15 * min(sum(1 for k in urgency_kw if k in lower) / 2, 1.0)
        map_kw = ["correspond", "linked", "mapped", "email", "ticket"]
        det_score += 0.15 * min(sum(1 for k in map_kw if k in lower) / 2, 1.0)

        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            sync = judge.evaluate(task.prompt.text, conversation, actions, self._SYNC_RUBRIC).score
            action = judge.evaluate(task.prompt.text, conversation, actions, self._ACTION_RUBRIC).score
        else:
            sync = self._fallback_sync(lower, clean)
            action = self._fallback_action(lower)

        completion = tool_penalty * (0.55 * det_score + 0.25 * sync + 0.20 * action)
        scores.completion = min(round(completion, 2), 1.0)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len([m for m in messages if m.message.role == "assistant"])
        return scores

    def _tool_gate(self, dispatches):
        gm = any(d.tool_name in ("gmail_list_messages", "gmail_get_message") and d.response_status < 400 for d in dispatches)
        hd = any(d.tool_name in ("helpdesk_list_tickets", "helpdesk_get_ticket") and d.response_status < 400 for d in dispatches)
        if not gm and not hd: return 0.2
        if not gm or not hd: return 0.5
        return 1.0

    def _fallback_sync(self, lower, clean):
        score = 0.0
        if "mc-001" in lower: score += 0.10
        if "mc-002" in lower: score += 0.10
        if "mc-003" in lower: score += 0.10
        if any(k in lower for k in ["wangwu", "data import"]): score += 0.15
        if any(k in lower for k in ["zhaojiu", "api timeout"]): score += 0.15
        if "3" in clean and "linked" in lower: score += 0.10
        if "2" in clean and "unlinked" in lower: score += 0.10
        if any(k in lower for k in ["correspond", "mapped"]): score += 0.20
        return min(score, 1.0)

    def _fallback_action(self, lower):
        score = 0.0
        if any(k in lower for k in ["urgent", "priority"]): score += 0.25
        if any(k in lower for k in ["recommend", "follow-up", "create ticket"]): score += 0.35
        if any(k in lower for k in ["action", "measure"]): score += 0.20
        if "|" in lower or "##" in lower: score += 0.20
        return min(score, 1.0)
