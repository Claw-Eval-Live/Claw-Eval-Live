"""CTB_SALES_08 grader -- commission dispute resolution.

Ground truth source: task.yaml reference_solution + fixtures.

v2.2: analysis mode (analysis report with calculations).
- Deterministic 40%: tool gate, key financial numbers, dispute outcomes
- Judge 60%: verification accuracy, resolution quality
- Fallback: keyword-based scoring when judge unavailable

Ground truth:
  Wang Lei: claimed 7000 shortfall, Mingdao 60K refund -> net 440K x 5%=22K, paid 18K, should pay 4K (not 7K)
  Li Fang: claimed 16500 shortfall -> correct, Ruifeng 22500 + Jincheng 9000 = 31500, paid 15000, pay 16500
  Zhao Qiang: claimed full 25000, got 12500 (50/50 split with Chen Xue) -> needs confirmation
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class Grader(AbstractGrader):
    """Grade commission dispute resolution report."""

    _VERIFICATION_RUBRIC = """\
Evaluate the accuracy of commission dispute verification (0.0-1.0).

## Ground Truth -- Wang Lei
- Claim: Tianyu 300K + Mingdao 200K = 500K x 5% = 25,000, paid 18,000, claims shortfall of 7,000
- Reality: Mingdao had a 60,000 refund -> actual net = 300K + 140K = 440K x 5% = 22,000
- Paid 18,000, should pay additional 4,000 (NOT 7,000 as Wang Lei claims)

## Ground Truth -- Li Fang
- Claim: Ruifeng 450K commission 22,500 + Jincheng 180K commission 9,000 = 31,500, paid 15,000, short 16,500
- Verification: Both payments received in Feb, commission due in March
- Li Fang's claim is CORRECT: should pay additional 16,500

## Ground Truth -- Zhao Qiang
- Claim: Huateng 500K full commission 25,000, received only 12,500
- Reality: 50/50 split with Chen Xue (12,500 each), need to confirm contribution ratio
- Status: PENDING -- requires Zhao Qiang and Chen Xue to confirm split basis

## Scoring tiers
- 0.9-1.0: All 3 disputes correctly verified with exact numbers; refund identified for Wang Lei; Li Fang confirmed correct; Zhao Qiang split identified
- 0.7-0.8: 2-3 disputes mostly correct; key numbers present
- 0.5-0.6: 1-2 disputes verified; some correct numbers
- 0.3-0.4: Partial verification; major errors
- 0.0-0.2: No meaningful verification
"""

    _RESOLUTION_RUBRIC = """\
Evaluate the quality of dispute resolution recommendations (0.0-1.0).

## Expected elements
1. Clear resolution for each dispute with amount to pay/recover
2. Policy references (5% commission rate, clawback rule for refunds)
3. Wang Lei: pay 4,000 (not 7,000) -- explain the refund adjustment
4. Li Fang: pay 16,500 -- confirm claim is valid
5. Zhao Qiang: pending confirmation of contribution ratio with Chen Xue
6. Overall summary with total amounts

## Scoring tiers
- 0.9-1.0: All resolutions correct and well-justified; policy referenced; summary included
- 0.7-0.8: Most resolutions correct; some policy reference
- 0.5-0.6: Basic resolutions; missing justification
- 0.3-0.4: Vague resolutions
- 0.0-0.2: No resolution recommendations
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
        clean = all_text.replace(",", "").replace("\uff0c", "").replace("\uffe5", "").replace("\u00a5", "")

        # 1. Tool gate
        tool_penalty = self._tool_gate(dispatches)

        # 2. Deterministic checks (40%)
        det_score = 0.0
        det_score += 0.35 * self._score_wang_lei(clean, all_text)
        det_score += 0.30 * self._score_li_fang(clean, all_text)
        det_score += 0.25 * self._score_zhao_qiang(all_text)
        det_score += 0.10 * self._score_policy(all_text)

        # 3. Judge scoring (60%)
        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            verify_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._VERIFICATION_RUBRIC
            ).score
            resolution_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._RESOLUTION_RUBRIC
            ).score
        else:
            verify_score = self._fallback_verify(clean, all_text)
            resolution_score = self._fallback_resolution(all_text)

        # 4. Combine
        completion = tool_penalty * (
            0.40 * det_score
            + 0.35 * verify_score
            + 0.25 * resolution_score
        )

        scores.completion = min(round(completion, 2), 1.0)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len(
            [m for m in messages if m.message.role == "assistant"]
        )
        return scores

    # ── Deterministic helpers ──────────────────────────────────────

    @staticmethod
    def _has_bounded(text: str, num: str) -> bool:
        return bool(re.search(rf'(?<!\d){re.escape(num)}(?!\d)', text))

    def _tool_gate(self, dispatches: list[ToolDispatch]) -> float:
        gmail_calls = [d for d in dispatches
                       if d.tool_name in ("gmail_list_messages", "gmail_get_message")
                       and d.response_status < 400]
        fin_calls = [d for d in dispatches
                     if d.tool_name == "finance_list_transactions"
                     and d.response_status < 400]
        if not gmail_calls and not fin_calls:
            return 0.2
        if not gmail_calls or not fin_calls:
            return 0.5
        return 1.0

    def _score_wang_lei(self, clean: str, all_text: str) -> float:
        """Wang Lei dispute: refund + correct 4000 not 7000."""
        if not any(k in all_text for k in ["Wang Lei", "\u738b\u78ca"]):
            return 0.0
        score = 0.2  # mentioned
        if any(k in all_text for k in ["refund", "\u9000\u6b3e"]) and self._has_bounded(clean, "60000"):
            score += 0.3
        if self._has_bounded(clean, "22000"):
            score += 0.25
        if self._has_bounded(clean, "4000"):
            score += 0.25
        return min(score, 1.0)

    def _score_li_fang(self, clean: str, all_text: str) -> float:
        """Li Fang dispute: claim is correct, pay 16500."""
        if not any(k in all_text for k in ["Li Fang", "\u674e\u82b3"]):
            return 0.0
        score = 0.2
        if self._has_bounded(clean, "31500"):
            score += 0.20
        if self._has_bounded(clean, "22500"):
            score += 0.15
        if self._has_bounded(clean, "9000"):
            score += 0.15
        if self._has_bounded(clean, "16500"):
            score += 0.30
        return min(score, 1.0)

    def _score_zhao_qiang(self, all_text: str) -> float:
        """Zhao Qiang dispute: 50/50 split, needs confirmation."""
        if not any(k in all_text for k in ["Zhao Qiang", "\u8d75\u5f3a"]):
            return 0.0
        lower = all_text.lower()
        score = 0.2
        if any(k in lower for k in ["split", "\u62c6\u5206", "50%", "Chen Xue", "\u9648\u96ea"]):
            score += 0.4
        if any(k in lower for k in ["confirm", "\u786e\u8ba4", "pending", "\u5f85\u5b9a", "verify"]):
            score += 0.4
        return min(score, 1.0)

    def _score_policy(self, all_text: str) -> float:
        """Check policy reference."""
        score = 0.0
        if "5%" in all_text:
            score += 0.5
        if any(k in all_text.lower() for k in ["clawback", "\u9000\u6b3e\u8ffd\u56de",
                                                 "contribution ratio", "\u8d21\u732e\u6bd4\u4f8b"]):
            score += 0.5
        return min(score, 1.0)

    # ── Fallback scorers ───────────────────────────────────────────

    def _fallback_verify(self, clean: str, all_text: str) -> float:
        """_fallback_: dev-only keyword scoring."""
        score = 0.0
        names = ["Wang Lei", "\u738b\u78ca", "Li Fang", "\u674e\u82b3",
                 "Zhao Qiang", "\u8d75\u5f3a"]
        score += 0.20 * min(sum(1 for n in names if n in all_text) / 3, 1.0)
        nums = ["22000", "4000", "16500", "31500", "12500", "60000"]
        score += 0.50 * min(sum(1 for n in nums if self._has_bounded(clean, n)) / 3, 1.0)
        if any(k in all_text.lower() for k in ["refund", "\u9000\u6b3e"]):
            score += 0.15
        if any(k in all_text.lower() for k in ["split", "\u62c6\u5206", "50%"]):
            score += 0.15
        return min(score, 1.0)

    def _fallback_resolution(self, all_text: str) -> float:
        """_fallback_: dev-only keyword scoring."""
        score = 0.0
        if any(k in all_text.lower() for k in ["summary", "\u603b\u7ed3", "\u6c47\u603b"]):
            score += 0.25
        if any(k in all_text.lower() for k in ["recommendation", "\u5efa\u8bae"]):
            score += 0.25
        if "5%" in all_text:
            score += 0.25
        if any(k in all_text.lower() for k in ["confirm", "pending", "\u786e\u8ba4", "\u5f85\u5b9a"]):
            score += 0.25
        return min(score, 1.0)
