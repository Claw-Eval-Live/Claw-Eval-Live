"""CTB_SHELL_21 grader v2 — Queue Depth Monitor.

Upgrade: verify agent actions (dispatches + audit) AND output accuracy,
not just keyword presence.

Ground truth (from fixtures):
  - TK-701: order-queue深度78000(阈值5000), 消费者6→2(网络闪断), 缺connection-recovery
  - TK-702: payment-dlq死信15600(阈值100), 85%超时>30s TTL, 15% ClassNotFoundException v2.3.1
  - TK-703: notification-events lag=32000, partition-0 lag=25000, userId hash集中
  - TK-704: audit-queue消息丢失2300条(差异率4.4%), autoAck=true
  - note_701: 队列配置与监控标准, note_702: 3月变更记录
"""
from __future__ import annotations

import re
from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.trace import DimensionScores


EXPECTED_TICKETS = {"TK-701", "TK-702", "TK-703", "TK-704"}
EXPECTED_NOTES = {"note_701", "note_702"}

QUEUE_ISSUES = {
    "order_backlog": {
        "anchor": "order-queue",
        "anchor_alt": "order",
        "symptom_keywords": ["78000", "7\\.8万", "78,000"],
        "detail_keywords": ["消费者.*6.*2", "6个.*2个", "消费者.*断开", "consumer.*6.*2",
                            "consumer.*disconnect", "consumer.*drop", "网络.*闪断", "网络分区", "network.*partition",
                            "network.*blip", "network.*flap", "connection.*recovery"],
    },
    "payment_dlq": {
        "anchor": "payment-dlq",
        "anchor_alt": "payment",
        "symptom_keywords": ["15600", "死信.*15", "dead.letter.*15", "DLQ.*15"],
        "detail_keywords": ["85%.*超时", "85%.*timeout", "85%.*timed.*out", "TTL.*30秒", "TTL.*30s", "30秒", "30.sec",
                            "ClassNotFoundException", "序列化失败", "serialization.*fail",
                            "serialization.*error", "deserializ", "v2\\.3\\.1"],
    },
    "notification_skew": {
        "anchor": "notification",
        "anchor_alt": "notification-events",
        "symptom_keywords": ["32000", "3\\.2万", "32,000", "partition-0.*25000"],
        "detail_keywords": ["分区.*不均", "partition.*skew", "partition.*imbalanc", "uneven.*partition",
                            "userId.*hash", "热点分区", "hot.*partition", "hotspot", "hot.*key",
                            "头部.*用户", "top.*user", "heavy.*user", "增加分区", "add.*partition", "increase.*partition", "round.?robin"],
    },
    "audit_loss": {
        "anchor": "audit-queue",
        "anchor_alt": "audit",
        "symptom_keywords": ["2300", "消息丢失", "message.*loss", "message.*lost", "message.*missing", "差异.*4", "discrepancy.*4", "gap.*4"],
        "detail_keywords": ["autoAck.*true", "自动确认", "auto.*acknowledge", "auto.*ack",
                            "手动.*ack", "manual.*ack", "explicit.*ack"],
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

        completion += 0.15 * min(retrieval, 1.0)

        # ────────────────────────────────────────────────────────
        # 2. DIAGNOSTIC ACCURACY (0.50)
        # ────────────────────────────────────────────────────────
        diag_score = 0.0
        per_issue = 1.0 / len(QUEUE_ISSUES)

        for _name, issue in QUEUE_ISSUES.items():
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
            # Fallback
            if not symptom_found:
                symptom_found = any(
                    re.search(sk, clean) for sk in issue["symptom_keywords"]
                )

            # Detail/root cause
            detail_found = any(
                re.search(dk, clean, re.IGNORECASE) for dk in issue["detail_keywords"]
            )

            item_score = 0.0
            if symptom_found:
                item_score += 0.5
            if detail_found:
                item_score += 0.5

            diag_score += per_issue * item_score

        completion += 0.50 * min(diag_score, 1.0)

        # ────────────────────────────────────────────────────────
        # 3. RECOMMENDATION QUALITY (0.20)
        # ────────────────────────────────────────────────────────
        rec_score = 0.0
        rec_keywords = [
            r"恢复.*消费者|自动重连|connection.?recovery|restore.*consumer|auto.?reconnect|recover.*consumer|restart.*consumer",
            r"升级.*消费者|v2\.3\.1|统一.*版本|upgrade.*consumer|unif.*version|align.*version|consistent.*version",
            r"增加.*分区|round.?robin|重新分区|add.*partition|repartition|rebalanc|increase.*partition",
            r"手动.*ack|manual.*ack|关闭.*autoAck|disable.*autoAck|turn.*off.*autoAck|explicit.*ack",
        ]
        for rk in rec_keywords:
            if re.search(rk, clean, re.IGNORECASE):
                rec_score += 1.0 / len(rec_keywords)

        # Priority ordering
        if re.search(r"紧急|优先级|排序|urgent|priority|ranking|triage|severity", clean, re.IGNORECASE):
            rec_score = min(rec_score + 0.1, 1.0)

        completion += 0.20 * min(rec_score, 1.0)

        # ── Queue metrics bonus (0.15) ──
        q_kw = sum(1 for kw in ["队列", "queue", "深度", "depth", "阈值", "threshold", "消费", "consumer", "backlog", "lag"] if kw in clean.lower())
        completion += 0.15 * min(q_kw / 2, 1.0)

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
