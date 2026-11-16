#!/usr/bin/env bash
# What: serialise one converter job, pause both models, restore only previously running services.
# Usage: convert-job.sh JOB_ID pymupdf|production|mineru [--tier standard|advanced] [--page-range N[-M]] [--dry-run].
# Input: pymupdf and mineru take jobs/JOB_ID/input.pdf; production takes exactly one of input.pdf, input.html, input.epub, input.mib.json plus job.json and an optional lexicon/ folder. Tier and page range apply only to MinerU.
# Exit codes: 0 success/dry run; 2 invalid input; 3 recovery needed; 4 GPU held; worker code on conversion failure.
set -euo pipefail
print_dry_run() {
  echo 'Would lock the job queue; refuse an operator GPU hold; record which models are running; stop both; run the offline converter with no network; restore that exact service set on success, failure or signal. Interrupted jobs leave a recovery record. Downloads: none.'
}
if [[ $# == 1 && "${1:-}" == --dry-run ]]; then
  print_dry_run
  exit 0
fi
[[ $# -ge 2 && "$1" =~ ^[a-z0-9][a-z0-9_-]{0,39}$ ]] || exit 2
job_id=$1
backend=$2
shift 2
[[ "$backend" == pymupdf || "$backend" == production || "$backend" == mineru ]] || exit 2
tier=advanced
page_range=
tier_set=false
page_range_set=false
dry_run=false
# Match the production arbiter's Python fullmatch, including Unicode Nd digits.
# A shell numeric class would change that contract and mishandle final newlines.
valid_page_range() {
  printf '%s' "$1" | python3 -I -B -c 'import re, sys; PAGE_RANGE_RE = re.compile(r"^\d+(?:-\d+)?$"); sys.exit(0 if PAGE_RANGE_RE.fullmatch(sys.stdin.buffer.read().decode("utf-8")) else 2)'
}
while (($#)); do
  case "$1" in
    --tier)
      [[ $# -ge 2 ]] || exit 2
      tier=$2
      [[ "$tier" == standard || "$tier" == advanced ]] || exit 2
      tier_set=true
      shift 2
      ;;
    --page-range)
      [[ $# -ge 2 ]] || exit 2
      page_range=$2
      valid_page_range "$page_range" || exit 2
      page_range_set=true
      shift 2
      ;;
    --dry-run)
      dry_run=true
      shift
      ;;
    *) exit 2 ;;
  esac
done
converter_options=()
if [[ "$backend" == mineru ]]; then
  converter_options+=(--tier "$tier")
  if "$page_range_set"; then
    converter_options+=(--page-range "$page_range")
  fi
elif "$tier_set" || "$page_range_set"; then
  exit 2
fi
if "$dry_run"; then
  print_dry_run
  exit 0
fi
# Converter option validation ends here; no state has changed.
if [[ "$backend" == mineru ]] && grep -q '^RERANKER_DEVICE=cpu$' /etc/deep32-forge/images.conf; then
  echo 'MinerU GPU backend is unavailable in processor mode' >&2
  exit 2
fi
mkdir -p /run/deep32-forge
chmod 0700 /run/deep32-forge
exec 9>/run/deep32-forge/converter.lock
flock 9
[[ ! -e /run/deep32-forge/gpu-hold ]] || exit 4
state_dir=/var/lib/deep32-forge
[[ ! -e "$state_dir/conversion-recovery" ]] || { echo 'Recover interrupted model service state first' >&2; exit 3; }
job_dir="$state_dir/jobs/$job_id"
[[ ! -L "$job_dir" && ! -e "$job_dir/output" ]] || exit 2
inputs=()
for name in input.pdf input.html input.epub input.mib.json; do
  [[ ! -L "$job_dir/$name" ]] || exit 2
  [[ ! -f "$job_dir/$name" ]] || inputs+=("$name")
done
[[ ${#inputs[@]} == 1 ]] || exit 2
input=${inputs[0]}
[[ "$backend" == production || "$input" == input.pdf ]] || exit 2
extra=()
if [[ "$backend" == production ]]; then
  [[ -f "$job_dir/job.json" && ! -L "$job_dir/job.json" ]] || exit 2
  extra+=(--job "/jobs/$job_id/job.json")
  if [[ -e "$job_dir/lexicon" || -L "$job_dir/lexicon" ]]; then
    [[ -d "$job_dir/lexicon" && ! -L "$job_dir/lexicon" ]] || exit 2
    extra+=(--lexicon "/jobs/$job_id/lexicon")
  fi
fi
compose=(docker compose --project-name deep32-forge --env-file /etc/deep32-forge/images.conf -f /etc/deep32-forge/compose.yaml)
running_output=$("${compose[@]}" ps --status running --services)
mapfile -t running < <(printf '%s\n' "$running_output" | grep -E '^(embeddings|reranker)$' || true)
printf '%s\n' "job=$job_id" "${running[@]}" > "$state_dir/conversion-recovery"
chmod 0600 "$state_dir/conversion-recovery"
restore() {
  local code=$?
  trap - EXIT INT TERM
  docker info >/dev/null 2>&1 || { echo 'Container engine unavailable; recovery record retained' >&2; exit 3; }
  # Stop the job container before returning the card to either model, including after a signal.
  if docker container inspect "deep32-convert-$job_id" >/dev/null 2>&1; then
    if [[ $(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' "deep32-convert-$job_id") != deep32-forge ]]; then
      echo 'Unexpected container owner; recovery record retained' >&2
      exit 3
    fi
    docker rm -f "deep32-convert-$job_id" >/dev/null || exit 3
  fi
  if ((${#running[@]})); then
    if ! "${compose[@]}" up -d --pull never --no-build "${running[@]}"; then
      echo 'Model restoration failed; recovery record retained' >&2
      exit 3
    fi
  fi
  rm -- "$state_dir/conversion-recovery"
  exit "$code"
}
trap restore EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
"${compose[@]}" stop embeddings reranker
"${compose[@]}" --profile conversion run --rm --no-deps --pull never --name "deep32-convert-$job_id" converter \
  --backend "$backend" --input "/jobs/$job_id/$input" --output "/jobs/$job_id/output" "${extra[@]}" "${converter_options[@]}"
