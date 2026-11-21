# Disc 1 alpha.2 on VM 910

<!-- operator runbook; commands below are for the authorized build owner. -->
Run on the existing OpenBSD 8.0 amd64 build VM, in `/build/dist`, using the
existing build account and key handling. These commands were not executed by the
offline worker. No package download, source fetch, new key or installer is needed.
Disc 2 retains its alpha.1 bytes; this run rebuilds disc 1 only.

Ruling D1-FC requires full public correspondence: every mapped Workstation,
model-bridge, and diagnostics path comes from the reviewed merged commit, even
when alpha.1 differs from the published `5cbec0d` baseline. The only deletions
are the nine hash-checked paths in `alpha1-deletions.sha256`; every other
alpha.1-only path keeps its bytes. A missing deletion path, a different deletion
hash, an altered deletion table, or a dangling quoted include or Makefile
prerequisite refuses the build. The `room_cisco` module ID in `patsubst` is
allowed. A refusal is not permission to edit the alpha.1 PIN or deletion table.

Place the reviewed merged source checkout at `/build/dist/in/alpha2-source`, and
the delivered stage-patches folder at `/build/dist/in/alpha2-stage-patches`.
The checkout must include this lane and lane DG's `src/diagnostics` runtime.
Keep the original complete alpha.1 PIN beside the original tree tar; the small
workstation PIN excerpt used for the offline audit is insufficient for a build.

```sh
cd /build/dist
set -eu
umask 077
SOURCE=/build/dist/in/alpha2-source
BUILDER=$SOURCE/src/build/alpha2-tree
MERGED_COMMIT=$(git -C "$SOURCE" rev-parse --verify 'HEAD^{commit}')
test -z "$(git -C "$SOURCE" status --porcelain)"
git -C "$SOURCE" show --no-patch --format=fuller "$MERGED_COMMIT"
BACKUP=/build/dist/work/alpha2-backup-$(date -u +%Y%m%dT%H%M%SZ)
mkdir "$BACKUP"
cp -p build.conf "$BACKUP/build.conf"
cp -p in/PIN.sha256 "$BACKUP/PIN.sha256"
cp -Rp stages "$BACKUP/stages"
cp -Rp state "$BACKUP/state"
printf '%s\n' "$MERGED_COMMIT" > "$BACKUP/source-commit.txt"
ALPHA1=743d25988ef7cb09c6d903e28eb1f84b8853a6a38d79ef904eb0bff368bf8e71
test "$(sha256 -q "$BACKUP/PIN.sha256")" = "$ALPHA1"
test -f "in/tree-$ALPHA1.tar"
python3.14 -I -B "$BUILDER/make-alpha2-tree.py" \
    --alpha1-tar "/build/dist/in/tree-$ALPHA1.tar" \
    --alpha1-pin "$BACKUP/PIN.sha256" \
    --deletions "$BUILDER/alpha1-deletions.sha256" \
    --checkout "$SOURCE" --commit "$MERGED_COMMIT" \
    --output-dir "$BACKUP/new-tree"
NEW=$BACKUP/new-tree
TREE=$(cat "$NEW/TREE-HASH")
test "$(sha256 -q "$NEW/PIN.sha256")" = "$TREE"
cat "$NEW/change-report.json"
```

Success produces a new directory containing `tree-<hash>.tar`, `PIN.sha256`,
`TREE-HASH`, `change-report.json`, `build.conf.patch`, and `build.conf.alpha2`.
Every tar regular file is verified against the full input PIN before the overlay.
All mapped source paths receive the merged commit's bytes; every other
alpha.1-only path except the nine reviewed deletions keeps its bytes. The report
records exact `replaced`, `added`, `deleted`, and `kept` counts, plus
`alpha1_sha256`, `published_sha256`, and `merged_sha256` for each replaced or
deleted path; an absent source path has a null hash. Refusals return status 2
without publishing partial output. Read the report and selected source commit
before continuing, and verify the count and hash records against the ruling.

Since 2026-11-19 the builder also regenerates the theme output (`theme/out/all`) from the merged theme sources and
needs `--node`. The GTK theme PNGs only come out as alpha.1 made them with an official Node.js build (bundled
zlib); OpenBSD's node package re-encodes them and the builder refuses. So run the command above with
`--node <official node>` on the Windows build controller, using the same alpha.1 tar, PIN and commit, copy the output
directory here as `$BACKUP/new-tree`, and continue from `NEW=$BACKUP/new-tree`. Its `change-report.json` names the
regenerated files (`theme-regenerated`, `theme-asset-copy`) and its `source_commit` must equal `$MERGED_COMMIT`.
For a PIN-only preflight, use the same reviewed source and deletion table with
`--audit-pin-only`; this checks supplied hashes and final names against committed
source text without reading alpha.1-only bytes. All nine deletion paths and
hashes are required. A partial PIN describes only its supplied names plus the
mapped source, so its counts and reference checks do not prove the full archive.
The audit reports `reference_scope` as `merged-only`: it cannot check references
inside alpha.1-only files or prove the required diagnostics are complete.
The complete tar/PIN and diagnostics verification above remains required for a build.

```sh
python3.14 -I -B "$BUILDER/make-alpha2-tree.py" \
    --alpha1-pin "$BACKUP/PIN.sha256" \
    --deletions "$BUILDER/alpha1-deletions.sha256" \
    --checkout "$SOURCE" --commit "$MERGED_COMMIT" \
    --audit-pin-only
```

Both modes check committed quoted includes and Makefile prerequisites against
final names and the merged commit, refusing a missing or deleted referenced path.
The full build also checks references in surviving alpha.1-only files.
The generated `library/src/lib/cisco_types.inc` is not a deletion and remains
available to its Makefile and source references when kept from alpha.1.
A build still requires the complete doctor runtime and schemas, model bridge,
source ancestry, mapped regular-file modes, portable paths, and archive safety
checks. The PIN-only audit keeps its narrower diagnostics preflight boundary.

Check source equality for the expensive outputs proposed for reuse, then preserve
the wheels before stage 10 removes their old lane. This check does not authenticate
the VM's installed outputs or external inputs: confirm they are the completed
alpha.1 outputs on this same VM, with the same toolchain, source archives, ports,
identity sources, taxonomy, external registry data and public key set. Check the
existing stage logs and hashes using normal release-owner controls. If any
required evidence is absent or differs, use the full rebuild route below.

```sh
python3.14 -I -B "$BUILDER/check-reuse.py" \
    --old-pin "$BACKUP/PIN.sha256" --new-pin "$NEW/PIN.sha256" \
    --require 20,30,40,55 > "$BACKUP/reuse.json"
cat "$BACKUP/reuse.json"
test -x /usr/local/d32/py312/bin/python3.12
test -x /usr/local/d32/venv/deep32-engine/bin/python3.12
test -d /build/lanes/pyrag/wheelhouse/cp312
test -d /build/lanes/pyrag/wheelhouse/cp314
test -f /build/lanes/pyrag/wheelhouse/SHA256SUMS
(cd /build/lanes/pyrag/wheelhouse && \
    sed 's/^\([0-9a-f]*\)  \(.*\)$/SHA256 (\2) = \1/' SHA256SUMS | sha256 -c -q)
cp -Rp /build/lanes/pyrag/wheelhouse "$BACKUP/wheelhouse"
cp -Rp /build/lanes/pyrag/locks "$BACKUP/locks"
test -d work/55-update-builds
```

Install the minimal VM stage patches as described in their README, then install
the new tar/PIN and apply only the two-assignment build configuration patch.
Keep the existing signing paths, keyset, engine/gateway source archive hashes,
job count and test disk selection. Select the test root disk for the intended
adapter before building: `sd1` for the existing KVM fixture or `wd1` for the
existing Hyper-V Generation 1 fixture; never select the medium disk.

```sh
install -m 0444 in/alpha2-stage-patches/files/stages/alpha2_stage.py stages/alpha2_stage.py
for f in 45-node.sh 60-stage.sh 65-map.sh 68-assets.sh 80-image.sh stage_map.py; do
    patch -C -p1 < "in/alpha2-stage-patches/$f.patch"
done
for f in 45-node.sh 60-stage.sh 65-map.sh 68-assets.sh 80-image.sh stage_map.py; do
    patch -p1 < "in/alpha2-stage-patches/$f.patch"
done
for f in stages/*.sh; do sh -n "$f"; done
python3.14 -I -B -c 'from pathlib import Path; [compile(p.read_bytes(), str(p), "exec") for p in Path("stages").glob("*.py")]'
install -m 0444 "$NEW/tree-$TREE.tar" "in/tree-$TREE.tar"
install -m 0444 "$NEW/PIN.sha256" in/PIN.sha256
patch -C build.conf < "$NEW/build.conf.patch"
patch build.conf < "$NEW/build.conf.patch"
grep -E '^D32_(RELEASE_ID|TREE_HASH|KEYSET|TEST_ROOTDISK)=' build.conf
sh s5-build.sh --dry-run
```

| Stage | Decision and reason |
| --- | --- |
| 00, 10 | Rerun: validate the host and unpack/verify the new PIN/tree; repopulate source lanes. Stage 10 deletes the Python build lane and its wheels. |
| 20 | Reuse only after the checker and external-input/output review pass: all pinned `pyrag` sources are identical; installed CPython/SQLite survive stage 10. |
| 30 | Reuse only with verified identical `pyrag` inputs, unchanged external toolchain/ports and the saved wheelhouse restored after stage 10. |
| 35 | Rerun: regenerate/check install locks from restored wheels and copy locks into the new release output directory. |
| 40 | Reuse only with identical `pyrag` inputs and engine/gateway archives plus intact installed venv/code trees; stage 10 does not remove `/usr/local/d32`. |
| 45 | Rerun: changed shims and shared bridge must enter `/usr/local/d32`. The native node build and FMA/AVX check run again. |
| 50 | Rerun conservatively: `ship/tools/stage.sh` changed; rebuild/test Library and Ask broker with the original required-library checks. |
| 55 | Reuse only with identical `ship/u10`, `ship/u14/apps`, all `ship/u2`, and `ask-client` sources, unchanged external registries/toolchain and intact `work/55-update-builds`. |
| 60, 65, 68 | Rerun: new runtime staging, baseline mapping, additive runtime audit and unchanged asset gates. |
| 70, 80 | Rerun: repack/sign system packs and build/sign both normal and TEST media with the new package roots and installer. |
| 85, 88, 90 | Rerun: raw conversion, read-only scan extraction and signed provenance for these new outputs. |

For the verified reuse route, run stages 00/10 separately, restore the wheelhouse,
then run the explicit remaining set. `--only` is necessary: stage 10 invalidates
later markers, and `--from` would require markers for skipped earlier stages.
Do not fabricate or restore stale `.done` markers to force a skip.

```sh
sh s5-build.sh --only 00,10
test ! -e /build/lanes/pyrag/wheelhouse
cp -Rp "$BACKUP/wheelhouse" /build/lanes/pyrag/wheelhouse
cp -p "$BACKUP/locks/requirements-core.lock" /build/lanes/pyrag/locks/requirements-core.lock
cp -p "$BACKUP/locks/requirements-edge.lock" /build/lanes/pyrag/locks/requirements-edge.lock
chown -R d32build:d32build /build/lanes/pyrag/wheelhouse /build/lanes/pyrag/locks
(cd /build/lanes/pyrag/wheelhouse && \
    sed 's/^\([0-9a-f]*\)  \(.*\)$/SHA256 (\2) = \1/' SHA256SUMS | sha256 -c -q)
sh s5-build.sh --only 35,45,50,60,65,68,70,80,85,88,90
```

If source equality, external inputs or outputs cannot be established, omit the
reuse route and rebuild all stages instead:

```sh
sh s5-build.sh --from 00
```

The release owner types the existing train-key passphrase directly at the build
console when `signify` prompts. The task specifies three prompts during stage 80;
answer each in that console. Stage 70 also signs the
system-pack checksum list, and stage 90 signs provenance. Never redirect, capture,
store, paste into a script, or pass the passphrase through an environment variable.
No real signing was performed in this offline task.

Inspect `/build/dist/out/0.1.0-alpha.2` after success. Expected outputs include the
normal `.img`, TEST `.vhd`, `SHA256`/`SHA256.sig`, public key copies, `SIZES.txt`,
`STAGE-MAP.tsv`, `MEDIUM-PACKAGES.sha256`, `MEDIUM-PACKAGE-ROOTS.txt`, TEST raw
image and `test/SHA256.raw`, `scan/` payload and hashes, and signed provenance.
The package guard must show one Tailscale package matching the signed local
repository, with Tailscale named in the medium's roots. A successful stage 85 is
raw conversion only; it is not an installation test.

```sh
OUT=/build/dist/out/0.1.0-alpha.2
grep -E '^tailscale(-|$)' "$OUT/MEDIUM-PACKAGE-ROOTS.txt"
grep 'tailscale-' "$OUT/MEDIUM-PACKAGES.sha256"
grep 'alpha2-verified' "$OUT/STAGE-MAP.tsv"
test -z "$(awk -F '\t' '$4 == "MISSING" {print $1}' "$OUT/STAGE-MAP.tsv")"
(cd "$OUT/scan" && sha256 -c SHA256)
(cd "$OUT/test" && sha256 -c SHA256.raw)
grep '^tree_hash ' "$OUT/PROVENANCE.txt"
grep '^release 0.1.0-alpha.2$' "$OUT/PROVENANCE.txt"
signify -V -p "$OUT/d32-release.pub" -m "$OUT/PROVENANCE.txt" -x "$OUT/PROVENANCE.txt.sig"
```

Use the existing authorized KVM and Hyper-V lab adapters with a new empty target
disk and the TEST medium. Their operator-controlled VM import/boot commands are
outside this offline code lane. Also test the guided medium without a settings
disc: Enter must accept the keyboard default; a sole eligible empty target must
be offered rather than the medium; no or multiple eligible disks must have no
default; the root maintenance step must show no `printf` error; installation
must power off with the marked medium-removal message. Detach the medium before
the first start. Verify `pkg_info tailscale`, `rcctl check tailscaled`, the existing
sign-in flow, and `deep32 doctor --format json` without using or publishing any
private test credential. The diagnostic dispatcher must preserve its current
fail-closed installation boundary until lane DG supplies qualified evidence.

After installation, run the following release-key checks at the installed
Workstation's console. Both files must exist, be owned by root:wheel with mode
0444, and the fingerprint's first line must match the public README fingerprint.
The compatibility list must also be root:wheel, mode 0444, and list alpha.1.

```sh
set -eu
KEYDIR=/usr/local/share/d32/release
for file in "$KEYDIR/release-key.gpg" "$KEYDIR/release-key.fingerprint" /etc/d32/content-compatible-versions; do
    test -f "$file"
    test "$(stat -f '%Su:%Sg:%Lp' "$file")" = root:wheel:444
done
test -s "$KEYDIR/release-key.gpg"
test "$(sed -n '1p' "$KEYDIR/release-key.fingerprint")" = B205B406548627AABF5250D8A078C5F25D025DD6
grep -Fx '0.1.0-alpha.1' /etc/d32/content-compatible-versions
```

Run the following checks at the installed Workstation's
console against its actual `/etc/pf.conf`. Confirm that the hub, work, and LAN
relay rules allow outbound TCP port 7452; source checkout and `scan/` checks
alone are insufficient. The parse check must pass and each loop iteration must
print its installed rule before the installation test passes.

```sh
set -eu
pfctl -nf /etc/pf.conf
for peer in relay_hub relay_work relay_work_lan; do
    grep -E "^pass[[:space:]]+out[[:space:]]+quick[[:space:]]+on[[:space:]]+(tailnet|egress)[[:space:]]+proto[[:space:]]+tcp[[:space:]]+to[[:space:]]+<$peer>[[:space:]]+port[[:space:]]+7452([[:space:]]|$)" /etc/pf.conf
done
```

Stage 88 copies site/packs/boot payload into `scan/` without altering the image;
it does not run release gates. Hand those outputs to the release owner for the
existing external scans, SPDX metadata, signed image checksum coverage and
release verification. This task does not access private gates, publish media,
alter disc 2 or authorize delivery.
