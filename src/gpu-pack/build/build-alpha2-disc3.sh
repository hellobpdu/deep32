#!/usr/bin/env bash
# Build the offline alpha.2 disc 3 images or print their complete plan.
set -euo pipefail
export LC_ALL=C TZ=UTC PYTHONDONTWRITEBYTECODE=1
exec "${D32_PYTHON:-python3}" -I -B "$(dirname -- "${BASH_SOURCE[0]}")/alpha2_disc3.py" "$@"
