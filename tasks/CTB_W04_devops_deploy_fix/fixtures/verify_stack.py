#!/usr/bin/env python3
"""Deterministic verification for CTB_W04_devops_deploy_fix."""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml


ROOT = Path("/workspace/project")
COMPOSE_PATH = ROOT / "docker-compose.yaml"
ENV_PATH = ROOT / "config/deploy.env"
GATEWAY_PATH = ROOT / "config/gateway.conf"
STATUS_PATH = Path("/workspace/output/stack/stack_status.json")
DOC_PATH = Path("/workspace/DEPLOY_FIX.md")


def load_env(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip()
    return result


def main() -> None:
    compose = yaml.safe_load(COMPOSE_PATH.read_text())
    env = load_env(ENV_PATH)
    gateway = GATEWAY_PATH.read_text()

    services = compose.get("services") or {}
    api = services.get("api") or {}
    worker = services.get("worker") or {}
    gateway_service = services.get("gateway") or {}

    compose_ok = (
        set(services.keys()) == {"api", "worker", "gateway"}
        and "localhost:8080/healthz" in " ".join(str(x) for x in (((api.get("healthcheck") or {}).get("test")) or []))
        and "http://api:8080" in " ".join(str(x) for x in (worker.get("command") or []))
        and "critical-jobs" in " ".join(str(x) for x in (worker.get("command") or []))
        and ((worker.get("depends_on") or {}).get("api") or {}).get("condition") == "service_healthy"
        and gateway_service.get("ports") == ["8088:8088"]
    )

    env_ok = (
        env.get("APP_MODE") == "prod"
        and env.get("API_PORT") == "8080"
        and env.get("WORKER_QUEUE") == "critical-jobs"
        and env.get("STACK_OUTPUT_DIR") == "/workspace/output/stack"
    )

    gateway_ok = (
        re.search(r"server\s+api:8080;", gateway) is not None
        and re.search(r"listen\s+8088;", gateway) is not None
        and "proxy_pass http://api_upstream;" in gateway
    )

    doc_ok = False
    if DOC_PATH.exists():
        text = DOC_PATH.read_text().lower()
        must_have = ["root cause", "根因", "app:8090", "api:8080", "gateway", "compose", "验证"]
        doc_ok = sum(1 for item in must_have if item.lower() in text) >= 4 and len(text) >= 120

    status_ok = False
    if STATUS_PATH.exists():
        try:
            status = json.loads(STATUS_PATH.read_text())
            status_ok = status == {
                "api_port": 8080,
                "gateway_port": 8088,
                "services": ["api", "worker", "gateway"],
                "status": "healthy",
                "worker_queue": "critical-jobs",
            }
        except Exception:  # noqa: BLE001
            status_ok = False

    print(json.dumps({
        "compose_ok": compose_ok,
        "env_ok": env_ok,
        "gateway_ok": gateway_ok,
        "doc_ok": doc_ok,
        "status_ok": status_ok,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
