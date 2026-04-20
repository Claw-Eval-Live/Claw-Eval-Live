"""CTB_DATA_09 grader -- ticket/email/CRM three-way reconciliation.

v2.2: hybrid deterministic + judge scoring.
- Deterministic: tool gate (tickets + emails + CRM), discrepancy IDs, VIP flag
- Judge: reconciliation accuracy, action plan quality
- Fallback: keyword-based scoring when judge unavailable

Ground truth:
  1. Alpha(TK-501): email mentions new issue (order search) -> no ticket -> create
  2. Beta(TK-502): email says resolved -> ticket still in_progress -> close
  3. Zeta(msg_1603): email complaint -> no ticket + no CRM record -> create both
  4. VIP reminder: Alpha(TK-501), Gamma(TK-503), Epsilon(TK-505) have open tickets
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class TicketEmailCRMReconcileGrader(AbstractGrader):

    # -- judge rubrics --

    _DATA_RUBRIC = """\
Evaluate the accuracy of ticket-email-CRM reconciliation findings (0.0-1.0).

## Ground Truth
### Discrepancy 1: Alpha / TK-501
- Alpha's email mentions a NEW issue (order search problem) that has no matching ticket.
- A new ticket needs to be created for this issue.

### Discrepancy 2: Beta / TK-502
- Beta's email says the problem is self-resolved.
- But ticket TK-502 is still in_progress status.
- Ticket should be closed/updated.

### Discrepancy 3: Zeta / msg_1603
- Zeta sent a complaint email about data sync issues.
- No matching ticket exists in helpdesk.
- Zeta does NOT exist in CRM at all.
- Both a new ticket and a new CRM record need to be created.

### VIP Priority
- VIP customers with open tickets: Alpha(TK-501), Gamma(TK-503), Epsilon(TK-505)

## Scoring tiers
- 0.9-1.0: All 3 discrepancies correct; VIP customers and CRM gaps correctly identified
- 0.7-0.8: 2-3 discrepancies found; VIP/CRM gaps mostly correct
- 0.5-0.6: 1-2 discrepancies found
- 0.3-0.4: Only surface-level comparison
- 0.0-0.2: No meaningful reconciliation
"""

    _REPORT_RUBRIC = """\
Evaluate the quality of the reconciliation action plan (0.0-1.0).

## Expected elements
1. Per-discrepancy next-action list
2. New ticket creation actions (for Alpha's new issue and Zeta)
3. Ticket closure/update action (Beta's TK-502)
4. CRM onboarding / follow-up action for Zeta
5. Priority handling recommendations for VIP open issues

## Scoring tiers
- 0.9-1.0: All discrepancies mapped to specific next actions; priorities are clear; follow-up steps are actionable
- 0.7-0.8: Most actions present; some prioritization
- 0.5-0.6: Partial report
- 0.3-0.4: Minimal structure
- 0.0-0.2: No meaningful report
"""

    def grade(self, messages, dispatches, task, audit_data=None, judge=None, media_events=None, env_snapshot=None):
        scores = DimensionScores()
        scores.safety = 1.0
        final_text = self._get_final_assistant_text(messages)
        clean = final_text.replace(",", "").replace("\uff0c", "")

        # 1. Tool gate
        tool_penalty = self._tool_gate(dispatches)

        # 2. Deterministic (30%)
        det_score = self._score_deterministic(clean, final_text)

        # 3. Judge (70%)
        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            data_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._DATA_RUBRIC
            ).score
            report_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._REPORT_RUBRIC
            ).score
        else:
            data_score = self._fallback_data(clean, final_text)
            report_score = self._fallback_report(final_text)

        # 4. Combine
        completion = tool_penalty * (
            0.30 * det_score
            + 0.35 * data_score
            + 0.35 * report_score
        )

        scores.completion = round(min(completion, 1.0), 2)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len([m for m in messages if m.message.role == "assistant"])
        return scores

    # -- helpers --

    @staticmethod
    def _has_bounded(text: str, num: str) -> bool:
        return bool(re.search(rf'(?<!\d){re.escape(num)}(?!\d)', text))

    def _tool_gate(self, dispatches):
        tk = any(d.tool_name == "helpdesk_list_tickets" and d.response_status < 400
                 for d in dispatches)
        email_calls = [d for d in dispatches
                       if d.tool_name in ("gmail_list_messages", "gmail_get_message")
                       and d.response_status < 400]
        crm = any(d.tool_name in ("crm_list_customers", "crm_search_customer")
                  and d.response_status < 400 for d in dispatches)

        sources = sum([tk, bool(email_calls), crm])
        if sources == 3:
            return 1.0
        if sources == 2:
            return 0.6
        if sources == 1:
            return 0.3
        return 0.15

    def _score_deterministic(self, clean, final_text):
        """4 dimensions: Alpha new issue, Beta close, Zeta missing, VIP flag."""
        hits = 0
        total = 4

        # D1: Alpha new issue (order search) needs new ticket
        if any(k in final_text for k in ["Alpha", "张伟"]):
            region = self._region_around(final_text, ["Alpha", "张伟"], 300)
            if region and any(kw in region for kw in [
                "order search", "search function", "new issue", "create", "new ticket",
                "missing", "no ticket",
                "订单搜索", "搜索功能", "新问题", "新建", "创建工单",
                "缺", "遗漏", "无工单"
            ]):
                hits += 1
            else:
                hits += 0.3

        # D2: Beta / TK-502 should close
        if any(k in final_text for k in ["Beta", "李明", "TK-502"]):
            region = self._region_around(final_text, ["Beta", "李明", "TK-502"], 300)
            if region and any(kw in region for kw in [
                "resolved", "self-resolved", "close", "update status",
                "已解决", "自行解决", "关闭", "更新状态"
            ]):
                hits += 1
            else:
                hits += 0.3

        # D3: Zeta not in CRM + no ticket
        if any(k in final_text for k in ["Zeta", "孙丽", "zeta"]):
            has_no_crm = any(kw in final_text for kw in [
                "not exist", "no record", "unknown customer", "not in CRM",
                "missing from CRM", "new customer",
                "不存在", "无记录", "未知客户", "CRM中没有",
                "CRM 中没有", "新客户"
            ])
            has_no_ticket = any(kw in final_text for kw in [
                "create ticket", "new ticket", "no ticket",
                "创建工单", "新建工单", "无工单"
            ])
            if has_no_crm and has_no_ticket:
                hits += 1
            elif has_no_crm or has_no_ticket:
                hits += 0.5

        # D4: VIP flag with specific customers
        if "VIP" in final_text:
            vip_names = sum(1 for n in ["Alpha", "Gamma", "Epsilon"] if n in final_text)
            if vip_names >= 2:
                hits += 1
            elif vip_names >= 1:
                hits += 0.5

        return min(hits / total, 1.0)

    def _region_around(self, text, keywords, radius):
        for kw in keywords:
            if kw in text:
                idx = text.index(kw)
                return text[max(0, idx - radius):idx + radius]
        return None

    # -- fallbacks (dev-only) --

    def _fallback_data(self, clean, all_text):
        score = 0.0
        if any(k in all_text for k in ["Alpha", "张伟"]) and \
           any(k in all_text for k in ["order search", "new issue", "create", "订单搜索", "新问题", "新建"]):
            score += 0.3
        if any(k in all_text for k in ["Beta", "TK-502"]) and \
           any(k in all_text for k in ["resolved", "close", "已解决", "关闭"]):
            score += 0.25
        if any(k in all_text for k in ["Zeta", "孙丽"]) and \
           any(k in all_text for k in ["not exist", "unknown", "no record", "不存在", "未知客户", "无记录"]):
            score += 0.3
        if "VIP" in all_text:
            score += 0.15
        return min(score, 1.0)

    def _fallback_report(self, all_text):
        score = 0.0
        if any(k in all_text for k in ["new ticket", "create ticket", "新建工单", "创建工单"]):
            score += 0.25
        if any(k in all_text for k in ["close", "update status", "关闭", "更新状态"]):
            score += 0.2
        if "VIP" in all_text and any(k in all_text for k in ["priority", "urgent", "优先", "紧急"]):
            score += 0.2
        if "|" in all_text and "---" in all_text:
            score += 0.2
        if len(all_text) > 400:
            score += 0.15
        return min(score, 1.0)
