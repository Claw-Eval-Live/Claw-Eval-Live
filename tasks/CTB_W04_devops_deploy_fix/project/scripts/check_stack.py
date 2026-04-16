#!/usr/bin/env python3
"""Check a simulated compose stack without running Docker."""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml


ROOT = Path("/workspace/project")
COMPOSE_PATH = ROOT / "docker-compose.yaml"
ENV_PATH = ROOT / "config/deploy.env"
GATEWAY_PATH = ROOT / "config/gateway.conf"


def load_env(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"Bad env line: {line}")
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip()
    return result


def require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def main() -> None:
    compose = yaml.safe_load(COMPOSE_PATH.read_text())
    env = load_env(ENV_PATH)
    gateway = GATEWAY_PATH.read_text()
    errors: list[str] = []

    services = compose.get("services") or {}
    require(set(services.keys()) == {"api", "worker", "gateway"}, "services must be api/worker/gateway", errors)

    api = services.get("api") or {}
    worker = services.get("worker") or {}
    gateway_service = services.get("gateway") or {}

    api_hc = (((api.get("healthcheck") or {}).get("test")) or [])
    api_hc_joined = " ".join(str(x) for x in api_hc)

    require(env.get("APP_MODE") == "prod", "APP_MODE must be prod", errors)
    require(env.get("API_PORT") == "8080", "API_PORT must be 8080", errors)
    require(env.get("WORKER_QUEUE") == "critical-jobs", "WORKER_QUEUE must be critical-jobs", errors)
    require(env.get("STACK_OUTPUT_DIR") == "/workspace/output/stack", "STACK_OUTPUT_DIR mismatch", errors)

    require("localhost:8080/healthz" in api_hc_joined, "api healthcheck must target 8080/healthz", errors)

    worker_command = " ".join(str(x) for x in (worker.get("command") or []))
    require("critical-jobs" in worker_command, "worker queue mismatch", errors)
    require("http://api:8080" in worker_command, "worker api url mismatch", errors)

    worker_dep = ((worker.get("depends_on") or {}).get("api") or {})
    require(worker_dep.get("condition") == "service_healthy", "worker must depend on healthy api", errors)

    gateway_ports = gateway_service.get("ports") or []
    require(gateway_ports == ["8088:8088"], "gateway ports must be 8088:8088", errors)

    require(re.search(r"server\s+api:8080;", gateway) is not None, "gateway upstream must target api:8080", errors)
    require(re.search(r"listen\s+8088;", gateway) is not None, "gateway must listen on 8088", errors)
    require("proxy_pass http://api_upstream;" in gateway, "gateway proxy_pass mismatch", errors)

    if errors:
        print("STACK_FAILED")
        for error in errors:
            print(f"- {error}")
        raise SystemExit(1)

    output_dir = Path(env["STACK_OUTPUT_DIR"])
    output_dir.mkdir(parents=True, exist_ok=True)
    status_path = output_dir / "stack_status.json"
    status_path.write_text(json.dumps({
        "services": ["api", "worker", "gateway"],
        "api_port": 8080,
        "gateway_port": 8088,
        "worker_queue": "critical-jobs",
        "status": "healthy",
    }, indent=2, sort_keys=True))
    print(f"STACK_OK {status_path}")


if __name__ == "__main__":
    main()
