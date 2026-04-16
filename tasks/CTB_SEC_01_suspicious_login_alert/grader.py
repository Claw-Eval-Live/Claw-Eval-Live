"""CTB_SEC_01 grader -- suspicious login alert analysis.

Ground truth source: task.yaml + fixtures/gmail/inbox.json

v2.2: Claw-Eval mode (security analysis report).
- Deterministic 35%: tool gate, account identification, IP correlation, classification
- Judge 65%: threat analysis accuracy, remediation quality
- Fallback: keyword-based scoring when judge unavailable

Ground truth:
  wang.lei + zhang.min: same IP 185.220.101.34 (Russia) -> coordinated attack, compromised
  li.na: brute force from Ukraine, 17 failed attempts, account locked
  chen.wei: Hong Kong login but has travel record -> false positive
  wang.lei: VPN data exfiltration 2.3GB, repos: core-payment-service, user-auth-module, api-gateway
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class Grader(AbstractGrader):
    """Grade suspicious login alert analysis."""

    COMPROMISED = ["wang.lei", "zhang.min"]
    ATTACKER_IP = "185.220.101.34"
    BRUTE_FORCE_USER = "li.na"
    FALSE_POSITIVE_USER = "chen.wei"
    EXFIL_REPOS = ["core-payment-service", "user-auth-module", "api-gateway"]

    _THREAT_ANALYSIS_RUBRIC = """\
Evaluate the accuracy of the security threat analysis (0.0-1.0).

## Ground Truth
- wang.lei and zhang.min: COMPROMISED -- same attacker IP 185.220.101.34 (Russia), coordinated attack
- li.na: BRUTE FORCE ATTACK -- 17 failed login attempts from Ukraine, account locked
- chen.wei: FALSE POSITIVE -- Hong Kong login matches travel schedule
- wang.lei additional: VPN session data exfiltration of 2.3GB, accessed repos: core-payment-service, user-auth-module, api-gateway

## Key Analysis Points
- IP correlation: 185.220.101.34 used for both wang.lei and zhang.min (coordinated)
- Data breach risk: 2.3GB exfiltrated via wang.lei's compromised VPN session
- 3 code repositories potentially exposed

## Scoring tiers
- 0.9-1.0: All accounts correctly classified; IP correlation identified; data exfiltration flagged with specifics
- 0.7-0.8: Most accounts correct; IP correlation noted; exfiltration mentioned
- 0.5-0.6: Some accounts classified; partial correlation or exfiltration detail
- 0.3-0.4: Minimal classification
- 0.0-0.2: No meaningful analysis
"""

    _REMEDIATION_RUBRIC = """\
Evaluate the quality of remediation recommendations (0.0-1.0).

## Expected Recommendations
- Reset passwords for compromised accounts (wang.lei, zhang.min)
- Block attacker IP 185.220.101.34
- Enable/enforce MFA for all accounts
- Review and revoke wang.lei's VPN access
- Conduct security audit on the 3 exposed repositories
- Dismiss chen.wei alert as false positive
- Monitor li.na account after lockout period

## Scoring tiers
- 0.9-1.0: Specific remediation per account; IP blocking; MFA recommendation; data breach response plan
- 0.7-0.8: Most remediations present; reasonable specificity
- 0.5-0.6: Some remediations; generic recommendations
- 0.3-0.4: Minimal recommendations
- 0.0-0.2: No recommendations
"""

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
        all_text = self._get_all_assistant_text(messages)
        clean = all_text.replace(",", "").replace("\uff0c", "")

        # 1. Tool gate -- read alert emails
        tool_penalty = self._tool_gate(dispatches)

        # 2. Deterministic checks (35%)
        det_score = 0.0
        det_score += 0.25 * self._score_account_identification(all_text)
        det_score += 0.25 * self._score_ip_correlation(all_text)
        det_score += 0.25 * self._score_false_positive(all_text)
        det_score += 0.25 * self._score_data_exfil(all_text)

        # 3. Judge scoring (65%)
        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            threat_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._THREAT_ANALYSIS_RUBRIC
            ).score
            remediation_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._REMEDIATION_RUBRIC
            ).score
        else:
            threat_score = self._fallback_threat(all_text, clean)
            remediation_score = self._fallback_remediation(all_text)

        # 4. Combine
        completion = tool_penalty * (
            0.35 * det_score
            + 0.35 * threat_score
            + 0.30 * remediation_score
        )

        scores.completion = min(round(completion, 2), 1.0)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len(
            [m for m in messages if m.message.role == "assistant"]
        )
        return scores

    # ── Deterministic helpers ──────────────────────────────────────

    def _tool_gate(self, dispatches: list[ToolDispatch]) -> float:
        get_calls = [d for d in dispatches
                     if d.tool_name == "gmail_get_message" and d.response_status < 400]
        if not get_calls:
            list_calls = [d for d in dispatches
                          if d.tool_name == "gmail_list_messages" and d.response_status < 400]
            return 0.5 if list_calls else 0.2
        return 1.0 if len(get_calls) >= 3 else 0.7

    def _score_account_identification(self, all_text: str) -> float:
        """Check compromised accounts + brute force user are identified."""
        found = 0
        lower = all_text.lower()
        risk_kw = ["compromised", "high risk", "threat", "attacked", "breach",
                   "\u5165\u4fb5", "\u88ab\u653b\u51fb", "\u9ad8\u98ce\u9669"]
        for user in self.COMPROMISED:
            if user in all_text and any(k in lower for k in risk_kw):
                found += 1
        if self.BRUTE_FORCE_USER in all_text and any(k in lower for k in ["brute", "force", "\u66b4\u529b"]):
            found += 1
        return min(found / 3, 1.0)

    def _score_ip_correlation(self, all_text: str) -> float:
        """Check IP correlation is identified."""
        if self.ATTACKER_IP not in all_text:
            return 0.0
        lower = all_text.lower()
        corr_kw = ["correlat", "same ip", "same attacker", "coordinated",
                   "\u76f8\u540c", "\u540c\u4e00", "\u5173\u8054"]
        if any(k in lower for k in corr_kw):
            return 1.0
        return 0.4

    def _score_false_positive(self, all_text: str) -> float:
        """Check chen.wei is identified as false positive."""
        if self.FALSE_POSITIVE_USER not in all_text:
            return 0.0
        lower = all_text.lower()
        fp_kw = ["false positive", "travel", "normal", "low risk",
                 "\u8bef\u62a5", "\u51fa\u5dee", "\u6b63\u5e38", "\u4f4e\u98ce\u9669"]
        return 1.0 if any(k in lower for k in fp_kw) else 0.2

    def _score_data_exfil(self, all_text: str) -> float:
        """Check data exfiltration details."""
        score = 0.0
        if "2.3" in all_text and any(k in all_text.lower() for k in ["gb", "exfil", "data", "\u6570\u636e", "\u5916\u6cc4"]):
            score += 0.5
        repo_count = sum(1 for r in self.EXFIL_REPOS if r in all_text)
        score += 0.5 * min(repo_count / 2, 1.0)
        return min(score, 1.0)

    # ── Fallback scorers ───────────────────────────────────────────

    def _fallback_threat(self, all_text: str, clean: str) -> float:
        """_fallback_: dev-only keyword scoring."""
        score = 0.0
        accounts = ["wang.lei", "zhang.min", "li.na", "chen.wei"]
        score += 0.20 * min(sum(1 for a in accounts if a in all_text) / 3, 1.0)
        if self.ATTACKER_IP in all_text:
            score += 0.20
        if "2.3" in all_text:
            score += 0.15
        repo_count = sum(1 for r in self.EXFIL_REPOS if r in all_text)
        score += 0.15 * min(repo_count / 2, 1.0)
        threat_kw = ["compromised", "brute force", "false positive", "correlation"]
        score += 0.30 * min(sum(1 for k in threat_kw if k.lower() in all_text.lower()) / 2, 1.0)
        return min(score, 1.0)

    def _fallback_remediation(self, all_text: str) -> float:
        """_fallback_: dev-only keyword scoring."""
        score = 0.0
        rem_kw = ["reset password", "block ip", "MFA", "multi-factor", "revoke",
                  "\u91cd\u7f6e\u5bc6\u7801", "\u5c01\u7981IP", "\u591a\u56e0\u7d20"]
        score += 0.60 * min(sum(1 for k in rem_kw if k.lower() in all_text.lower()) / 3, 1.0)
        struct_kw = ["account", "recommendation", "risk level", "timeline"]
        score += 0.40 * min(sum(1 for k in struct_kw if k.lower() in all_text.lower()) / 2, 1.0)
        return min(score, 1.0)
