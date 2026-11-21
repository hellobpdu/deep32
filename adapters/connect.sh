#!/usr/bin/env bash
# Direct MCP and relay connector; arguments are forwarded literally.
set -euo pipefail
kit="$(cd "$(dirname "$0")" && pwd)"
exec "${DEEP32_CONNECT_PYTHON:-python3}" -I -B "$kit/connect.py" "$@"
