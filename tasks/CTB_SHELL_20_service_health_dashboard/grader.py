"""CTB_SHELL_20 grader v2 — service health dashboard.

Upgrade: verify agent actions (dispatches + audit) AND output accuracy.

Ground truth (from fixtures):
  - TK-601: payment-service P99=4.2s (SLA<=3s), success rate 97.3% (SLA 99.5%)
  - TK-602: order-service error rate 5.1% (SLA<0.5%), slow queries 350 (threshold 50)
  - TK-603: notification-service lag=45000 (SLA<5000), consume rate 120/s (base 500)
  - TK-604: gateway-service TLS handshake 120ms (SLA<=20ms), session cache 32% (threshold 80%)
  - note_601: SLA standards for all 4 services
  - note_602: March change/deployment log
  All 4 services violate SLA → 0/4 health score
"""
from __future__ import annotations

import re
from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.trace import DimensionScores


REQUIRED_TICKETS = ["TK-601", "TK-602", "TK-603", "TK-604"]
REQUIRED_NOTES = ["note_601", "note_602"]

SERVICE_ISSUES = {
    "payment": {
        "service": "payment-service",
        "metrics": ["4.2", "97.3"],
        "sla_refs": ["3秒", "3s", "99.5%"],
        "cause_keywords": ["第三方.*网关.*升级", "支付网关.*延迟", "third.party.*gateway.*upgrade", "payment.*gateway.*latency", "payment.*gateway.*delay", "external.*gateway.*upgrade", "v3.0"],
    },
    "order": {
        "service": "order-service",
        "metrics": ["5.1%", "350"],
        "sla_refs": ["0.5%", "50条", "50 queries", "50 slow"],
        "cause_keywords": ["缺少索引", "索引.*缺失", "全表扫描", "未优化", "missing.*index", "index.*missing", "full.*table.*scan", "not.*optimiz", "no.*index", "table.*scan", "unoptimiz"],
    },
    "notification": {
        "service": "notification",
        "metrics": ["45000", "120"],
        "sla_refs": ["5000", "500条", "500/s", "500 messages"],
        "cause_keywords": ["GC.*暂停", "GC.*pause", "GC.*stop.*the.*world", "session.*timeout", "被踢出", "kicked.*out", "evicted", "rebalance"],
    },
    "gateway": {
        "service": "gateway",
        "metrics": ["120.*ms", "32%"],
        "sla_refs": ["20ms", "80%"],
        "cause_keywords": ["session.*ticket", "证书.*更新", "certificate.*update", "certificate.*renewal", "cert.*renew", "TLS.*会话.*缓存", "TLS.*session.*cache", "TLS.*session.*reuse"],
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
        read_tickets = set()
        for d in dispatches:
            if d.tool_name == "helpdesk_get_ticket" and d.response_status < 400:
                body = d.request_body if isinstance(d.request_body, dict) else {}
                tid = body.get("ticket_id", "")
                if tid in REQUIRED_TICKETS:
                    read_tickets.add(tid)

        read_notes = set()
        for d in dispatches:
            if d.tool_name == "notes_get_document" and d.response_status < 400:
                body = d.request_body if isinstance(d.request_body, dict) else {}
                nid = body.get("note_id", "")
                if nid in REQUIRED_NOTES:
                    read_notes.add(nid)

        hd_calls = [d for d in dispatches
                    if d.tool_name in ("helpdesk_list_tickets", "helpdesk_get_ticket")
                    and d.response_status < 400]
        nd_calls = [d for d in dispatches
                    if d.tool_name in ("notes_list_documents", "notes_get_document")
                    and d.response_status < 400]

        audit_verified = False
        if audit_data:
            hd_audit = audit_data.get("helpdesk", {}).get("calls", [])
            nd_audit = audit_data.get("notes", {}).get("calls", [])
            audit_verified = len(hd_audit) >= 1 and len(nd_audit) >= 1

        retrieval = 0.5 * len(read_tickets) / len(REQUIRED_TICKETS) + \
                    0.3 * len(read_notes) / len(REQUIRED_NOTES)
        if audit_verified:
            retrieval += 0.2
        elif hd_calls and nd_calls:
            retrieval += 0.1

        completion += 0.15 * min(retrieval, 1.0)

        # ────────────────────────────────────────────────────────
        # 2. DIAGNOSTIC ACCURACY (0.50)
        # ────────────────────────────────────────────────────────
        diag_score = 0.0
        per_svc = 1.0 / len(SERVICE_ISSUES)

        for key, svc in SERVICE_ISSUES.items():
            service = svc["service"]

            # Service mentioned
            service_found = bool(re.search(re.escape(service), clean, re.IGNORECASE))
            if not service_found:
                continue

            # Current metrics mentioned near service
            metrics_found = sum(
                1 for m in svc["metrics"]
                if self._near_context(all_text, service, [m], 300) or re.search(m, clean)
            )
            metric_score = min(metrics_found / max(len(svc["metrics"]), 1), 1.0)

            # SLA comparison
            sla_found = any(re.search(s, clean) for s in svc["sla_refs"])

            # Root cause
            cause_found = any(re.search(ck, clean, re.IGNORECASE) for ck in svc["cause_keywords"])

            item_score = 0.0
            if service_found:
                item_score += 0.15
            item_score += 0.35 * metric_score
            if sla_found:
                item_score += 0.20
            if cause_found:
                item_score += 0.30

            diag_score += per_svc * item_score

        completion += 0.50 * min(diag_score, 1.0)

        # ────────────────────────────────────────────────────────
        # 3. RECOMMENDATION QUALITY (0.15)
        # ────────────────────────────────────────────────────────
        rec_score = 0.0

        # Overall health score
        if re.search(r"0/4|0.*达标|0.*compliant|0.*pass|全部.*违规|all.*violat|all.*breach|all.*fail|4.*服务.*违|4.*service.*violat|none.*compliant", clean, re.IGNORECASE):
            rec_score += 0.30

        # Priority ordering
        if re.search(r"优先级|priorit|排序|ranking|triage|order-service.*最|order-service.*highest|order-service.*worst|错误率.*最高|error.*rate.*highest", clean, re.IGNORECASE):
            rec_score += 0.30

        # Specific recommendations
        if re.search(r"索引|index|优化.*SQL|optimize.*SQL|添加.*索引|add.*index|create.*index", clean, re.IGNORECASE):
            rec_score += 0.20
        if re.search(r"超时.*配置|timeout.*config|timeout.*setting|调整.*timeout|adjust.*timeout|session.*ticket|GC.*调优|GC.*tun", clean, re.IGNORECASE):
            rec_score += 0.20

        completion += 0.15 * min(rec_score, 1.0)

        # ── Overall health score bonus (0.20) ──
        health_kw = sum(1 for kw in ["SLA", "健康", "health", "healthy", "仪表盘", "dashboard", "状态", "status", "compliance"] if kw.lower() in clean.lower())
        completion += 0.20 * min(health_kw / 2, 1.0)

        scores.completion = round(min(completion, 1.0), 2)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len(
            [m for m in messages if m.message.role == "assistant"]
        )
        return scores

    @staticmethod
    def _near_context(text: str, anchor: str, patterns: list[str], distance: int = 200) -> bool:
        anchor_positions = [m.start() for m in re.finditer(re.escape(anchor), text, re.IGNORECASE)]
        for pat in patterns:
            kw_positions = [m.start() for m in re.finditer(pat, text, re.IGNORECASE)]
            for ap in anchor_positions:
                for kp in kw_positions:
                    if abs(ap - kp) < distance:
                        return True
        return False
