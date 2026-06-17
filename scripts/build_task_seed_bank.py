#!/usr/bin/env python3
"""Build a market-signal-driven task seed bank.

This stage sits between:

    workflow patterns -> task authoring

It does not generate runnable tasks directly. Instead, it produces a
structured seed bank that can later be expanded by an LLM or a human task
author into candidate task specs.
"""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path


DEFAULT_WORKFLOW_CSV = "benchmark/signals/workflow_patterns.csv"
DEFAULT_FAMILY_CSV = "benchmark/signals/family_weights.csv"
DEFAULT_OUTPUT_CSV = "benchmark/seeds/market_task_seed_bank_v0.1.csv"
DEFAULT_OUTPUT_MD = "benchmark/seeds/market_task_seed_bank_v0.1.md"


FAMILY_PLAYBOOK = {
    "Workspace-Repair-Config": {
        "environment_hint": "local_workspace",
        "workflow_archetype": "diagnose -> repair -> verify",
        "artifact_hint": "fixed files / diagnosis report / passing checks",
    },
    "Document-Transform": {
        "environment_hint": "attachments_or_documents_service",
        "workflow_archetype": "read -> extract -> reorganize -> output",
        "artifact_hint": "merged report / converted file / structured summary",
    },
    "Cross-Tool-Orchestration": {
        "environment_hint": "mock_services",
        "workflow_archetype": "gather context -> coordinate tools -> take actions -> summarize",
        "artifact_hint": "coordinated actions + execution summary",
    },
    "Research-to-Artifact": {
        "environment_hint": "mock_services_or_web",
        "workflow_archetype": "search -> compare -> synthesize -> deliver artifact",
        "artifact_hint": "brief / report / recommendation memo",
    },
    "Data-Analysis-Reporting": {
        "environment_hint": "attachments_or_mock_services",
        "workflow_archetype": "collect data -> compute -> interpret -> report",
        "artifact_hint": "metrics report / reconciliation table / alert memo",
    },
    "Communication-Drafting": {
        "environment_hint": "mock_services",
        "workflow_archetype": "inspect context -> personalize draft -> decide send/draft/skip",
        "artifact_hint": "draft set / meeting prep / communication plan",
    },
}


PATTERN_PLAYBOOK = {
    "shell_terminal": {
        "seed_frames": [
            "从一组终端输出和日志中定位失败根因，并写出修复说明",
            "检查命令执行结果与目录状态，修复环境后重新验证",
        ],
    },
    "debugging": {
        "seed_frames": [
            "根据报错日志定位 bug 根因，修复后给出验证结果",
            "排查多步失败链路，区分表象错误与真正 root cause",
        ],
    },
    "config_management": {
        "seed_frames": [
            "修复多份配置文件并输出变更记录",
            "根据策略要求审计配置，指出风险项并给出建议值",
        ],
    },
    "devops_infra": {
        "seed_frames": [
            "排查部署失败原因并修复基础设施配置",
            "检查服务健康异常，给出最小修复方案和验证结果",
        ],
    },
    "coding_tools": {
        "seed_frames": [
            "修复脚本或测试失败问题，并总结哪些改动是必要的",
            "在不改业务目标的前提下让工具链重新跑通",
        ],
    },
    "doc_summarization": {
        "seed_frames": [
            "把长文档压缩成可执行摘要，并保留关键约束",
            "从多页资料里提炼决策者真正需要看的信息",
        ],
    },
    "pdf_processing": {
        "seed_frames": [
            "从 PDF 中提取关键信息并整理成结构化输出",
            "比较多份 PDF 的差异并写出统一结论",
        ],
    },
    "format_conversion": {
        "seed_frames": [
            "把多种格式资料整理成统一 Markdown / JSON 交付物",
            "在格式转换同时做去重、归类和字段标准化",
        ],
    },
    "ocr_extraction": {
        "seed_frames": [
            "从噪声文档中提取可用文本或表格，再清洗成结果文件",
            "处理扫描件 / OCR 文本中的错漏，得到可靠摘要",
        ],
    },
    "content_rewriting": {
        "seed_frames": [
            "保留原意但重写成更自然、更可执行的文案",
            "将技术或 AI 风格文本改成目标受众能直接使用的版本",
        ],
    },
    "media_processing": {
        "seed_frames": [
            "从多媒体素材提炼重点，并转成可审阅的文字交付物",
            "围绕图像 / 音视频内容做加工后摘要",
        ],
    },
    "translation": {
        "seed_frames": [
            "将工作文档翻译并本地化到指定受众场景",
            "对跨语言材料做统一术语和风格整理",
        ],
    },
    "workflow_automation": {
        "seed_frames": [
            "跨多个业务系统完成一条完整事务链，并做最终汇总",
            "根据输入约束决定先后顺序，自动推进多个动作",
        ],
    },
    "calendar_scheduling": {
        "seed_frames": [
            "结合日历和上下文安排会议，并说明为何这样安排",
            "围绕即将发生的会议，产出准备材料或协调动作",
        ],
    },
    "task_management": {
        "seed_frames": [
            "收集分散任务状态并整理成可执行待办清单",
            "根据优先级、截止时间和风险调整任务计划",
        ],
    },
    "note_knowledge": {
        "seed_frames": [
            "从历史笔记和知识材料中抽取背景，服务当前任务",
            "把分散知识整理成 onboarding / prep / recall 文档",
        ],
    },
    "multi_service_integration": {
        "seed_frames": [
            "在多个系统之间搬运上下文，完成一件端到端的业务事",
            "对齐多个系统里的事实后再做动作，避免单点误判",
        ],
    },
    "web_research": {
        "seed_frames": [
            "对多个外部方案做结构化调研并形成推荐建议",
            "从多源资料中提炼可执行结论，而不是只堆信息",
        ],
    },
    "web_search": {
        "seed_frames": [
            "围绕明确问题做检索、筛选和归纳，产出简洁结论",
            "将搜索结果转成带优先级的行动建议",
        ],
    },
    "news_monitoring": {
        "seed_frames": [
            "监控一组来源，抽取对业务有影响的更新并汇总",
            "按重要度筛选新闻流，形成 briefing",
        ],
    },
    "academic_research": {
        "seed_frames": [
            "从论文或技术文档中提炼方法、限制和适用边界",
            "比较若干研究方案，形成工程化视角的总结",
        ],
    },
    "browser_automation": {
        "seed_frames": [
            "围绕网页信息收集与整理，产出可验证的结构化结果",
            "从网页流程中提取多步证据，再输出报告",
        ],
    },
    "data_analysis": {
        "seed_frames": [
            "对原始数据计算关键指标，并解释背后的业务含义",
            "从数据中发现异常或趋势，再生成报告",
        ],
    },
    "financial_analysis": {
        "seed_frames": [
            "围绕预算、收益或成本做分析并给出决策建议",
            "对多项财务信息做汇总和优先级判断",
        ],
    },
    "spreadsheet_database": {
        "seed_frames": [
            "从表格 / 数据库风格资料中抽取、对齐并核对字段",
            "做跨表合并或对账，输出结构化结论",
        ],
    },
    "accounting_expense": {
        "seed_frames": [
            "对支出、报销或预算执行做归类和汇总",
            "识别重复、异常或越界开销，并形成处理建议",
        ],
    },
    "email_management": {
        "seed_frames": [
            "基于邮件上下文起草高质量回复或跟进草稿",
            "对邮件做分类、筛选和动作决策，而不是只回复",
        ],
    },
    "messaging_chat": {
        "seed_frames": [
            "根据聊天上下文做团队同步或决策通知草稿",
            "从碎片消息中整理关键事项并形成正式输出",
        ],
    },
    "marketing_content": {
        "seed_frames": [
            "围绕特定受众重写营销内容并保留核心卖点",
            "把原始卖点资料整理成可直接外发的内容草稿",
        ],
    },
    "crm_sales": {
        "seed_frames": [
            "结合 CRM 和历史沟通起草个性化客户推进方案",
            "识别谁该跟进、谁该跳过，并给出依据",
        ],
    },
}


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _allocate_integer_budget(weight_rows: list[dict[str, str]], total_budget: int) -> dict[str, int]:
    raw = []
    floors = {}
    remainders = []
    used = 0
    for row in weight_rows:
        family = row["family"]
        weight = float(row["market_weight"])
        exact = total_budget * weight
        floor = int(exact)
        floors[family] = floor
        used += floor
        remainders.append((exact - floor, family))

    remaining = total_budget - used
    for _frac, family in sorted(remainders, reverse=True):
        if remaining <= 0:
            break
        floors[family] += 1
        remaining -= 1

    return floors


def _allocate_family_pattern_budget(patterns: list[dict[str, str]], family_budget: int) -> list[int]:
    if not patterns:
        return []
    if family_budget <= len(patterns):
        return [1 if i < family_budget else 0 for i in range(len(patterns))]

    base = [1] * len(patterns)
    remaining = family_budget - len(patterns)
    total_downloads = sum(int(p["total_downloads"]) for p in patterns)

    exact_extras = []
    floors = []
    used = 0
    for pattern in patterns:
        share = int(pattern["total_downloads"]) / total_downloads if total_downloads else 0
        exact = remaining * share
        floor = int(exact)
        floors.append(floor)
        exact_extras.append(exact - floor)
        used += floor

    leftover = remaining - used
    order = sorted(range(len(patterns)), key=lambda idx: exact_extras[idx], reverse=True)
    for idx in range(len(patterns)):
        base[idx] += floors[idx]
    for idx in order[:leftover]:
        base[idx] += 1
    return base


def _build_seed_row(
    *,
    family: str,
    family_budget: int,
    family_weight_pct: str,
    pattern_rank: int,
    pattern_row: dict[str, str],
    recommended_seed_ideas: int,
) -> dict[str, str]:
    playbook = FAMILY_PLAYBOOK.get(family, {})
    override = PATTERN_PLAYBOOK.get(pattern_row["pattern_id"], {})
    seed_frames = override.get("seed_frames", [
        f"围绕 {pattern_row['label']} 设计一条复合工作流任务",
        "要求包含明确 artifact 和可验证输出",
    ])

    return {
        "seed_id": f"SEED_{pattern_row['pattern_id']}",
        "family": family,
        "family_market_weight": family_weight_pct,
        "family_seed_budget": str(family_budget),
        "pattern_rank_in_family": str(pattern_rank),
        "pattern_id": pattern_row["pattern_id"],
        "pattern_label": pattern_row["label"],
        "pattern_total_downloads": pattern_row["total_downloads"],
        "recommended_seed_ideas": str(recommended_seed_ideas),
        "workflow_archetype": playbook.get("workflow_archetype", "inspect -> decide -> deliver"),
        "environment_hint": playbook.get("environment_hint", "mock_services"),
        "artifact_hint": playbook.get("artifact_hint", "report / file / structured output"),
        "seed_frame_1": seed_frames[0],
        "seed_frame_2": seed_frames[1] if len(seed_frames) > 1 else "",
        "top_skills": pattern_row["top_skills"],
    }


def write_csv(rows: list[dict[str, str]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "seed_id",
        "family",
        "family_market_weight",
        "family_seed_budget",
        "pattern_rank_in_family",
        "pattern_id",
        "pattern_label",
        "pattern_total_downloads",
        "recommended_seed_ideas",
        "workflow_archetype",
        "environment_hint",
        "artifact_hint",
        "seed_frame_1",
        "seed_frame_2",
        "top_skills",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_markdown(
    rows: list[dict[str, str]],
    output_path: Path,
    *,
    seed_budget: int,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[row["family"]].append(row)

    lines = [
        "# Market-Driven Task Seed Bank v0.1",
        "",
        "这份 seed bank 不是 runnable task 列表，而是 market signal 推出来的 task 种子层。",
        "",
        f"- total seed budget: `{seed_budget}`",
        "- source of truth: `benchmark/signals/workflow_patterns.csv` + `benchmark/signals/family_weights.csv`",
        "- principle: 先由市场 signal 决定要在哪些 workflow 上发散，再让 LLM / 人工把 seed 扩成 candidate tasks",
        "- non-goal: 不把 legacy benchmark 题库当成 seed 来源",
        "",
    ]

    for family, family_rows in grouped.items():
        family_rows = sorted(
            family_rows,
            key=lambda row: (int(row["pattern_rank_in_family"]), -int(row["recommended_seed_ideas"])),
        )
        budget = family_rows[0]["family_seed_budget"]
        weight = family_rows[0]["family_market_weight"]
        lines.extend([
            f"## {family}",
            "",
            f"- market weight: `{weight}`",
            f"- family seed budget: `{budget}`",
            "",
            "| Pattern | Downloads | Seed Ideas | Environment | Seed Frame 1 | Seed Frame 2 |",
            "|---|---:|---:|---|---|---|",
        ])
        for row in family_rows:
            lines.append(
                "| {pattern_label} | {pattern_total_downloads} | {recommended_seed_ideas} | {environment_hint} | {seed_frame_1} | {seed_frame_2} |".format(**row)
            )
        lines.append("")

    lines.extend([
        "## How To Use",
        "",
        "1. 对每个 seed 先扩成 5-8 个 candidate task ideas。",
        "2. 用 artifact clarity / verifier feasibility / workflow completeness / expected difficulty 做初筛。",
        "3. 每个 seed 只实现 top 2-3 个 idea。",
        "4. smoke 跑后再做 retention，最后留下 1-2 个真正值得公开发布的任务。",
        "",
    ])

    output_path.write_text("\n".join(lines), encoding="utf-8")


def build_seed_bank(
    workflow_csv: Path,
    family_csv: Path,
    *,
    seed_budget: int,
    patterns_per_family: int,
) -> list[dict[str, str]]:
    workflow_rows = _read_csv(workflow_csv)
    family_rows = _read_csv(family_csv)

    family_budget_map = _allocate_integer_budget(family_rows, seed_budget)
    grouped_patterns: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in workflow_rows:
        grouped_patterns[row["family"]].append(row)

    for family, rows in grouped_patterns.items():
        rows.sort(key=lambda row: int(row["total_downloads"]), reverse=True)

    family_weight_lookup = {
        row["family"]: row["market_weight_pct"]
        for row in family_rows
    }

    output_rows: list[dict[str, str]] = []
    for family_row in family_rows:
        family = family_row["family"]
        family_budget = family_budget_map[family]
        ranked_patterns = grouped_patterns.get(family, [])[:patterns_per_family]
        pattern_alloc = _allocate_family_pattern_budget(ranked_patterns, family_budget)

        for rank, (pattern_row, seed_count) in enumerate(zip(ranked_patterns, pattern_alloc), start=1):
            if seed_count <= 0:
                continue
            output_rows.append(
                _build_seed_row(
                    family=family,
                    family_budget=family_budget,
                    family_weight_pct=family_weight_lookup[family],
                    pattern_rank=rank,
                    pattern_row=pattern_row,
                    recommended_seed_ideas=seed_count,
                )
            )

    return output_rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow-csv", default=DEFAULT_WORKFLOW_CSV)
    parser.add_argument("--family-csv", default=DEFAULT_FAMILY_CSV)
    parser.add_argument("--seed-budget", type=int, default=24)
    parser.add_argument("--patterns-per-family", type=int, default=3)
    parser.add_argument("--output-csv", default=DEFAULT_OUTPUT_CSV)
    parser.add_argument("--output-md", default=DEFAULT_OUTPUT_MD)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    workflow_csv = Path(args.workflow_csv)
    family_csv = Path(args.family_csv)
    output_csv = Path(args.output_csv)
    output_md = Path(args.output_md)

    rows = build_seed_bank(
        workflow_csv,
        family_csv,
        seed_budget=args.seed_budget,
        patterns_per_family=args.patterns_per_family,
    )
    write_csv(rows, output_csv)
    write_markdown(rows, output_md, seed_budget=args.seed_budget)

    total_seed_ideas = sum(int(row["recommended_seed_ideas"]) for row in rows)
    print(f"wrote {len(rows)} seed rows")
    print(f"total recommended seed ideas: {total_seed_ideas}")
    print(output_csv)
    print(output_md)


if __name__ == "__main__":
    main()
