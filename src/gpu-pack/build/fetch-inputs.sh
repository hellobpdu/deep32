#!/usr/bin/env bash
# What: fetch pinned public inputs into a new staging directory, or print the whole plan.
# Usage: fetch-inputs.sh --stage NEW_DIR [--pins FILLED_PINS.json] [--dry-run].
# Exit codes: 0 success/dry run; 2 missing pin, existing target or verification failure.
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1
python3 -B "$(dirname -- "${BASH_SOURCE[0]}")/fetch-inputs.py" "$@"
