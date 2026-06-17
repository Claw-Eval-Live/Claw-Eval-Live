"""Fetch top skills from ClawHub public API for benchmark signal analysis.

Usage:
    python scripts/fetch_skill_signals.py [--top N] [--output PATH]

Strategy:
  1. Search ClawHub API with ~80 diverse keywords covering all relevant categories
  2. Collect unique skill slugs
  3. Batch query detail API for download/star stats
  4. Rank by downloads, output CSV + summary
"""

import argparse
import csv
import json
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError
from urllib.parse import quote

SEARCH_URL = "https://clawhub.ai/api/search?q={query}"
DETAIL_URL = "https://clawhub.ai/api/skill?slug={slug}"

SEARCH_KEYWORDS = [
    # Productivity & Office
    "email", "calendar", "task management", "todo", "notes",
    "summarize", "document", "spreadsheet", "pdf", "report",
    "meeting", "schedule", "reminder", "agenda", "memo",
    "writing", "draft", "template", "knowledge base", "wiki",
    "obsidian", "notion", "joplin", "bookmark", "clipboard",
    # Data & Analysis
    "data analysis", "csv", "json", "database", "sql",
    "visualization", "chart", "statistics", "excel", "table",
    "data cleaning", "data transform", "scraping", "parsing",
    # Web & Browser
    "browser", "web search", "web automation", "screenshot",
    "http", "api", "rest", "url", "download",
    # Development & DevOps
    "github", "git", "code review", "debug", "deploy",
    "docker", "kubernetes", "ci cd", "terminal", "shell",
    "python", "javascript", "typescript", "testing",
    # Finance & Business
    "finance", "stock", "crypto", "invoice", "budget",
    "crm", "sales", "marketing", "accounting", "expense",
    # Communication
    "slack", "discord", "telegram", "chat", "notification",
    "sms", "whatsapp", "teams",
    # Files & System
    "file manager", "backup", "sync", "storage", "archive",
    "rename", "convert", "compress", "image", "video",
    # AI & Automation
    "automation", "workflow", "agent", "llm", "prompt",
    "rag", "embedding", "translate", "ocr", "transcribe",
    # Research
    "research", "paper", "citation", "arxiv", "academic",
    "news", "rss", "feed", "monitor",
]


def api_get(url: str, retries: int = 2) -> dict | None:
    for attempt in range(retries + 1):
        try:
            req = Request(url, headers={"User-Agent": "claw-bench-signal/0.1"})
            with urlopen(req, timeout=10) as resp:
                return json.loads(resp.read())
        except (URLError, TimeoutError, json.JSONDecodeError) as e:
            if attempt < retries:
                time.sleep(1 * (attempt + 1))
            else:
                print(f"  [WARN] Failed: {url} — {e}", file=sys.stderr)
                return None


def collect_slugs() -> set[str]:
    slugs = set()
    total = len(SEARCH_KEYWORDS)
    for i, kw in enumerate(SEARCH_KEYWORDS, 1):
        print(f"\r  Searching [{i}/{total}]: {kw:<30}", end="", flush=True)
        data = api_get(SEARCH_URL.format(query=quote(kw)))
        if data and "results" in data:
            for r in data["results"]:
                slugs.add(r["slug"])
        time.sleep(0.3)
    print(f"\n  Unique slugs collected: {len(slugs)}")
    return slugs


def fetch_details(slugs: set[str]) -> list[dict]:
    skills = []
    total = len(slugs)
    for i, slug in enumerate(sorted(slugs), 1):
        if i % 20 == 0 or i == total:
            print(f"\r  Fetching details [{i}/{total}]", end="", flush=True)
        data = api_get(DETAIL_URL.format(slug=slug))
        if data and "skill" in data:
            s = data["skill"]
            stats = s.get("stats", {})
            skills.append({
                "slug": s.get("slug", slug),
                "name": s.get("displayName", slug),
                "summary": s.get("summary", ""),
                "downloads": stats.get("downloads", 0),
                "stars": stats.get("stars", 0),
                "installs_current": stats.get("installsCurrent", 0),
                "installs_all_time": stats.get("installsAllTime", 0),
                "comments": stats.get("comments", 0),
                "versions": stats.get("versions", 0),
                "owner": data.get("owner", {}).get("handle", ""),
            })
        time.sleep(0.2)
    print()
    return skills


def main():
    parser = argparse.ArgumentParser(description="Fetch ClawHub skill signals")
    parser.add_argument("--top", type=int, default=500, help="Output top N by downloads")
    parser.add_argument("--output", type=str, default=None, help="Output CSV path")
    args = parser.parse_args()

    print("=== ClawHub Skill Signal Collector ===\n")

    print("Step 1: Collecting slugs via search API...")
    slugs = collect_slugs()

    print(f"\nStep 2: Fetching detail stats for {len(slugs)} skills...")
    skills = fetch_details(slugs)

    skills.sort(key=lambda x: x["downloads"], reverse=True)
    top_skills = skills[:args.top]

    out_path = args.output or f"benchmark/signals/clawhub_top_{len(top_skills)}.csv"
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "rank", "slug", "name", "downloads", "stars",
            "installs_current", "installs_all_time",
            "comments", "versions", "owner", "summary",
        ])
        writer.writeheader()
        for i, s in enumerate(top_skills, 1):
            writer.writerow({"rank": i, **s})

    print(f"\nStep 3: Output written to {out_path}")
    print(f"  Total skills fetched: {len(skills)}")
    print(f"  Top {len(top_skills)} by downloads saved\n")

    print("=== Top 30 Preview ===\n")
    print(f"{'Rank':<5} {'Downloads':>10} {'Stars':>6} {'Slug':<40} {'Name'}")
    print("-" * 90)
    for i, s in enumerate(top_skills[:30], 1):
        print(f"{i:<5} {s['downloads']:>10,} {s['stars']:>6} {s['slug']:<40} {s['name']}")

    print(f"\n=== Category Distribution (top {len(top_skills)}) ===\n")
    kw_hits: dict[str, int] = {}
    for s in top_skills:
        text = (s["name"] + " " + s["summary"]).lower()
        for kw_group, keywords in [
            ("Productivity/Office", ["email", "calendar", "task", "todo", "note", "document", "meeting", "schedule", "memo", "write", "draft"]),
            ("Data/Analysis", ["data", "csv", "database", "sql", "chart", "statistic", "excel", "table", "clean", "transform", "scrap"]),
            ("Web/Browser", ["browser", "web", "search", "screenshot", "http", "url", "download"]),
            ("Dev/DevOps", ["github", "git", "code", "debug", "deploy", "docker", "kubernetes", "terminal", "shell", "python", "test"]),
            ("Finance/Business", ["finance", "stock", "crypto", "invoice", "budget", "crm", "sales", "market", "account", "expense"]),
            ("Communication", ["slack", "discord", "telegram", "chat", "notification", "sms", "teams"]),
            ("Files/System", ["file", "backup", "sync", "storage", "archive", "rename", "convert", "compress", "image", "video"]),
            ("AI/Automation", ["automat", "workflow", "agent", "llm", "prompt", "rag", "embed", "translat", "ocr", "transcri"]),
        ]:
            for kw in keywords:
                if kw in text:
                    kw_hits[kw_group] = kw_hits.get(kw_group, 0) + 1
                    break
    for group, count in sorted(kw_hits.items(), key=lambda x: -x[1]):
        print(f"  {group:<25} {count:>4} skills")


if __name__ == "__main__":
    main()
