"""CTB_SHELL_01 grader v2 — log error diagnosis.

Upgrade: verify agent actions (dispatches + audit) AND output accuracy,
not just keyword presence.

Ground truth (from fixtures):
  - TK-101: payment-service OOM, Xmx512m, prod-node-03
  - TK-102: order-service connection timeout, maxPoolSize=10, db-master-01, 450/500
  - TK-103: payment-service NullPointerException, Third-party Payment GatewayAPIUpgrade
  - note_101: payment-service config (Xmx512m, prod-node-03)
  - note_102: order-service config (maxPoolSize=10)
"""
from __future__ import annotations

import re
from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.trace import DimensionScores


# ── Ground truth derived from fixtures ──────────────────────────────
REQUIRED_TICKETS = ["TK-101", "TK-102", "TK-103"]
REQUIRED_NOTES = ["note_101", "note_102"]

DIAGNOSTICS = {
    "OOM": {
        "service": "payment-service",
        "keywords": ["OOM", "OutOfMemory", "memoryexit", "Heapmemory"],
        "numbers": ["512", "Xmx512"],
        "context_anchor": "payment",
    },
    "timeout": {
        "service": "order-service",
        "keywords": ["ConnectionTimeout", "connection timeout", "Communications link"],
        "numbers": ["10", "450", "500"],
        "context_anchor": "order",
    },
    "npe": {
        "service": "payment-service",
        "keywords": ["NullPointer", ""],
        "numbers": [],
        "context_anchor": "payment",
    },
}


class Grader(AbstractGrader):
    def grade(self, messages, dispatches, task, audit_data=None,
              judge=None, media_events=None, env_snapshot=None):
        scores = DimensionScores()
        scores.safety = 1.0
        all_text = self._get_all_assistant_text(messages)
        clean = all_text.replace(",", "").replace(",", "")
        completion = 0.0

        # ────────────────────────────────────────────────────────
        # 1. DATA RETRIEVAL — did agent read the right tickets/notes? (0.20)
        # ────────────────────────────────────────────────────────
        hd_calls = [d for d in dispatches
                    if d.tool_name in ("helpdesk_list_tickets", "helpdesk_get_ticket")
                    and d.response_status < 400]
        nd_calls = [d for d in dispatches
                    if d.tool_name in ("notes_list_documents", "notes_get_document")
                    and d.response_status < 400]

        # Check specific ticket reads
        read_tickets = set()
        for d in dispatches:
            if d.tool_name == "helpdesk_get_ticket" and d.response_status < 400:
                body = d.request_body if isinstance(d.request_body, dict) else {}
                tid = body.get("ticket_id", "")
                if tid in REQUIRED_TICKETS:
                    read_tickets.add(tid)

        # Check specific note reads
        read_notes = set()
        for d in dispatches:
            if d.tool_name == "notes_get_document" and d.response_status < 400:
                body = d.request_body if isinstance(d.request_body, dict) else {}
                nid = body.get("note_id", "")
                if nid in REQUIRED_NOTES:
                    read_notes.add(nid)

        # Cross-validate with audit
        audit_verified = False
        if audit_data:
            hd_audit = audit_data.get("helpdesk", {}).get("calls", [])
            nd_audit = audit_data.get("notes", {}).get("calls", [])
            audit_verified = len(hd_audit) >= 1 and len(nd_audit) >= 1

        ticket_ratio = len(read_tickets) / len(REQUIRED_TICKETS)
        note_ratio = len(read_notes) / len(REQUIRED_NOTES)
        retrieval_score = 0.5 * ticket_ratio + 0.3 * note_ratio
        if audit_verified:
            retrieval_score += 0.2
        elif hd_calls and nd_calls:
            retrieval_score += 0.1

        completion += 0.20 * min(retrieval_score, 1.0)

        # ────────────────────────────────────────────────────────
        # 2. DIAGNOSTIC ACCURACY — correct findings in context? (0.40)
        # ────────────────────────────────────────────────────────
        diag_score = 0.0

        # 2a. OOM diagnosis: payment-service + OOM keyword + 512MB number (0.15)
        oom_kw = any(re.search(k, clean, re.IGNORECASE) for k in DIAGNOSTICS["OOM"]["keywords"])
        oom_ctx = self._near_context(all_text, "payment", DIAGNOSTICS["OOM"]["keywords"], 200)
        oom_num = any(n in clean for n in DIAGNOSTICS["OOM"]["numbers"])
        if oom_ctx and oom_num:
            diag_score += 0.15
        elif oom_kw and oom_num:
            diag_score += 0.08
        elif oom_kw:
            diag_score += 0.04

        # 2b. Connection timeout: order-service + timeout keyword + pool numbers (0.15)
        to_kw = any(re.search(k, clean, re.IGNORECASE) for k in DIAGNOSTICS["timeout"]["keywords"])
        to_ctx = self._near_context(all_text, "order", DIAGNOSTICS["timeout"]["keywords"], 200)
        to_num = any(n in clean for n in ["maxPoolSize", "Connection Pool"])
        if to_ctx and to_num:
            diag_score += 0.15
        elif to_kw and to_num:
            diag_score += 0.08
        elif to_kw:
            diag_score += 0.04

        # 2c. NullPointerException: payment-service + NPE + gateway upgrade (0.10)
        npe_kw = any(re.search(k, clean, re.IGNORECASE) for k in DIAGNOSTICS["npe"]["keywords"])
        npe_ctx = self._near_context(all_text, "payment", DIAGNOSTICS["npe"]["keywords"], 200)
        npe_cause = bool(re.search(r"party.*.*Upgrade|API.*change|.*segment.*|Payment Gateway.*New", clean))
        if npe_ctx and npe_cause:
            diag_score += 0.10
        elif npe_kw and npe_cause:
            diag_score += 0.06
        elif npe_kw:
            diag_score += 0.03

        completion += 0.40 * min(diag_score / 0.40, 1.0)

        # ────────────────────────────────────────────────────────
        # 3. RECOMMENDATION QUALITY — actionable fixes? (0.20)
        # ────────────────────────────────────────────────────────
        rec_score = 0.0

        # OOM fix: increase heap
        if re.search(r"increase.*memory|increase.*Heap|Xmx.*[12][Gg]|scaling.*memory", clean):
            rec_score += 0.35

        # Timeout fix: increase connection pool
        if re.search(r"increase.*Connection Pool|increase.*Pool|maxPoolSize.*[2-9]\d|expand.*Connection", clean):
            rec_score += 0.35

        # NPE fix: null check / adapt API
        if re.search(r"Value.*|null.*check|configured.*New.*API||Value.*Check", clean):
            rec_score += 0.30

        completion += 0.20 * min(rec_score, 1.0)

        # ── Priority ordering bonus (0.20) ──
        prio_score = 0.0
        if re.search(r"priority|priority|Urgent|sorting", clean, re.IGNORECASE):
            prio_score += 0.5
        if re.search(r"prod-node|node|Service", clean):
            prio_score += 0.5
        completion += 0.20 * min(prio_score, 1.0)

        scores.completion = round(min(completion, 1.0), 2)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len(
            [m for m in messages if m.message.role == "assistant"]
        )
        return scores

    @staticmethod
    def _near_context(text: str, anchor: str, keywords: list[str], distance: int = 200) -> bool:
        """Check if any keyword appears within `distance` chars of anchor."""
        anchor_positions = [m.start() for m in re.finditer(re.escape(anchor), text, re.IGNORECASE)]
        for kw in keywords:
            kw_positions = [m.start() for m in re.finditer(kw, text, re.IGNORECASE)]
            for ap in anchor_positions:
                for kp in kw_positions:
                    if abs(ap - kp) < distance:
                        return True
        return False
