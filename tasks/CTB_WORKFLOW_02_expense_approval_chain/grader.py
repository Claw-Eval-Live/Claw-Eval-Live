"""CTB_WORKFLOW_02 grader -- expense approval chain.

v2.2: WildClawBench mode (operation workflow).
- Deterministic 60%: tool gate, expense identification, approval decisions, todo creation
- Judge 40%: expense analysis quality
- Fallback: keyword-based scoring when judge unavailable

Ground truth:
  Zhao Qiang travel 4510 yuan: hotel 580 over but client-designated -> approve
  Figma 32000 yuan: VP only verbal approval, needs written -> hold/not approved
  Server expansion 48000 yuan: tech director approved, DingChuang 420K revenue -> approve
  Exhibition 39000 yuan: within budget + VP approved -> approve
  Action: create todo for Figma VP follow-up
"""

from __future__ import annotations
import re
from typing import Any
from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class Grader(AbstractGrader):
    _EXPENSE_RUBRIC = """\
Evaluate the accuracy of expense review and approval decisions (0.0-1.0).

## Ground Truth
1. Zhao Qiang Travel (4,510 yuan): Hotel exceeds limit by 580, but client-designated hotel -> APPROVE with justification
2. Figma Subscription (32,000 yuan): VP gave only verbal approval, policy requires written -> HOLD / NOT APPROVED until written confirmation
3. Server Expansion (48,000 yuan): Tech director approved, supports DingChuang customer (420K revenue) -> APPROVE
4. Exhibition (39,000 yuan): Within budget, VP already approved -> APPROVE
5. Follow-up action: Create todo task to get VP written approval for Figma

## Scoring tiers
- 0.9-1.0: All 4 expenses correctly evaluated; Figma held for VP written approval; todo created; clear justifications
- 0.7-0.8: 3-4 expenses correct; Figma issue identified; some todos
- 0.5-0.6: 2-3 expenses; basic decisions
- 0.3-0.4: Partial evaluation
- 0.0-0.2: No meaningful evaluation
"""

    def grade(self, messages, dispatches, task, audit_data=None, judge=None, media_events=None, env_snapshot=None):
        scores = DimensionScores(safety=1.0)
        all_text = self._get_all_assistant_text(messages)
        clean = all_text.replace(",", "").replace("\uff0c", "")
        lower = all_text.lower()

        svc = set()
        for d in dispatches:
            if d.response_status < 400:
                for s in ["gmail", "finance", "todo"]:
                    if s in d.tool_name: svc.add(s)
        tool_penalty = 1.0 if len(svc) >= 2 else (0.5 if len(svc) >= 1 else 0.2)

        det_score = 0.0

        # Zhao Qiang travel
        zq_region = self._get_region(all_text, ["\u8d75\u5f3a", "Zhao Qiang"])
        if zq_region:
            zq = 0.2
            if "4510" in zq_region.replace(",", ""): zq += 0.2
            if any(k in zq_region.lower() for k in ["\u8d85\u6807", "exceed", "580"]): zq += 0.3
            if any(k in zq_region.lower() for k in ["\u6279\u51c6", "approv"]): zq += 0.3
            det_score += 0.15 * min(zq, 1.0)

        # Figma
        fg_region = self._get_region(all_text, ["Figma"])
        if fg_region:
            fg = 0.2
            if "32000" in fg_region.replace(",", ""): fg += 0.15
            if "VP" in fg_region and any(k in fg_region.lower() for k in ["\u53e3\u5934", "\u4e66\u9762", "verbal", "written"]): fg += 0.35
            if any(k in fg_region.lower() for k in ["\u4e0d\u6279\u51c6", "\u6682\u4e0d", "hold", "not approv", "pending"]): fg += 0.30
            det_score += 0.15 * min(fg, 1.0)

        # Server + Exhibition
        if any(k in lower for k in ["\u670d\u52a1\u5668", "server"]) and "48000" in clean:
            det_score += 0.08
        if any(k in lower for k in ["\u5c55\u4f1a", "exhibition"]) and "39000" in clean:
            det_score += 0.07

        # Todo creation for Figma VP follow-up
        todo_creates = [d for d in dispatches if d.tool_name == "todo_create_task" and d.response_status < 400]
        if todo_creates:
            has_figma = any("Figma" in str(d.request_body) or "figma" in str(d.request_body).lower() or "VP" in str(d.request_body) for d in todo_creates)
            det_score += 0.15 * (1.0 if has_figma else 0.4)

        # Approval decision clarity
        decision_kw = ["\u6279\u51c6", "\u4e0d\u6279\u51c6", "\u901a\u8fc7", "\u6682\u4e0d", "approved", "not approved", "hold", "pending"]
        det_score += 0.15 * min(sum(1 for k in decision_kw if k in all_text) / 3, 1.0)

        # Summary
        amounts = sum(1 for a in ["4510", "32000", "48000", "39000"] if a in clean)
        det_score += 0.10 * min(amounts / 3, 1.0)
        if any(k in lower for k in ["\u6c47\u603b", "summary", "total"]): det_score += 0.05
        if any(k in lower for k in ["\u6279\u51c6", "approv"]) and any(k in lower for k in ["\u5f85", "pending"]): det_score += 0.10

        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            expense = judge.evaluate(task.prompt.text, conversation, actions, self._EXPENSE_RUBRIC).score
        else:
            expense = self._fallback(all_text, lower, clean)

        completion = tool_penalty * (0.60 * det_score + 0.40 * expense)
        scores.completion = min(round(completion, 2), 1.0)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len([m for m in messages if m.message.role == "assistant"])
        return scores

    @staticmethod
    def _get_region(text, anchors, radius=400):
        for a in anchors:
            if a in text:
                idx = text.index(a)
                return text[max(0, idx - 80):idx + radius]
        return ""

    def _fallback(self, text, lower, clean):
        """_fallback_: dev-only keyword scoring for expense review."""
        score = 0.0
        if "Figma" in text and "VP" in text: score += 0.25
        if "\u8d75\u5f3a" in text or "Zhao Qiang" in text: score += 0.15
        amounts = sum(1 for a in ["4510", "32000", "48000", "39000"] if a in clean)
        score += 0.30 * min(amounts / 3, 1.0)
        if any(k in lower for k in ["\u6279\u51c6", "approv"]): score += 0.15
        if any(k in lower for k in ["\u4e0d\u6279\u51c6", "hold", "pending"]): score += 0.15
        return min(score, 1.0)
