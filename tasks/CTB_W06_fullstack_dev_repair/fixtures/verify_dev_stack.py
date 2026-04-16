#!/usr/bin/env python3
"""Deterministic verification for CTB_W06_fullstack_dev_repair."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path("/workspace/project")
BACKEND_ENV_PATH = ROOT / "config/backend.env"
FRONTEND_ENV_PATH = ROOT / "frontend/.env.local"
PROXY_PATH = ROOT / "proxy/dev_proxy.json"
STATUS_PATH = Path("/workspace/output/dev-stack/dev_stack_status.json")
DOC_PATH = Path("/workspace/DEV_ENV_FIX.md")


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
    backend = load_env(BACKEND_ENV_PATH)
    frontend = load_env(FRONTEND_ENV_PATH)
    proxy = json.loads(PROXY_PATH.read_text())

    backend_ok = (
        backend.get("APP_MODE") == "dev"
        and backend.get("API_PORT") == "9101"
        and backend.get("DB_HOST") == "postgres-dev"
        and backend.get("PUBLIC_API_PATH") == "/api"
        and backend.get("SESSION_MODE") == "local"
        and backend.get("STACK_OUTPUT_DIR") == "/workspace/output/dev-stack"
    )

    frontend_ok = (
        frontend.get("VITE_API_ORIGIN") == "http://localhost:9101"
        and frontend.get("VITE_API_PATH") == "/api"
        and frontend.get("VITE_DEV_PROXY_PORT") == "3000"
        and frontend.get("VITE_LOGIN_MODE") == "local"
    )

    routes = proxy.get("routes") or {}
    proxy_ok = (
        proxy.get("listen") == 3000
        and routes.get("/api") == "http://backend:9101/api"
        and routes.get("/auth") == "http://backend:9101/auth"
        and proxy.get("websocket") == "ws://backend:9101/socket"
    )

    status_ok = False
    if STATUS_PATH.exists():
        try:
            status = json.loads(STATUS_PATH.read_text())
            status_ok = status == {
                "backend_mode": "dev",
                "backend_port": 9101,
                "db_host": "postgres-dev",
                "frontend_port": 3000,
                "public_api_path": "/api",
                "session_mode": "local",
                "status": "healthy",
            }
        except Exception:  # noqa: BLE001
            status_ok = False

    doc_ok = False
    if DOC_PATH.exists():
        text = DOC_PATH.read_text().lower()
        must_have = ["root cause", "根因", "9001", "9101", "api/v2", "local", "proxy", "验证"]
        doc_ok = sum(1 for item in must_have if item.lower() in text) >= 5 and len(text) >= 140

    print(
        json.dumps(
            {
                "backend_ok": backend_ok,
                "frontend_ok": frontend_ok,
                "proxy_ok": proxy_ok,
                "status_ok": status_ok,
                "doc_ok": doc_ok,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
