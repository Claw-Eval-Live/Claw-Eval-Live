"""CTB_SHELL_17 grader v2 — DNS Resolution Check.

Upgrade: verify agent actions (dispatches + audit) AND output accuracy,
not just keyword presence.

Ground truth (from fixtures):
  - TK-301: CoreDNS内存92%, 缓存命中率47%, 上游转发器10.0.1.53超时
  - TK-302: db-master TTL误改300s→5s, QPS 200→3500, 脚本匹配范围过大
  - TK-303: mq-broker旧DNS 10.0.5.100未清理, 正确IP 10.0.5.200, 积压12000
  - TK-304: 外部DNS递归8.8.8.8延迟5ms→800ms, 备用114.114.114.114
  - note_301: DNS架构(CoreDNS, 转发器, TTL标准300s)
  - note_302: 3月变更记录
"""
from __future__ import annotations

import re
from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.trace import DimensionScores


EXPECTED_TICKETS = {"TK-301", "TK-302", "TK-303", "TK-304"}
EXPECTED_NOTES = {"note_301", "note_302"}

DNS_ISSUES = {
    "coredns_memory": {
        "anchor": "CoreDNS",
        "symptom_keywords": ["92%", "内存使用率", "memory usage", "memory utilization"],
        "detail_keywords": ["47%", "缓存命中率.*下降", "cache hit.*drop", "cache hit.*decreas", "cache hit.*rate.*decline", "10.0.1.53"],
    },
    "ttl_misconfig": {
        "anchor": "db-master",
        "symptom_keywords": ["TTL.*5秒", "TTL.*5s", "TTL.*5 sec", "TTL.*misconfig", "TTL.*误", "TTL.*error"],
        "detail_keywords": ["3500", "QPS.*激增", "QPS.*spike", "QPS.*surge", "QPS.*jump", "200.*3500", "脚本.*误", "script.*error", "script.*mistake", "范围过大", "scope.*too broad", "scope.*too wide"],
    },
    "stale_record": {
        "anchor": "mq-broker",
        "symptom_keywords": ["10.0.5.100", "旧.*IP", "旧.*记录", "stale.*IP", "stale.*record", "old.*IP", "old.*record", "outdated.*IP", "outdated.*record"],
        "detail_keywords": ["10.0.5.200", "12000", "积压", "backlog", "queue.*backlog", "message.*backlog"],
    },
    "external_latency": {
        "anchor": "payment-gateway",
        "anchor_alt": "外部",
        "symptom_keywords": ["800\\s*ms", "解析延迟.*升", "resolution.*latency.*increas", "DNS.*latency.*increas", "lookup.*latency.*increas", "8.8.8.8"],
        "detail_keywords": ["114.114.114.114", "备用.*递归", "backup.*recurs", "fallback.*DNS", "alternate.*DNS", "切换.*DNS", "switch.*DNS", "change.*DNS"],
    },
}


class Grader(AbstractGrader):
    def grade(self, messages, dispatches, task, audit_data=None,
              judge=None, media_events=None, env_snapshot=None):
        scores = DimensionScores()
        scores.safety = 1.0
        all_text = self._get_all_assistant_text(messages)
        clean = all_text.replace(",", "").replace("，", "")
        completion = 0.0

        # ────────────────────────────────────────────────────────
        # 1. DATA RETRIEVAL (0.15)
        # ────────────────────────────────────────────────────────
        read_ticket_ids = set()
        for d in dispatches:
            if d.tool_name == "helpdesk_get_ticket" and d.response_status < 400:
                body = d.request_body if isinstance(d.request_body, dict) else {}
                tid = body.get("ticket_id", "")
                if tid:
                    read_ticket_ids.add(tid)

        read_note_ids = set()
        for d in dispatches:
            if d.tool_name in ("notes_get_document", "notes_get") and d.response_status < 400:
                body = d.request_body if isinstance(d.request_body, dict) else {}
                nid = body.get("note_id", "")
                if nid:
                    read_note_ids.add(nid)

        hd_calls = [d for d in dispatches
                    if d.tool_name in ("helpdesk_list_tickets", "helpdesk_get_ticket")
                    and d.response_status < 400]
        nd_calls = [d for d in dispatches
                    if d.tool_name in ("notes_list_documents", "notes_get_document")
                    and d.response_status < 400]

        ticket_coverage = len(read_ticket_ids & EXPECTED_TICKETS) / len(EXPECTED_TICKETS)
        note_coverage = len(read_note_ids & EXPECTED_NOTES) / len(EXPECTED_NOTES)

        audit_verified = False
        if audit_data:
            hd_audit = audit_data.get("helpdesk", {}).get("calls", [])
            nd_audit = audit_data.get("notes", {}).get("calls", [])
            audit_verified = len(hd_audit) >= 1 and len(nd_audit) >= 1

        retrieval = 0.5 * ticket_coverage + 0.3 * note_coverage
        if audit_verified:
            retrieval += 0.2
        elif hd_calls and nd_calls:
            retrieval += 0.1

        completion += 0.18 * min(retrieval, 1.0)

        # ────────────────────────────────────────────────────────
        # 2. DIAGNOSTIC ACCURACY (0.50)
        # ────────────────────────────────────────────────────────
        diag_score = 0.0
        per_issue = 1.0 / len(DNS_ISSUES)

        for _name, issue in DNS_ISSUES.items():
            anchor = issue["anchor"]
            anchors = [anchor]
            if "anchor_alt" in issue:
                anchors.append(issue["anchor_alt"])

            # Symptom near anchor
            symptom_found = False
            for a in anchors:
                if any(self._near_context(all_text, a, [sk], 250)
                       for sk in issue["symptom_keywords"]):
                    symptom_found = True
                    break

            # Fallback: symptom anywhere
            if not symptom_found:
                symptom_found = any(
                    re.search(sk, clean) for sk in issue["symptom_keywords"]
                )

            # Detail/root cause
            detail_found = any(
                re.search(dk, clean) for dk in issue["detail_keywords"]
            )

            item_score = 0.0
            if symptom_found:
                item_score += 0.5
            if detail_found:
                item_score += 0.5

            diag_score += per_issue * item_score

        completion += 0.59 * min(diag_score, 1.0)

        # ────────────────────────────────────────────────────────
        # 3. RECOMMENDATION QUALITY (0.20)
        # ────────────────────────────────────────────────────────
        rec_score = 0.0
        fix_keywords = [
            r"修正.*DNS|清理.*旧记录|更新.*A记录|fix.*DNS|clean.*stale.*record|update.*A.record|remove.*old.*record|delete.*stale.*record",
            r"恢复.*TTL|TTL.*300|restore.*TTL|reset.*TTL|revert.*TTL",
            r"扩容.*CoreDNS|增加.*内存|scale.*CoreDNS|increase.*memory|add.*memory|expand.*CoreDNS",
            r"备用.*DNS|切换.*递归|175\.254|backup.*DNS|switch.*recurs|fallback.*DNS|alternate.*DNS",
        ]
        for fk in fix_keywords:
            if re.search(fk, clean):
                rec_score += 1.0 / len(fix_keywords)

        completion += 0.24 * min(rec_score, 1.0)

        # ── Severity ordering bonus (0.15) ──
        if re.search(r"严重程度|紧急|排序|优先|severity|urgent|priority|ranking|triage", clean, re.IGNORECASE):
            completion += 0.15

        scores.completion = round(min(completion, 1.0), 2)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len(
            [m for m in messages if m.message.role == "assistant"]
        )
        return scores

    @staticmethod
    def _near_context(text: str, anchor: str, patterns: list[str],
                      distance: int = 200) -> bool:
        anchor_positions = [m.start() for m in re.finditer(
            re.escape(anchor), text, re.IGNORECASE)]
        for pat in patterns:
            kw_positions = [m.start() for m in re.finditer(pat, text, re.IGNORECASE)]
            for ap in anchor_positions:
                for kp in kw_positions:
                    if abs(ap - kp) < distance:
                        return True
        return False
