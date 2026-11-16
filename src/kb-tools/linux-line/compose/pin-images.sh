#!/bin/bash
# pin-images.sh: ON 901 as d32. Pulls the two base images by tag ONCE, reads their index digests and writes
# compose/pins.env; the lead then compares both digests with the registry's own listing (Docker Hub pages for
# qdrant/qdrant:v1.19.1 and python:3.12-slim-trixie) before "render". After that every pull and build uses the
# digest, never the tag.
# usage: pin-images.sh CTX
set -euo pipefail
CTX=${1:?CTX}
pin() { docker pull -q "$1" >/dev/null; docker image inspect --format '{{index .RepoDigests 0}}' "$1" | sed 's/.*@//'; }
Q=$(pin qdrant/qdrant:v1.19.1)
P=$(pin python:3.12-slim-trixie)
printf 'QDRANT=%s\nPYTHON=%s\n' "$Q" "$P" > "$CTX/compose/pins.env"
cat "$CTX/compose/pins.env"
echo "COMPARE both with the registry listing, then: python3 -I -B $CTX/lane/compose/check_compose.py render --compose $CTX/lane/compose/compose.json --pins $CTX/compose/pins.env --out $CTX   (writes $CTX/compose/compose.json and the Dockerfiles with digests)"
