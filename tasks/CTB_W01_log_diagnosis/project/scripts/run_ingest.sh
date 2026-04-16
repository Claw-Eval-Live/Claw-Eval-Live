#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

python "${PROJECT_ROOT}/scripts/ingest_runner.py" \
  --config "${PROJECT_ROOT}/config/runtime.env" \
  --input "${PROJECT_ROOT}/data/payload.json"
