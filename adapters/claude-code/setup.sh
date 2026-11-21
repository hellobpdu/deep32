#!/usr/bin/env bash
# Install the complete Claude research skill and merge its marked guidance.
# Optional MCP config uses ${DEEP32_MCP_TOKEN}; setup never reads a credential.
set -euo pipefail
kit="$(cd "$(dirname "$0")" && pwd)"
target="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
url=""
while [ $# -gt 0 ]; do
  case "$1" in
    --claude-home) [ $# -ge 2 ] || exit 2; target="$2"; shift 2 ;;
    --workstation-url) [ $# -ge 2 ] || exit 2; url="$2"; shift 2 ;;
    --help|-h) echo 'Usage: setup.sh [--claude-home DIR] [--workstation-url https://HOST[:PORT]/mcp]'; exit 0 ;;
    *) echo 'Unknown setup argument.'; exit 2 ;;
  esac
done
if [ -n "${DEEP32_PYTHON:-}" ]; then python="$DEEP32_PYTHON"
elif command -v python3 >/dev/null 2>&1; then python="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then python="$(command -v python)"
else echo 'Python 3.10 or newer was not found; run the inventory prerequisite check.'; exit 1; fi
if command -v cygpath >/dev/null 2>&1; then target="$(cygpath -am "$target")"; fi
exec "$python" -I -B "$kit/../setup_skills.py" --agent claude --home "$target" --workstation-url "$url"
