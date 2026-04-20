"""CTB_RESEARCH_01 grader — competitor product comparison report.

Ground truth source: task.yaml reference_solution is empty for this task.
Rubric ground truth derived from fixture data:     - fixtures/web/pages.json

v2.2: analysis mode (research).
- Deterministic 30%: search gate, product coverage, meeting capacity numbers, API counts
- Judge 70%: data accuracy matrix, positioning analysis, customer-type recommendations
- Fallback: keyword-based scoring when judge unavailable
"""

from __future__ import annotations

import re

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.trace import DimensionScores


class Grader(AbstractGrader):

    # ── Judge rubrics ──────────────────────────────────────────────

    _DATA_ACCURACY_RUBRIC = """\
Evaluate the accuracy of the competitor feature comparison matrix (0.0-1.0).

## Ground Truth
### Meeting capacity
- Feishu (飞书): 500 people
- WeCom (企业微信): 300 people
- DingTalk (钉钉): 302 people
- WeLink: 1000 people (largest)

### Open platform API count
- Feishu (飞书): 800+
- WeCom (企业微信): 500+
- DingTalk (钉钉): 1200+ (most)
- WeLink: ~300 (fewest)

### Pricing (per seat per month)
- Feishu (飞书): 50 yuan/person/month (commercial edition), free for <50 people
- WeCom (企业微信): 30 yuan/person/month (standard edition), free basic
- DingTalk (钉钉): 9.8 yuan/person/month (professional edition), lowest price
- WeLink: 25 yuan/person/month (commercial edition), free for <100

### Key conclusions
- WeLink has largest meeting capacity (1000)
- DingTalk (钉钉) has most APIs (1200+)
- DingTalk (钉钉) has lowest per-seat price (9.8 yuan)

## Scoring tiers
- 0.9-1.0: All 4 products with correct capacity, API count, and pricing; correct superlatives
- 0.7-0.8: 3-4 products mostly correct; correct identification of max capacity and max API
- 0.5-0.6: 2-3 products with partial data; some correct superlatives
- 0.3-0.4: Minimal correct data
- 0.0-0.2: No meaningful comparison data
"""

    _POSITIONING_RUBRIC = """\
Evaluate the quality of product positioning analysis for each competitor (0.0-1.0).

## Expected positioning
- Feishu (飞书): AI-driven, best document collaboration; suited for tech/creative companies
- WeCom (企业微信): WeChat ecosystem integration; strongest customer management (CRM-like)
- DingTalk (钉钉): Cheapest, Alibaba Cloud integration, low-code platform; suited for cost-sensitive SMEs
- WeLink: Highest security (level-3 certification), best video quality; suited for government/enterprise

### Feature dimensions to compare
- Instant messaging, video conferencing, document collaboration, approval workflow, project management

## Scoring tiers
- 0.9-1.0: Clear positioning for each product; comprehensive feature matrix; differentiation well explained
- 0.7-0.8: Good positioning for most products; some feature coverage
- 0.5-0.6: Basic positioning; missing some products
- 0.3-0.4: Minimal positioning analysis
- 0.0-0.2: No meaningful positioning
"""

    _RECOMMENDATION_RUBRIC = """\
Evaluate the quality of recommendations by customer type (0.0-1.0).

## Expected recommendations
- SME (中小企业): DingTalk (钉钉) -- lowest cost, good features
- Large enterprise (大型企业): Feishu (飞书) or WeCom (企业微信)
- Government/SOE (政企): WeLink -- security certifications

## Scoring tiers
- 0.9-1.0: Specific recommendations per customer type with reasoning; all 3 segments covered
- 0.7-0.8: Recommendations for 2-3 segments; reasoning mostly clear
- 0.5-0.6: Generic recommendations; 1-2 segments covered
- 0.3-0.4: Vague suggestions without clear segmentation
- 0.0-0.2: No meaningful recommendations
"""

    # ── Helpers ───────────────────────────────────────────────────

    @staticmethod
    def _has_bounded(text: str, num: str) -> bool:
        return bool(re.search(rf'(?<!\d){re.escape(num)}(?!\d)', text))

    # ── Grading ──────────────────────────────────────────────────

    def grade(self, messages, dispatches, task, audit_data=None,
              judge=None, media_events=None, env_snapshot=None):
        scores = DimensionScores()
        scores.safety = 1.0
        final_text = self._get_final_assistant_text(messages)
        clean = final_text.replace(",", "").replace("\uff0c", "")

        # 1. Tool gate
        search_calls = [d for d in dispatches
                        if d.tool_name in ("web_search", "web_get_page")
                        and d.response_status < 400]
        tool_penalty = 1.0 if search_calls else 0.2

        # 2. Deterministic dimensions (30%)
        det = 0.0
        det += 0.05 * min(len(search_calls) / 3, 1.0)         # dim1: search effort
        det += 0.05 * self._check_product_coverage(final_text)  # dim2: 4 products mentioned
        det += 0.10 * self._check_capacity_numbers(clean, final_text)  # dim3: meeting caps
        det += 0.10 * self._check_api_numbers(clean, final_text)       # dim4: API counts

        # 3. Judge dimensions (70%)
        judge_score = 0.0
        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            judge_score += 0.30 * judge.evaluate(
                task.prompt.text, conversation, actions, self._DATA_ACCURACY_RUBRIC
            ).score
            judge_score += 0.22 * judge.evaluate(
                task.prompt.text, conversation, actions, self._POSITIONING_RUBRIC
            ).score
            judge_score += 0.18 * judge.evaluate(
                task.prompt.text, conversation, actions, self._RECOMMENDATION_RUBRIC
            ).score
        else:
            judge_score = self._fallback_judge(clean, final_text)

        # 4. Combine
        completion = tool_penalty * (det + judge_score)

        scores.completion = round(min(completion, 1.0), 2)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len([m for m in messages if m.message.role == "assistant"])
        return scores

    # ── Deterministic helpers ────────────────────────────────────

    @staticmethod
    def _check_product_coverage(text):
        products_cn = ["飞书", "企业微信", "钉钉", "WeLink"]
        products_en = ["Feishu", "WeCom", "DingTalk", "WeLink"]
        text_lower = text.lower()
        found = 0
        for cn, en in zip(products_cn, products_en):
            if cn in text or en.lower() in text_lower:
                found += 1
        return min(found / 3, 1.0)

    def _check_capacity_numbers(self, clean, text):
        """Meeting capacity: 飞书/Feishu 500, 企业微信/WeCom 300, 钉钉/DingTalk 302, WeLink 1000."""
        pairs = [
            (["飞书", "Feishu"], "500"),
            (["企业微信", "WeCom"], "300"),
            (["钉钉", "DingTalk"], "302"),
            (["WeLink"], "1000"),
        ]
        text_lower = text.lower()
        hits = 0
        for prods, val in pairs:
            if any(p.lower() in text_lower for p in prods) and self._has_bounded(clean, val):
                hits += 1
        return min(hits / 3, 1.0)

    def _check_api_numbers(self, clean, text):
        """API count: 飞书/Feishu 800, 企业微信/WeCom 500, 钉钉/DingTalk 1200, WeLink 300."""
        pairs = [
            (["飞书", "Feishu"], "800"),
            (["企业微信", "WeCom"], "500"),
            (["钉钉", "DingTalk"], "1200"),
            (["WeLink"], "300"),
        ]
        text_lower = text.lower()
        hits = 0
        for prods, val in pairs:
            if any(p.lower() in text_lower for p in prods) and self._has_bounded(clean, val):
                hits += 1
        return min(hits / 3, 1.0)

    # ── Fallback (dev-only) ──────────────────────────────────────

    @classmethod
    def _fallback_judge(cls, clean, text):
        """_fallback_: keyword-based, only for --no-judge dev mode."""
        score = 0.0

        # Pricing mentions
        price_kws = ["50元", "30元", "9.8元", "25元", "免费", "free", "50 yuan", "30 yuan", "9.8 yuan", "25 yuan"]
        score += 0.15 * min(sum(1 for p in price_kws if p in text.lower()) / 3, 1.0)

        # Superlative conclusions
        text_lower = text.lower()
        if ("WeLink" in text or "Huawei" in text or "华为" in text) and (cls._has_bounded(text, "1000") or "largest" in text_lower or "最大" in text):
            score += 0.08
        if ("DingTalk" in text or "钉钉" in text) and (cls._has_bounded(text, "1200") or "most api" in text_lower or "most APIs" in text or "API最多" in text or "最多" in text):
            score += 0.08

        # Recommendations by customer type
        rec_kws = ["SME", "small", "large enterprise", "government", "recommend", "suited",
                   "中小企业", "大型企业", "政企", "推荐", "适合"]
        score += 0.12 * min(sum(1 for kw in rec_kws if kw.lower() in text_lower) / 3, 1.0)

        # Feature dimensions
        feat_kws = ["instant messaging", "video conferenc", "document collaborat", "approval", "project management",
                    "即时通讯", "视频会议", "文档协作", "审批", "项目管理"]
        score += 0.12 * min(sum(1 for kw in feat_kws if kw.lower() in text_lower) / 3, 1.0)

        # Table format
        if "|" in text and "---" in text:
            score += 0.08

        return min(score, 0.70)
