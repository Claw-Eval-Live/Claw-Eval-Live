"""Stage 2-4 of the benchmark signal pipeline.

Stage 2: Cluster skills into workflow patterns
Stage 3: Map patterns to Families, derive market weights
Stage 4: Compare market weights vs current task distribution, find gaps

Usage:
    python scripts/derive_benchmark_weights.py \
        --input benchmark/signals/clawhub_top_500.csv \
        --output-dir benchmark/signals

Produces:
    - workflow_patterns.csv      (Stage 2)
    - family_weights.csv         (Stage 3)
    - coverage_gap.csv           (Stage 4)
    - pipeline_report.md         (human-readable summary)
"""

import argparse
import csv
import re
from collections import defaultdict
from pathlib import Path

# ──────────────────────────────────────────────
# Stage 2: Workflow Pattern definitions
# ──────────────────────────────────────────────
# Each pattern has:
#   - keywords that match against skill name+summary (lowercase)
#   - the Family it maps to in Stage 3

WORKFLOW_PATTERNS = {
    # ── Document-Transform ──
    "doc_summarization": {
        "keywords": ["summarize", "summarise", "summary", "condense", "tldr", "digest"],
        "family": "Document-Transform",
        "label": "Document Summarization",
    },
    "pdf_processing": {
        "keywords": ["pdf", "nano-pdf", "nano pdf"],
        "family": "Document-Transform",
        "label": "PDF Processing",
    },
    "format_conversion": {
        "keywords": ["markdown convert", "word", "docx", "xlsx", "excel", "csv convert",
                      "html to", "to markdown", "to json", "to csv", "converter"],
        "family": "Document-Transform",
        "label": "Format Conversion",
    },
    "ocr_extraction": {
        "keywords": ["ocr", "extract text", "extract data from", "transcri", "parse document"],
        "family": "Document-Transform",
        "label": "OCR & Text Extraction",
    },
    "content_rewriting": {
        "keywords": ["humanize", "rewrite", "paraphrase", "rephrase", "proofread",
                      "grammar", "copyedit", "blog writ"],
        "family": "Document-Transform",
        "label": "Content Rewriting & Editing",
    },

    # ── Research-to-Artifact ──
    "web_research": {
        "keywords": ["research", "deep research", "investigate", "literature review",
                      "competitive analysis", "market research", "due diligence"],
        "family": "Research-to-Artifact",
        "label": "Web Research & Reports",
    },
    "web_search": {
        "keywords": ["web search", "duckduckgo", "tavily", "serp", "google search",
                      "bing search", "search engine"],
        "family": "Research-to-Artifact",
        "label": "Web Search Tools",
    },
    "news_monitoring": {
        "keywords": ["news", "rss", "feed", "monitor", "alert", "trending",
                      "daily brief", "morning brief"],
        "family": "Research-to-Artifact",
        "label": "News & Feed Monitoring",
    },
    "academic_research": {
        "keywords": ["arxiv", "paper", "citation", "academic", "scholar", "pubmed",
                      "journal", "doi"],
        "family": "Research-to-Artifact",
        "label": "Academic & Paper Research",
    },

    # ── Cross-Tool-Orchestration ──
    "workflow_automation": {
        "keywords": ["workflow", "automat", "n8n", "zapier", "make.com", "pipedream",
                      "trigger", "orchestrat"],
        "family": "Cross-Tool-Orchestration",
        "label": "Workflow Automation & Orchestration",
    },
    "calendar_scheduling": {
        "keywords": ["calendar", "schedule", "appointment", "meeting", "gcal",
                      "ical", "booking"],
        "family": "Cross-Tool-Orchestration",
        "label": "Calendar & Scheduling",
    },
    "task_management": {
        "keywords": ["todo", "todoist", "task manage", "project manage", "kanban",
                      "trello", "asana", "jira", "linear"],
        "family": "Cross-Tool-Orchestration",
        "label": "Task & Project Management",
    },
    "note_knowledge": {
        "keywords": ["notion", "obsidian", "note", "joplin", "knowledge base",
                      "wiki", "roam", "logseq", "second brain", "pkm"],
        "family": "Cross-Tool-Orchestration",
        "label": "Notes & Knowledge Management",
    },
    "multi_service_integration": {
        "keywords": ["integration", "sync", "connect", "bridge", "webhook",
                      "api gateway"],
        "family": "Cross-Tool-Orchestration",
        "label": "Multi-Service Integration",
    },

    # ── Data-Analysis-Reporting ──
    "data_analysis": {
        "keywords": ["data analy", "statistic", "visualization", "chart",
                      "dashboard", "plot", "graph", "insight"],
        "family": "Data-Analysis-Reporting",
        "label": "Data Analysis & Visualization",
    },
    "financial_analysis": {
        "keywords": ["stock", "finance", "crypto", "trading", "portfolio",
                      "investment", "revenue", "earnings", "fiscal",
                      "ticker", "yahoo finance", "tushare"],
        "family": "Data-Analysis-Reporting",
        "label": "Financial & Investment Analysis",
    },
    "spreadsheet_database": {
        "keywords": ["spreadsheet", "sql", "database", "postgres", "mysql",
                      "sqlite", "table", "query", "airtable", "supabase"],
        "family": "Data-Analysis-Reporting",
        "label": "Spreadsheet & Database Operations",
    },
    "accounting_expense": {
        "keywords": ["invoice", "expense", "budget", "accounting", "bookkeep",
                      "receipt", "tax", "payroll"],
        "family": "Data-Analysis-Reporting",
        "label": "Accounting & Expense Tracking",
    },

    # ── Workspace-Repair-Config ──
    "debugging": {
        "keywords": ["debug", "troubleshoot", "diagnose", "fix bug", "error",
                      "stack trace", "breakpoint"],
        "family": "Workspace-Repair-Config",
        "label": "Debugging & Troubleshooting",
    },
    "devops_infra": {
        "keywords": ["docker", "kubernetes", "k8s", "deploy", "ci/cd", "ci cd",
                      "terraform", "ansible", "nginx", "server"],
        "family": "Workspace-Repair-Config",
        "label": "DevOps & Infrastructure",
    },
    "git_version_control": {
        "keywords": ["git", "github", "gitlab", "version control", "branch",
                      "merge", "commit", "pull request", "pr review"],
        "family": "Workspace-Repair-Config",
        "label": "Git & Version Control",
    },
    "shell_terminal": {
        "keywords": ["shell", "terminal", "bash", "command line", "cli",
                      "zsh", "ssh", "tmux"],
        "family": "Workspace-Repair-Config",
        "label": "Shell & Terminal Operations",
    },
    "file_system": {
        "keywords": ["file manag", "backup", "archive", "compress", "zip",
                      "rename", "organiz", "cleanup", "storage"],
        "family": "Workspace-Repair-Config",
        "label": "File System & Backup",
    },
    "config_management": {
        "keywords": ["config", "env", "dotfile", "setting", "preference",
                      "yaml", "toml", ".env"],
        "family": "Workspace-Repair-Config",
        "label": "Configuration Management",
    },

    # ── Communication-Drafting ──
    "email_management": {
        "keywords": ["email", "inbox", "gmail", "outlook", "mail", "smtp", "imap"],
        "family": "Communication-Drafting",
        "label": "Email Management & Drafting",
    },
    "messaging_chat": {
        "keywords": ["slack", "discord", "telegram", "whatsapp", "teams",
                      "chat", "sms", "imessage", "signal"],
        "family": "Communication-Drafting",
        "label": "Messaging & Chat Platforms",
    },
    "marketing_content": {
        "keywords": ["marketing", "seo", "copywriting", "content creat",
                      "social media", "campaign", "advertis", "newsletter"],
        "family": "Communication-Drafting",
        "label": "Marketing & Content Creation",
    },
    "crm_sales": {
        "keywords": ["crm", "sales", "lead", "pipeline", "customer",
                      "hubspot", "salesforce", "outreach"],
        "family": "Communication-Drafting",
        "label": "CRM & Sales Communication",
    },

    # ── Browser-Automation (maps to multiple families) ──
    "browser_automation": {
        "keywords": ["browser", "scraping", "scrape", "crawl", "puppeteer",
                      "playwright", "selenium", "screenshot", "headless"],
        "family": "Research-to-Artifact",
        "label": "Browser Automation & Scraping",
    },

    # ── Coding (partial overlap with Repair) ──
    "coding_tools": {
        "keywords": ["code review", "refactor", "linter", "testing", "test",
                      "python", "javascript", "typescript", "rust", "golang"],
        "family": "Workspace-Repair-Config",
        "label": "Coding & Development Tools",
    },

    # ── Media (secondary) ──
    "media_processing": {
        "keywords": ["video", "audio", "image", "photo", "ffmpeg", "whisper",
                      "tts", "speech", "music", "podcast", "youtube"],
        "family": "Document-Transform",
        "label": "Media Processing",
    },

    # ── Translation ──
    "translation": {
        "keywords": ["translat", "locali", "i18n", "multilingual", "language"],
        "family": "Document-Transform",
        "label": "Translation & Localization",
    },

    # ── Discovered Patterns (v2, 2026-03-26) ──
    "presentation_generation": {
        "keywords": ["ppt", "slides", "presentation", "演示", "幻灯片", "keynote", "reveal"],
        "family": "Document-Transform",
        "label": "Presentation & Slides Generation",
    },
    "ui_frontend_design": {
        "keywords": ["ui design", "ux design", "frontend design", "shadcn",
                      "tailwind component", "react component", "figma",
                      "interface design", "ui/ux"],
        "family": "Workspace-Repair-Config",
        "label": "UI/UX & Frontend Design",
    },
    "security_auditing": {
        "keywords": ["security audit", "vulnerability", "vetting", "injection detect",
                      "antivirus", "security scan", "guard", "security check"],
        "family": "Workspace-Repair-Config",
        "label": "Security Auditing & Vetting",
    },
    "china_platform_integration": {
        "keywords": ["feishu", "飞书", "dingtalk", "钉钉", "小红书", "bilibili", "b站",
                      "抖音", "douyin", "微信", "weixin", "akshare", "a股"],
        "family": "Cross-Tool-Orchestration",
        "label": "Chinese Platform Integration",
    },
}


def classify_skill(name: str, summary: str) -> list[str]:
    """Return list of matching workflow pattern IDs."""
    text = (name + " " + summary).lower()
    matches = []
    for pat_id, pat in WORKFLOW_PATTERNS.items():
        for kw in pat["keywords"]:
            if kw in text:
                matches.append(pat_id)
                break
    return matches if matches else ["uncategorized"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="benchmark/signals/clawhub_top_500.csv")
    parser.add_argument("--output-dir", default="benchmark/signals")
    parser.add_argument("--current-tasks", type=int, default=104,
                        help="Total tasks in current claw-eval")
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Load skills
    with open(args.input, encoding="utf-8") as f:
        skills = list(csv.DictReader(f))
    print(f"Loaded {len(skills)} skills\n")

    # ── Stage 2: Classify into workflow patterns ──
    pattern_skills: dict[str, list[dict]] = defaultdict(list)
    pattern_downloads: dict[str, int] = defaultdict(int)
    skill_patterns: list[dict] = []

    for s in skills:
        dl = int(s["downloads"])
        pats = classify_skill(s["name"], s["summary"])
        for pat_id in pats:
            pattern_skills[pat_id].append(s)
            pattern_downloads[pat_id] += dl
        skill_patterns.append({
            "slug": s["slug"],
            "name": s["name"],
            "downloads": dl,
            "patterns": "|".join(pats),
        })

    # Write workflow_patterns.csv
    pat_rows = []
    for pat_id in sorted(pattern_downloads, key=lambda x: -pattern_downloads[x]):
        info = WORKFLOW_PATTERNS.get(pat_id, {})
        top_skills = sorted(pattern_skills[pat_id],
                           key=lambda x: -int(x["downloads"]))[:5]
        pat_rows.append({
            "pattern_id": pat_id,
            "label": info.get("label", "Uncategorized"),
            "family": info.get("family", "—"),
            "skill_count": len(pattern_skills[pat_id]),
            "total_downloads": pattern_downloads[pat_id],
            "top_skills": ", ".join(s["name"] for s in top_skills),
        })

    with open(out_dir / "workflow_patterns.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[
            "pattern_id", "label", "family", "skill_count",
            "total_downloads", "top_skills"])
        w.writeheader()
        w.writerows(pat_rows)

    print("=== Stage 2: Workflow Patterns ===\n")
    print(f"{'Pattern':<35} {'Family':<28} {'Skills':>6} {'Downloads':>12}")
    print("-" * 85)
    for r in pat_rows:
        print(f"{r['label']:<35} {r['family']:<28} {r['skill_count']:>6} {r['total_downloads']:>12,}")

    # ── Stage 3: Aggregate to Family weights ──
    family_downloads: dict[str, int] = defaultdict(int)
    family_skills: dict[str, int] = defaultdict(int)
    family_patterns: dict[str, list[str]] = defaultdict(list)

    for pat_id, dl in pattern_downloads.items():
        info = WORKFLOW_PATTERNS.get(pat_id, {})
        fam = info.get("family", "Other")
        family_downloads[fam] += dl
        family_skills[fam] += len(pattern_skills[pat_id])
        family_patterns[fam].append(info.get("label", pat_id))

    total_dl = sum(family_downloads.values())
    family_rows = []
    for fam in sorted(family_downloads, key=lambda x: -family_downloads[x]):
        weight = family_downloads[fam] / total_dl if total_dl > 0 else 0
        family_rows.append({
            "family": fam,
            "total_downloads": family_downloads[fam],
            "skill_count": family_skills[fam],
            "market_weight": round(weight, 4),
            "market_weight_pct": f"{weight*100:.1f}%",
            "patterns": ", ".join(family_patterns[fam]),
        })

    with open(out_dir / "family_weights.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[
            "family", "total_downloads", "skill_count",
            "market_weight", "market_weight_pct", "patterns"])
        w.writeheader()
        w.writerows(family_rows)

    print(f"\n=== Stage 3: Family Market Weights ===\n")
    print(f"{'Family':<30} {'Downloads':>12} {'Skills':>7} {'Weight':>8}")
    print("-" * 60)
    for r in family_rows:
        print(f"{r['family']:<30} {r['total_downloads']:>12,} {r['skill_count']:>7} {r['market_weight_pct']:>8}")

    # ── Stage 4: Coverage gap analysis ──
    # Current claw-eval distribution (from task_audit_v0.1.md)
    current_dist = {
        "Research-to-Artifact": 34,
        "Data-Analysis-Reporting": 24,
        "Document-Transform": 18,
        "Cross-Tool-Orchestration": 16,
        "Workspace-Repair-Config": 8,
        "Communication-Drafting": 4,
    }
    current_total = sum(current_dist.values())

    gap_rows = []
    for r in family_rows:
        fam = r["family"]
        mw = r["market_weight"]
        current_count = current_dist.get(fam, 0)
        current_ratio = current_count / current_total if current_total > 0 else 0
        gap = mw - current_ratio
        suggested = round(mw * 72)  # target 72 tasks total

        if gap > 0.05:
            action = "PRIORITY: significantly under-represented"
        elif gap > 0.02:
            action = "ADD: moderately under-represented"
        elif gap < -0.05:
            action = "REDUCE: over-represented, select best only"
        elif gap < -0.02:
            action = "TRIM: slightly over-represented"
        else:
            action = "OK: roughly balanced"

        gap_rows.append({
            "family": fam,
            "market_weight": f"{mw*100:.1f}%",
            "current_ratio": f"{current_ratio*100:.1f}%",
            "gap": f"{gap*100:+.1f}%",
            "current_tasks": current_count,
            "suggested_tasks_in_72": suggested,
            "action": action,
        })

    with open(out_dir / "coverage_gap.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[
            "family", "market_weight", "current_ratio", "gap",
            "current_tasks", "suggested_tasks_in_72", "action"])
        w.writeheader()
        w.writerows(gap_rows)

    print(f"\n=== Stage 4: Coverage Gap Analysis ===\n")
    print(f"{'Family':<30} {'Market':>7} {'Current':>8} {'Gap':>7} {'Now':>4} {'Target':>6} Action")
    print("-" * 105)
    for r in gap_rows:
        print(f"{r['family']:<30} {r['market_weight']:>7} {r['current_ratio']:>8} "
              f"{r['gap']:>7} {r['current_tasks']:>4} {r['suggested_tasks_in_72']:>6}   {r['action']}")

    # ── Write pipeline report ──
    report = []
    report.append("# Benchmark Signal Pipeline Report\n")
    report.append(f"> Generated from {len(skills)} skills in `{args.input}`\n")

    report.append("\n## Stage 2: Workflow Patterns\n")
    report.append(f"| Pattern | Family | Skills | Downloads |")
    report.append(f"|---------|--------|--------|-----------|")
    for r in pat_rows:
        report.append(f"| {r['label']} | {r['family']} | {r['skill_count']} | {r['total_downloads']:,} |")

    report.append(f"\n## Stage 3: Family Market Weights\n")
    report.append(f"| Family | Downloads | Skills | Market Weight |")
    report.append(f"|--------|-----------|--------|---------------|")
    for r in family_rows:
        report.append(f"| {r['family']} | {r['total_downloads']:,} | {r['skill_count']} | {r['market_weight_pct']} |")

    report.append(f"\n## Stage 4: Coverage Gap Analysis\n")
    report.append(f"| Family | Market | Current | Gap | Now | Target(72) | Action |")
    report.append(f"|--------|--------|---------|-----|-----|------------|--------|")
    for r in gap_rows:
        report.append(f"| {r['family']} | {r['market_weight']} | {r['current_ratio']} | "
                       f"{r['gap']} | {r['current_tasks']} | {r['suggested_tasks_in_72']} | {r['action']} |")

    report.append(f"\n## Key Takeaways\n")
    priority_families = [r for r in gap_rows if "PRIORITY" in r["action"] or "ADD" in r["action"]]
    over_families = [r for r in gap_rows if "REDUCE" in r["action"] or "TRIM" in r["action"]]
    if priority_families:
        report.append("### Under-represented (need more tasks)\n")
        for r in priority_families:
            report.append(f"- **{r['family']}**: market={r['market_weight']}, current={r['current_ratio']}, gap={r['gap']}")
    if over_families:
        report.append("\n### Over-represented (select best, don't add more)\n")
        for r in over_families:
            report.append(f"- **{r['family']}**: market={r['market_weight']}, current={r['current_ratio']}, gap={r['gap']}")

    report.append("\n## Methodology\n")
    report.append("1. **Signal Collection**: ClawHub public API, keyword-based broad search")
    report.append("2. **Pattern Clustering**: Rule-based keyword matching against skill name+summary")
    report.append(f"3. **Pattern Count**: {len(WORKFLOW_PATTERNS)} predefined workflow patterns")
    report.append("4. **Family Mapping**: Each pattern maps to exactly one Family")
    report.append("5. **Weight Calculation**: Family weight = sum(downloads of all skills in family patterns) / total downloads")
    report.append("6. **Gap Analysis**: gap = market_weight - current_task_ratio")
    report.append("\n### Limitations\n")
    report.append("- Keyword-based clustering may miss semantic matches")
    report.append("- Some skills match multiple patterns (counted in each)")
    report.append("- Downloads ≠ importance (popular ≠ complex)")
    report.append("- ClawHub coverage is not exhaustive of all real-world workflows")
    report.append("- Should be supplemented with human review and domain expertise")

    with open(out_dir / "pipeline_report.md", "w", encoding="utf-8") as f:
        f.write("\n".join(report))

    print(f"\n=== Output Files ===\n")
    print(f"  {out_dir / 'workflow_patterns.csv'}")
    print(f"  {out_dir / 'family_weights.csv'}")
    print(f"  {out_dir / 'coverage_gap.csv'}")
    print(f"  {out_dir / 'pipeline_report.md'}")


if __name__ == "__main__":
    main()
