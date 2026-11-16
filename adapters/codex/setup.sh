#!/usr/bin/env bash
# What:  Installs the Deep32 research skill and its five lens skills into a Codex home (Linux, macOS, Git Bash):
#        copies skills/*, writes the Codex home's absolute path where the files say ${CODEX_HOME}, removes any
#        earlier Deep32 block from AGENTS.md and appends the current one (the rest of the file is kept), and, with
#        --workstation-url, appends the deep32 MCP entry to config.toml if it has none. No token, key or address is
#        stored: Codex reads the MCP token from DEEP32_MCP_TOKEN, which pairing sets. Every installed skill file is then
#        compared with this kit by SHA-256.
# Usage: ./setup.sh [--codex-home DIR] [--workstation-url https://HOST[:PORT]/mcp]
#        DIR defaults to $CODEX_HOME, else ~/.codex.
# Exit:  0 installed and checked; 1 the post-install check failed; 2 bad arguments;
#        3 refused: the target is this machine's Codex home ($CODEX_HOME or ~/.codex, in any spelling) and the
#          original Deep32 install is present.
set -euo pipefail
kit="$(cd "$(dirname "$0")" && pwd)"
D="${CODEX_HOME:-$HOME/.codex}"
url=""
while [ $# -gt 0 ]; do
  case "$1" in
    --codex-home) [ $# -ge 2 ] || exit 2; D="$2"; shift 2 ;;
    --workstation-url) [ $# -ge 2 ] || exit 2; url="$2"; shift 2 ;;
    *) echo "unknown argument: $1"; exit 2 ;;
  esac
done
case "$D" in /*|[A-Za-z]:/*) ;; *) echo "the Codex home must be an absolute folder"; exit 2 ;; esac
D="${D%/}"
if [ -n "$url" ] && ! printf '%s' "$url" | grep -Eq '^https://[A-Za-z0-9.-]+(:[0-9]{1,5})?/mcp$'; then
  echo "--workstation-url must look like https://<host>[:port]/mcp"; exit 2
fi
canon() {  # one spelling for a folder: the physical path when it exists, else the path without a final slash
  if [ -d "$1" ]; then (cd "$1" 2>/dev/null && pwd -P) || printf '%s' "${1%/}"; else printf '%s' "${1%/}"; fi
}
original=""
if [ -n "${LOCALAPPDATA:-}" ]; then
  if [ -f "$LOCALAPPDATA/Programs/Deep32/Deep32-package.json" ] || [ -d "$LOCALAPPDATA/Programs/Deep32/state/private/relay" ]; then original=1; fi
fi
if [ -n "$original" ]; then
  target="$(canon "$D")"
  for home in "${CODEX_HOME:-}" "$HOME/.codex"; do
    if [ -n "$home" ] && [ "$target" = "$(canon "$home")" ]; then
      echo "refused: this machine holds the original Deep32 install; the distribution never installs beside it."; exit 3
    fi
  done
fi

installed() {  # print a kit file as installed (${CODEX_HOME} replaced), keeping its last newline
  local t
  t="$(cat "$1"; printf x)"; t="${t%x}"
  printf '%s' "${t//'${CODEX_HOME}'/"$D"}"
}

mkdir -p "$D/skills"
names=()
for dir in "$kit"/skills/*/; do
  n="$(basename "$dir")"; names+=("$n")
  rm -rf "${D:?}/skills/$n"
  cp -R "$kit/skills/$n" "$D/skills/"
  while IFS= read -r -d '' f; do
    rel="${f#"$D/skills/"}"
    installed "$kit/skills/$rel" > "$f"
  done < <(find "$D/skills/$n" -type f -name '*.md' -print0)
done

agents="$D/AGENTS.md"
tmp="$agents.deep32-tmp"   # beside the file: nothing is written outside the Codex home
: > "$tmp"
if [ -f "$agents" ]; then
  # drop the old block and any blank lines left at the end, so a re-run adds no blank line
  awk '/<!-- deep32:begin/{skip=1} !skip{ if ($0 ~ /^[ \t\r]*$/) {b++; next} for (i = 0; i < b; i++) print ""; b = 0; print } /<!-- deep32:end -->/{skip=0}' "$agents" > "$tmp"
fi
{ cat "$tmp"; [ -s "$tmp" ] && printf '\n'; installed "$kit/AGENTS.deep32.md"; } > "$agents"
rm -f "$tmp"

note=""
if [ -n "$url" ]; then
  config="$D/config.toml"
  if [ -f "$config" ] && grep -q '^\[mcp_servers\.deep32\]' "$config"; then
    note="config.toml already has a deep32 entry: left as it is."
  else
    entry="$(cat "$kit/config.deep32.toml")"
    { [ -f "$config" ] && cat "$config"; printf '\n%s\n' "${entry//'{{WORKSTATION_MCP_URL}}'/$url}"; } > "$config.new"
    mv "$config.new" "$config"
  fi
else
  note="no --workstation-url: config.toml not changed (pairing adds the deep32 entry)."
fi

bad=0
for n in "${names[@]}"; do
  while IFS= read -r -d '' f; do
    rel="${f#"$kit/skills/"}"
    if [ "${f##*.}" = "md" ]; then want="$(installed "$f" | sha256sum | cut -d' ' -f1)"; else want="$(sha256sum < "$f" | cut -d' ' -f1)"; fi
    have="$( [ -f "$D/skills/$rel" ] && sha256sum < "$D/skills/$rel" | cut -d' ' -f1 || echo missing)"
    if [ "$want" != "$have" ]; then echo "differs: $D/skills/$rel"; bad=1; fi
  done < <(find "$kit/skills/$n" -type f -print0)
done
[ "$(grep -c '<!-- deep32:begin' "$agents")" = "1" ] || { echo "differs: $agents"; bad=1; }
[ "$bad" = "0" ] || { echo "check FAILED"; exit 1; }
[ -n "$note" ] && echo "$note"
echo "done: $D (${#names[@]} skills installed and checked)"
exit 0
