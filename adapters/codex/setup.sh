#!/usr/bin/env bash
# Install all six Codex skill directories, merging guidance and optional MCP config.
# {{WORKSTATION_MCP_URL}} is replaced by the shared helper; DEEP32_MCP_TOKEN
# remains an environment reference. No credential or service is opened here.
set -euo pipefail
kit="$(cd "$(dirname "$0")" && pwd)"
target="${CODEX_HOME:-$HOME/.codex}"
url=""
while [ $# -gt 0 ]; do
  case "$1" in
    --codex-home) [ $# -ge 2 ] || exit 2; target="$2"; shift 2 ;;
    --workstation-url) [ $# -ge 2 ] || exit 2; url="$2"; shift 2 ;;
    --help|-h) echo 'Usage: setup.sh [--codex-home DIR] [--workstation-url https://HOST[:PORT]/mcp]'; exit 0 ;;
    *) echo 'Unknown setup argument.'; exit 2 ;;
  esac
done
if [ -n "${DEEP32_PYTHON:-}" ]; then python="$DEEP32_PYTHON"
elif command -v python3 >/dev/null 2>&1; then python="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then python="$(command -v python)"
else echo 'Python 3.10 or newer was not found; run the inventory prerequisite check.'; exit 1; fi
# Native Windows Python needs a native path when this wrapper runs in Git Bash.
if command -v cygpath >/dev/null 2>&1; then target="$(cygpath -am "$target")"; fi
exec "$python" -I -B "$kit/../setup_skills.py" --agent codex --home "$target" --workstation-url "$url"
