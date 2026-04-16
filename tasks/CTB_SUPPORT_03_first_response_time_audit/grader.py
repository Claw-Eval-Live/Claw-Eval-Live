"""CTB_SUPPORT_03 grader -- first response time audit.

v2.2: Claw-Eval mode (data analysis report).
- Deterministic 40%: tool gate, per-ticket FRT, SLA stats, agent performance
- Judge 60%: data accuracy, analysis quality
- Fallback: keyword-based scoring when judge unavailable

Ground truth:
  FRT-001 (high): 10min PASS | FRT-002 (high): 55min FAIL
  FRT-003 (medium): 8min PASS | FRT-004 (high): 90min FAIL
  FRT-005 (high): 135min FAIL | FRT-006 (medium): 5min PASS
  SLA compliant: 3, breach: 3, rate: 50%
  agent_a: 100% SLA (best), agent_b: 0% (worst), agent_c: 0%
"""

from __future__ import annotations
import re
from typing import Any
from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class Grader(AbstractGrader):
    _DATA_RUBRIC = """\
Evaluate the accuracy of first response time data and SLA compliance (0.0-1.0).

## Ground Truth
- FRT-001 (high priority): 10 min -- PASS (SLA: 30 min)
- FRT-002 (high priority): 55 min -- FAIL
- FRT-003 (medium priority): 8 min -- PASS (SLA: 60 min)
- FRT-004 (high priority): 90 min -- FAIL
- FRT-005 (high priority): 135 min -- FAIL
- FRT-006 (medium priority): 5 min -- PASS
- Overall: 3 compliant, 3 breach, 50% compliance rate
- agent_a: 100% compliance (best), agent_b: 0% (worst), agent_c: 0%

## Scoring tiers
- 0.9-1.0: All 6 tickets with correct FRT and SLA status; overall stats correct; per-agent stats correct
- 0.7-0.8: 5+ tickets correct; overall stats present; agent identification
- 0.5-0.6: 3-4 tickets; partial stats
- 0.3-0.4: Few tickets analyzed
- 0.0-0.2: No meaningful data
"""

    _ANALYSIS_RUBRIC = """\
Evaluate the quality of the FRT audit analysis and recommendations (0.0-1.0).

## Expected elements
1. Per-ticket: ticket ID, priority, FRT, SLA pass/fail
2. Per-agent: average FRT, SLA compliance rate
3. Overall: total tickets, compliant count, breach count, rate (50%)
4. Best/worst performing agents identified
5. Improvement recommendations for underperforming agents

## Scoring tiers
- 0.9-1.0: Complete structured report; all statistics correct; clear agent ranking; actionable recommendations
- 0.7-0.8: Good structure; most stats correct; agents identified
- 0.5-0.6: Basic report; missing per-agent analysis
- 0.3-0.4: Incomplete
- 0.0-0.2: No meaningful report
"""

    TIMES = {"FRT-001": "10", "FRT-002": "55", "FRT-003": "8", "FRT-004": "90", "FRT-005": "135", "FRT-006": "5"}

    def grade(self, messages, dispatches, task, audit_data=None, judge=None, media_events=None, env_snapshot=None):
        scores = DimensionScores(safety=1.0)
        all_text = self._get_all_assistant_text(messages)
        clean = all_text.replace(",", "").replace(" ", "")

        tool_penalty = self._tool_gate(dispatches)

        det_score = 0.0
        # Per-ticket FRT
        ticket_hits = 0
        for tid, minutes in self.TIMES.items():
            if tid in all_text:
                region = self._get_region(all_text, tid)
                if region and re.search(rf'(?<!\d){re.escape(minutes)}(?!\d)', region):
                    ticket_hits += 1
                else:
                    ticket_hits += 0.4
        det_score += 0.35 * min(ticket_hits / 5, 1.0)

        # SLA stats
        sla = 0.0
        if re.search(r'3.*compliant|compliant.*3|3.*pass|met.*3', clean, re.IGNORECASE): sla += 0.33
        if re.search(r'3.*breach|breach.*3|3.*fail|3.*non.?compliant', clean, re.IGNORECASE): sla += 0.33
        if "50%" in all_text: sla += 0.34
        det_score += 0.30 * min(sla, 1.0)

        # Agent identification
        agent_score = 0.0
        a_region = self._get_region(all_text, "agent_a")
        if a_region and any(k in a_region.lower() for k in ["100%", "best", "top", "fully compliant"]): agent_score += 0.35
        b_region = self._get_region(all_text, "agent_b")
        if b_region and any(k in b_region.lower() for k in ["0%", "worst", "non-compliant", "slowest"]): agent_score += 0.35
        if "agent_c" in all_text: agent_score += 0.30
        det_score += 0.35 * min(agent_score, 1.0)

        # Cap without per-ticket data
        if sum(1 for t in self.TIMES if t in all_text) == 0:
            det_score = min(det_score, 0.3)

        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            data = judge.evaluate(task.prompt.text, conversation, actions, self._DATA_RUBRIC).score
            analysis = judge.evaluate(task.prompt.text, conversation, actions, self._ANALYSIS_RUBRIC).score
        else:
            data = self._fallback_data(all_text, clean)
            analysis = self._fallback_analysis(all_text)

        completion = tool_penalty * (0.40 * det_score + 0.35 * data + 0.25 * analysis)
        scores.completion = min(round(completion, 2), 1.0)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len([m for m in messages if m.message.role == "assistant"])
        return scores

    def _tool_gate(self, dispatches):
        hd = [d for d in dispatches if d.tool_name in ("helpdesk_list_tickets", "helpdesk_get_ticket") and d.response_status < 400]
        if not hd: return 0.2
        return 1.0 if len(hd) >= 3 else 0.5

    @staticmethod
    def _get_region(text, anchor, radius=300):
        if anchor in text:
            idx = text.index(anchor)
            return text[max(0, idx - 50):idx + radius]
        return ""

    def _fallback_data(self, text, clean):
        score = 0.0
        score += 0.30 * min(sum(1 for t in self.TIMES if t in text) / 5, 1.0)
        if "50%" in text: score += 0.20
        time_mentions = re.findall(r'\d+\s*(?:minutes?|min)', text, re.IGNORECASE)
        score += 0.30 * min(len(time_mentions) / 4, 1.0)
        if "agent_a" in text: score += 0.10
        if "agent_b" in text: score += 0.10
        return min(score, 1.0)

    def _fallback_analysis(self, text):
        score = 0.0
        if "|" in text or "##" in text: score += 0.25
        if any(k in text.lower() for k in ["best", "worst", "top performer"]): score += 0.25
        if any(k in text.lower() for k in ["recommend", "improv"]): score += 0.25
        if any(k in text.lower() for k in ["sla", "compliance"]): score += 0.25
        return min(score, 1.0)
