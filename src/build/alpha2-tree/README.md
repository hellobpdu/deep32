# Alpha.2 transformed-tree builder

`make-alpha2-tree.py` builds from an original alpha.1 tar, its complete external `PIN.sha256`, the reviewed nine-entry deletion table, and one committed, merged source revision descended from the published `5cbec0d` baseline.

Run with Python 3.12 or later and Git, offline:

```sh
python3.14 -I -B make-alpha2-tree.py \
  --alpha1-tar /build/dist/in/tree-ALPHA1_HASH.tar \
  --alpha1-pin /build/dist/in/PIN.sha256 \
  --deletions alpha1-deletions.sha256 \
  --checkout /build/alpha2-source --commit MERGED_COMMIT_HASH \
  --output-dir /build/dist/alpha2-tree-output
```

The output directory must be new, and its parent must exist.
The tool reads committed Git blobs, so local edits cannot enter an image accidentally.
For a build, the source revision must contain the doctor entrypoint, its base package, its output schema, and the model bridge.
Selftest is optional; if any selftest member is present, all four runtime modules and all three schemas are required.

The mapping is `src/workstation/X` to `X`, the shared model bridge to `model-bridge/d32_model_bridge.py`, and the doctor entrypoint, `deep32_diag/**/*.py`, and `schemas/*.json` to the same paths under `diagnostics/`.
Diagnostics launcher templates, documentation, tests, and caches are excluded.
The committed `install/release-key.gpg` and `install/release-key.fingerprint` map to
`distrib/site/usr/local/share/d32/release/`, with mode `0444` in the tree.
The committed `LICENSE`, `NOTICE`, `licenses/AGPL-3.0-or-later.txt`, `notices/THIRD-PARTY-NOTICES.md` and
`notices/disc1-workstation.md` map the same way to `distrib/site/usr/local/share/doc/d32/`, so the site set installs
them under `/usr/local/share/doc/d32`. A build refuses a commit that lacks any of them.

Ruling D1-FC replaces the old baseline-equality refusal with full public correspondence: every mapped path is written from the merged commit, even when its alpha.1 bytes differ from the published baseline or its source has not changed since that baseline.
The only deletions are the nine paths in `alpha1-deletions.sha256`; the tool verifies that the supplied table exactly matches the ruling and refuses if any listed alpha.1 path is absent or its hash differs.
Every other alpha.1-only path, including generated assets, themes, art, and configs, keeps its original bytes,
except the theme output below.

The theme output `theme/out/all` is generated and never committed. When the alpha.1 tree carries it, the build runs
the theme generator again on the final tree's theme sources (`node tools/gen.js --release all --out out/all`, the
command alpha.1's `apply-transforms.py --generate` ran) and takes its output. `--node` names the Node.js to use; the
build refuses without it. The regenerated output must have the same file names as alpha.1's, and only the paths in
`THEME_REGENERATED` may differ: the three manifests and `etc/dt/config/C/d32.copyright`. Anything else refuses the
tree. The PNGs depend on Node's zlib, so use an official Node.js build (its bundled zlib made alpha.1's PNGs);
OpenBSD's node package links the system zlib and re-encodes the GTK theme PNGs. When a regenerated file has a copy in
the asset store (`assets/root`), the copy, its `assets/SHA256SUMS` line and its live `assets/LEDGER-v2.tsv` row are
rewritten to the new bytes, and only if the copy matched alpha.1's theme bytes. Every rewrite is in the change report
(`theme-regenerated`, `theme-asset-copy`). The generator runs without `NODE_*`, `NPM_*` and `D32_*` variables, and the
build refuses if it changes a source or writes outside `theme/out/all`.
The generated `library/src/lib/cisco_types.inc` is not one of the nine deletions and stays when present only in alpha.1.

Quoted includes and Makefile prerequisites are checked against the final tree names and the merged commit; a reference to a path in neither refuses the build.
A surviving quoted include or Makefile prerequisite that names a deleted path also refuses the build.
The `room_cisco` module ID in a Makefile `patsubst` expression is allowed because it is not a file reference.

The full alpha.1 PIN is checked against every regular archive member before changes are applied.
The builder refuses duplicate paths, traversal, nonportable names, case collisions, links, devices, sparse members, special permissions, missing files, extra files, and incorrect hashes.
Mapped Git entries must be regular blobs with mode `100644` or `100755`.
It never extracts archive paths onto the filesystem, except the theme sources, which it writes below a new scratch
folder for the generator.

Outputs are `tree-<hash>.tar`, `PIN.sha256`, `TREE-HASH`, `build.conf.patch`, `build.conf.alpha2`, and `change-report.json`.
The report contains exact `replaced`, `added`, `deleted`, and `kept` counts and, for every replaced or deleted path, `alpha1_sha256`, `published_sha256`, and `merged_sha256`; a source hash is null when that path is absent from that source revision.
The tree hash is SHA256 of the sorted UTF-8 PIN, whose lines are exactly lowercase SHA256, two spaces, a relative path, and LF.
Tar order, ownership, timestamps, and metadata are fixed; kept file permissions come from the alpha.1 archive and mapped file permissions come from committed Git modes, except the two release-key files use `0444`.
No signing, network access, config mutation, or source checkout occurs.

The patch expects the alpha.1 build configuration's release assignment at line 1 and its tree hash at line 3, matching the supplied 910 snapshot.
Apply it to a backed-up configuration and inspect both assignments before building; the separate two-variable fragment offers an explicit alternative.

A hash-only preflight can inspect a complete or partial PIN without the tar:

```sh
python3.14 -I -B make-alpha2-tree.py \
  --alpha1-pin PIN-alpha1-workstation.sha256 \
  --deletions alpha1-deletions.sha256 \
  --checkout /build/alpha2-source --commit MERGED_COMMIT_HASH \
  --audit-pin-only
```

This prints a JSON audit, returns status 2 for a refusal, and never authorizes a build or verifies an archive.
It applies the same correspondence, deletion-hash, and reference rules using supplied PIN names and hashes plus committed source text; it cannot inspect alpha.1-only bytes.
Its `reference_scope` is `merged-only`, and its diagnostics preflight does not prove the full build's required doctor runtime and schemas are complete.
All nine deletion entries must be present with the ruling's hashes even in a partial PIN.
Mapped paths absent from the supplied PIN are counted as additions, so counts and reference resolution describe only the supplied PIN plus the mapped source; use the complete PIN for a complete final-name audit.

Run the synthetic test suite without writing bytecode:

```sh
python3.14 -I -B test_alpha2_tree.py
```

Tests cover full correspondence, exact deletion hashes, dangling quoted includes and Makefile prerequisites, allowed module IDs, preservation of unrelated alpha.1-only bytes, exact counts, all-output byte determinism, archive and path refusals, PIN formatting, source mapping, output preservation, and incomplete merged-source refusal.
