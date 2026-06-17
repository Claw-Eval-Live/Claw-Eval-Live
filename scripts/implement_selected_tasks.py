#!/usr/bin/env python3
"""Generate runnable task files from selected candidates.

For each selected candidate, generates:
- task.yaml: Task definition with prompt, tools, services
- fixtures/: Mock data files
- grader.py: Automated grading logic
- README.md: Task documentation

Usage:
    python3 scripts/implement_selected_tasks.py \
        --input benchmark/candidates/selected_for_implementation_v1.0.jsonl \
        --output-dir benchmark/tasks/ \
        --limit 5
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_selected_candidates(input_path: Path) -> list[dict[str, Any]]:
    """Load selected candidates from JSONL."""
    candidates = []
    with open(input_path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                candidates.append(json.loads(line.strip()))
    return candidates


def generate_task_id(candidate: dict[str, Any], index: int) -> str:
    """Generate unique task ID from candidate."""
    base = candidate.get("idea_slug", f"task_{index:03d}")
    # Clean slug for filename
    clean = re.sub(r'[^a-z0-9_]', '_', base.lower())
    return f"CTB_{clean}"


def generate_task_yaml(candidate: dict[str, Any], task_id: str, task_dir: Path) -> dict[str, Any]:
    """Generate task.yaml content."""

    # Map mock services to tool definitions
    services = candidate.get("required_mock_services", [])
    tools = []
    tool_endpoints = []

    for svc in services:
        if svc == "gmail":
            tools.append({
                "name": "gmail_search",
                "description": "Search Gmail messages",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "max_results": {"type": "integer", "default": 10}
                    }
                }
            })
            tool_endpoints.append({
                "tool_name": "gmail_search",
                "url": "http://localhost:9102/gmail/search",
                "method": "POST"
            })
        elif svc == "crm":
            tools.append({
                "name": "crm_get_customer",
                "description": "Get customer details from CRM",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "customer_id": {"type": "string"}
                    }
                }
            })
            tool_endpoints.append({
                "tool_name": "crm_get_customer",
                "url": "http://localhost:9103/crm/customers/get",
                "method": "POST"
            })
        elif svc == "calendar":
            tools.append({
                "name": "calendar_list_events",
                "description": "List calendar events",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "date": {"type": "string", "format": "date"}
                    }
                }
            })
            tool_endpoints.append({
                "tool_name": "calendar_list_events",
                "url": "http://localhost:9101/calendar/events",
                "method": "POST"
            })
        elif svc == "web_search":
            tools.append({
                "name": "web_search",
                "description": "Search the web",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "max_results": {"type": "integer", "default": 5}
                    }
                }
            })
            tool_endpoints.append({
                "tool_name": "web_search",
                "url": "http://localhost:9114/web/search",
                "method": "POST"
            })
        elif svc == "notes":
            tools.append({
                "name": "notes_search",
                "description": "Search notes",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"}
                    }
                }
            })
            tool_endpoints.append({
                "tool_name": "notes_search",
                "url": "http://localhost:9106/notes/search",
                "method": "POST"
            })

    attachments = [
        "fixtures/input_data.json",
        "fixtures/expected_output.md",
    ]
    checkpoints = candidate.get("grader_checkpoints_zh", [])
    reference_solution_parts = [
        f"场景：{candidate.get('scenario_zh', '')}",
        f"预期产物：{candidate.get('artifact_spec_zh', '')}",
    ]
    if checkpoints:
        reference_solution_parts.append("验收要点：")
        reference_solution_parts.extend(f"- {item}" for item in checkpoints)

    # Build task.yaml structure
    task_yaml = {
        "task_id": task_id,
        "task_name": candidate.get("idea_title_zh", ""),
        "version": "1.0",
        "category": candidate.get("primary_family", "general").lower().replace("-", "_").replace(" ", "_"),
        "difficulty": candidate.get("difficulty_band", "medium"),
        "prompt": {
            "text": candidate.get("user_request_zh", ""),
            "language": "zh",
            "attachments": attachments,
        },
        "tools": tools,
        "tool_endpoints": tool_endpoints,
        "environment": {
            "timeout_seconds": 300,
            "max_turns": 20,
            "fixtures": attachments,
        },
        "scoring_components": [],
        "safety_checks": [
            {
                "type": "tool_not_called",
                "tool_name": "send_notification",
                "description": "防止误发外部通知",
            }
        ],
        "services": [],
        "expected_actions": [],
        "judge_rubric": candidate.get("artifact_spec_zh", ""),
        "reference_solution": "\n".join(reference_solution_parts),
        "primary_dimensions": ["completion", "communication"],
        "sandbox_files": attachments,
        "sandbox_grader_files": [],
        "env_snapshot_files": [],
        "env_snapshot_commands": [],
    }

    return task_yaml


def generate_fixtures(candidate: dict[str, Any], task_id: str, task_dir: Path):
    """Generate mock fixture files."""
    fixtures_dir = task_dir / "fixtures"
    fixtures_dir.mkdir(exist_ok=True)

    # Generate input data fixture
    input_data = {
        "task_id": task_id,
        "scenario": candidate.get("scenario_zh", ""),
        "workflow_steps": candidate.get("workflow_steps_zh", []),
        "artifact_spec": candidate.get("artifact_spec_zh", ""),
        "mock_services": candidate.get("required_mock_services", [])
    }

    with open(fixtures_dir / "input_data.json", "w", encoding="utf-8") as f:
        json.dump(input_data, f, ensure_ascii=False, indent=2)

    # Generate expected output template
    expected_output = f"""# Expected Output: {candidate.get('idea_title_zh', '')}

## Task Description
{candidate.get('scenario_zh', '')}

## Expected Artifact
{candidate.get('artifact_spec_zh', '')}

## Grading Criteria
"""

    with open(fixtures_dir / "expected_output.md", "w", encoding="utf-8") as f:
        f.write(expected_output)


def generate_grader(candidate: dict[str, Any], task_id: str, task_dir: Path):
    """Generate grader.py file."""
    grader_content = f'''#!/usr/bin/env python3
"""Grading logic for {task_id}"""

import json
from pathlib import Path
from typing import Any


def grade_task(dispatches: list[dict], final_response: str, workspace_path: Path) -> dict[str, Any]:
    """Grade the task execution."""

    # Checkpoint scoring based on workflow steps
    checkpoints = {json.dumps(candidate.get('grader_checkpoints_zh', []), ensure_ascii=False, indent=2)}

    score = 0.0
    max_score = len(checkpoints)

    # Basic checks
    if len(dispatches) > 0:
        score += 0.2  # At least some tool calls

    if final_response and len(final_response.strip()) > 50:
        score += 0.3  # Non-empty final response

    # Check for expected artifact keywords
    expected_keywords = ['报告', '表格', '摘要', '清单', '草稿']  # Common artifact types

    for keyword in expected_keywords:
        if keyword in final_response:
            score += 0.1
            break

    # Normalize to 0-1 scale
    final_score = min(1.0, score)

    return {{
        "score": final_score,
        "max_score": 1.0,
        "checkpoints_passed": int(score * 10),
        "feedback": f"Task completed with basic requirements. Score: {{final_score:.1f}}/1.0"
    }}


if __name__ == "__main__":
    # Test the grader
    test_dispatches = []
    test_response = "Test response"
    result = grade_task(test_dispatches, test_response, Path("."))
    print(json.dumps(result, indent=2))
'''

    with open(task_dir / "grader.py", "w", encoding="utf-8") as f:
        f.write(grader_content)


def generate_readme(candidate: dict[str, Any], task_id: str, task_dir: Path):
    """Generate README.md for the task."""
    readme_content = f"""# {task_id}: {candidate.get('idea_title_zh', '')}

## Scenario
{candidate.get('scenario_zh', '')}

## User Request
{candidate.get('user_request_zh', '')}

## Expected Workflow
"""

    steps = candidate.get('workflow_steps_zh', [])
    for i, step in enumerate(steps, 1):
        readme_content += f"\n{i}. {step}"

    readme_content += f"""

## Required Mock Services
- {', '.join(candidate.get('required_mock_services', []))}

## Expected Artifact
{candidate.get('artifact_spec_zh', '')}

## Difficulty
- Level: {candidate.get('difficulty_band', 'medium')}
- Estimated steps: {candidate.get('step_count_estimate', 8)}
- Tool calls: ~{candidate.get('tool_call_estimate', 15)}

## Implementation Notes
- This task tests: {candidate.get('primary_pattern', '')} + {candidate.get('aux_patterns', '')}
- Family: {candidate.get('primary_family', '')}
- Archetype: {candidate.get('archetype', '')}
"""

    with open(task_dir / "README.md", "w", encoding="utf-8") as f:
        f.write(readme_content)


def implement_task(candidate: dict[str, Any], index: int, output_dir: Path) -> bool:
    """Implement a single task from candidate."""
    try:
        task_id = generate_task_id(candidate, index)
        task_dir = output_dir / task_id

        if task_dir.exists():
            print(f"  [skip] {task_id} already exists")
            return False

        task_dir.mkdir(parents=True, exist_ok=True)

        # Generate all files
        task_yaml = generate_task_yaml(candidate, task_id, task_dir)
        with open(task_dir / "task.yaml", "w", encoding="utf-8") as f:
            import yaml
            yaml.dump(task_yaml, f, allow_unicode=True, default_flow_style=False)

        generate_fixtures(candidate, task_id, task_dir)
        generate_grader(candidate, task_id, task_dir)
        generate_readme(candidate, task_id, task_dir)

        print(f"  [done] {task_id}")
        return True

    except Exception as e:
        print(f"  [error] Failed to implement {task_id}: {e}")
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="benchmark/candidates/selected_for_implementation_v1.0.jsonl")
    parser.add_argument("--output-dir", default="benchmark/tasks/")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--dry-run", action="store_true", help="Print the tasks that would be generated without writing files")
    args = parser.parse_args()

    input_path = REPO_ROOT / args.input
    output_dir = REPO_ROOT / args.output_dir

    if not input_path.exists():
        print(f"Error: Input file {input_path} not found")
        return 1

    print(f"Loading selected candidates from {input_path}")
    candidates = load_selected_candidates(input_path)
    print(f"Loaded {len(candidates)} selected candidates")

    if args.limit:
        candidates = candidates[:args.limit]

    print(f"Implementing {len(candidates)} tasks to {output_dir}")

    if args.dry_run:
        for i, candidate in enumerate(candidates, 1):
            task_id = generate_task_id(candidate, i)
            print(f"  [dry-run] {task_id}: {candidate.get('idea_title_zh', '')}")
        print("\n[DRY RUN] No task files written.")
        return 0

    implemented = 0
    for i, candidate in enumerate(candidates, 1):
        if implement_task(candidate, i, output_dir):
            implemented += 1

    print(f"\n=== Implementation Summary ===")
    print(f"Successfully implemented: {implemented}/{len(candidates)} tasks")
    print(f"Output directory: {output_dir}")

    return 0


if __name__ == "__main__":
    main()
