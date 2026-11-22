# Knowledge-base builder

This image runs the exported engine's CPU steps. Existing TEI and reranker
services do the model work, and Qdrant 1.19.1 stores the dense collections.
The builder publishes no ports and requests no GPU. The internal client URLs
are embeddings:80, reranker:8082 and qdrant:6333. The door retains host ports
8081 and 8082. A bounded readiness barrier runs before a cold build.

Run containers/builder-job.sh as the Forge administrator. It prepares only
corpus, rag-data, kb-work, exports, bundles and qdrant beneath the Forge state
directory for UID/GID 10032. It adds traverse permission to the state parent;
it never changes ownership or permissions beneath private. Supply runtime
files with the matching owner. Signing hooks must have explicit access to
their designated operator-managed key; the image supplies no key or default.

## Offline assembly

The resolver copies kit/builder-pins.json to STAGE/builder/pins.json and fills
every TO-PIN from authenticated evidence. It stages the base image archive
as builder/base.tar, Qdrant as builder/qdrant.tar, and the full git/age/GnuPG Debian
dependency closure as builder/debs/*.deb. builder/debs.json is an array of
{package, version, path, sha256}, with paths relative to builder/debs.

Put exactly one Linux CPython 3.12 wheel for each requirements-engine.in pin
in payload/wheelhouse/builder. Generate the lock with the unchanged S2 tool:

    python3 -I -B make_lock.py --pins requirements-engine.in --wheels STAGE/payload/wheelhouse/builder --out STAGE/payload/locks/builder.lock

Record its SHA256 and that of debs.json in builder/pins.json. No wheel hash is
provided or guessed by the source kit. The existing fetch-inputs stage remains
responsible for the original model/application inputs; this builder lane uses
only pre-staged inputs and performs no downloads.

Write STAGE/builder/INPUTS.json as a sorted array of {path, size, sha256} for
exactly builder/pins.json, builder/base.tar, builder/qdrant.tar,
builder/debs.json, all builder/debs members, payload/locks/builder.lock and all
payload/wheelhouse/builder members. Paths are stage-relative POSIX paths,
sorted as strings. The inventory excludes itself. No other staged file is
read by builder_inputs.check.

No eval data or evaluator is included in the public builder image. Image
assembly refuses an embedded eval tree. Private eval data remains operator input
until the separate eval dataset release.

Quality validation is optional at runtime. Omit --eval-root to run the structural
validator (build completion, SQLite/FTS, server health, identity, counts and
backend status) and continue without quality queries. The served BUILD_REPORT.json
and structural validation report record exactly:

    quality validation skipped: no eval sets supplied

To run quality gates, provide --eval-root DIR for a read-only runtime directory
containing exactly eval_v2.py and golden/. golden/ must hold at least one allowed
family: golden_sc_v1, golden_ise_v1, golden_iseapi_v1 or golden_pwsh_v1*. Each set
requires MANIFEST.json, qrels.jsonl and queries.jsonl. Excluded, malformed or empty
supplied data is an error; it never silently selects the skip path. All referenced
doc_key fields must belong to the kept manifest. No supplied code or data is
copied into the image or generation export. Place the mount beneath the existing
kb-work bind mount, for example /var/lib/deep32-forge/kb-work/operator-eval, and
add --eval-root with that container path to builder-job.sh build.

To compare a build with an earlier one, also add --baseline-root with the
earlier served generation's root, for example
/var/lib/deep32-forge/rag-data/old-served. For each supplied set, validate_root
then compares the new generation with that root's VALIDATION-<set>.json query
by query, and the build stops when hit@1 or hit@8 has a net loss or delivery@8
falls by more than 0.005. The comparison and its gates are in the new root's
VALIDATION-<set>.json; BUILD_REPORT.json's quality_validation names the sets
compared and those not compared (a probe set, or a set the earlier generation
did not run). A baseline made with another version of a set stops the build.
--baseline-root needs --eval-root.

build/build-images.sh now also calls build/build-builder.sh. The latter can
run separately with --stage STAGE --out NEW_OUTPUT. It verifies inputs, builds
with --network none --pull=false, and records immutable ids and archive hashes.
After merging the original application-image index, run:

    python3 -I -B build/builder_inputs.py merge --stage STAGE --out NEW_OUTPUT

For the combined build, NEW_OUTPUT is OUT/builder-build. Reseal PAYLOAD.json,
then run the full build/validate.py. It requires both added image roles, the
Qdrant source digest/version, and the exact engine lock and wheel hashes.
First boot loads both added images when the merged pins contain builder and
writes BUILDER_IMAGE/QDRANT_IMAGE into installed images.conf.

## Build and export

First use the document converter's add-document command to create and validate a new Cisco block
version. It calls the existing converter chain. It has no implicit KB hook.
Then use corpus_from_blocks.py with the updated kept blocks to assemble a
temporary corpus tree. Rebuild its derived layers with the provided raw cache,
frozen CLI base and style cache. Finally assemble a NEW corpus repository with
--kb pointing to that run's pruned layers, --repo and --created. build_root
archives the committed ref: writing layers beside an older repository does not
put them into its commit. The temporary tree and final repository must use the
same updated block inputs. See --help for the unchanged tool arguments.

The existing tools run inside the builder image. For example, from its shell
(--entrypoint /bin/bash with the same mounts and model network):

```sh
python3 -I -B /opt/deep32/builder/corpus_from_blocks.py --out /var/lib/deep32-forge/corpus/layer-source RFC_BLOCK CISCO_BLOCK POWERSHELL_BLOCK SYSTEM_BLOCK
python3 -I -B /opt/deep32/builder/derived_layers.py terms --manifest /var/lib/deep32-forge/corpus/layer-source/manifest.json --terms RUNTIME_SOURCE --out /var/lib/deep32-forge/kb-work/terms.kept.txt
python3 -I -B /opt/deep32/builder/derived_layers.py script --kit /opt/deep32/builder/kb-tools --corpus /var/lib/deep32-forge/corpus/layer-source --raw /var/lib/deep32-forge/kb-work/raw --base FROZEN_CLI_BASE --base-sha256 BASE_HASH --styles STYLE_CACHE --work /var/lib/deep32-forge/kb-work/run --tools /opt/deep32/builder --terms /var/lib/deep32-forge/kb-work/terms.kept.txt --out /var/lib/deep32-forge/kb-work/run/derived.sh
bash /var/lib/deep32-forge/kb-work/run/derived.sh
python3 -I -B /opt/deep32/builder/corpus_from_blocks.py --out /var/lib/deep32-forge/corpus/repository --kb /var/lib/deep32-forge/kb-work/run/pruned --repo --created UTC_TIMESTAMP RFC_BLOCK CISCO_BLOCK POWERSHELL_BLOCK SYSTEM_BLOCK
```

Use derived_layers.py terms --manifest MANIFEST --terms RUNTIME_SOURCE --out KEPT
to retain only references to kept documents. Neither private source glossary
list is included in this image. derived_layers.py script requires --terms KEPT
and installs that runtime list into a per-run kb-tools copy. The script produces
the same features, CLI, RFC, ID terms, glossary and IANA layers, then prunes and
checks them. document_styles.py needs PyMuPDF if style-cache regeneration is wanted;
the engine lock has no PyMuPDF pin, so provide the existing style cache from the
converter lane rather than changing the tested engine lock.

The build command is:

    builder-job.sh build --corpus-repo /var/lib/deep32-forge/corpus/repository --corpus-ref refs/heads/corpus --build-root /var/lib/deep32-forge/rag-data/new-build --generation new-build --served-root /var/lib/deep32-forge/rag-data/new-served --served-generation new-served --kept-manifest /var/lib/deep32-forge/corpus/manifest.json --expect-fingerprint FINGERPRINT --export-dir /var/lib/deep32-forge/exports --recipient AGE_PUBLIC_RECIPIENT

Choose new generation names and an expected corpus fingerprint. The command runs
build_root with dense indexing and powershell,system opt-in; derive_root with
the production reranker/fold/regex settings; validate_root with structural gates and, when supplied, every approved neutral
set, compared with --baseline-root when one is given; and export_generation only
after successful validation. The projected
doc_key fields of all supplied sets must be kept. An approved _probe with no
qrels doc_key fields uses --min hit@1=off --min hit@8=off; all other gates remain,
and judged sets retain both hit-rate floors. Without supplied eval data only the
quality query step is skipped; every non-quality validation gate remains. Child output
is captured and withheld so failures cannot print query or expected-answer text.

## C3 bundle and signing

export_generation produces an age-encrypted .tar.age file. The bundle command
requires a DECRYPTED tar file. The operator must decrypt the selected export
using their existing age identity into an explicitly chosen runtime file before
calling bundle. No identity path or decryption key is baked into this wrapper.

    builder-job.sh bundle --export-input DECRYPTED_TAR --unpack-dir /var/lib/deep32-forge/exports/unpacked --served-generation new-served --expect-fingerprint FINGERPRINT --bundle-dir /var/lib/deep32-forge/bundles --compat COMPAT_JSON --created UTC_TIMESTAMP --block RFC_BLOCK_JSON --block CISCO_BLOCK_JSON --block POWERSHELL_BLOCK_JSON --block SYSTEM_BLOCK_JSON --signer-type openpgp --signer-id PRIMARY_FINGERPRINT --sign-cmd '["gpg","--homedir","OPERATOR_KEY_DIRECTORY","--local-user","{signer_id}","--armor","--detach-sign","--output","{sig}","{bundle_json}"]' --verify-cmd '["python3","-I","-B","/opt/deep32/builder/kb-bundle/hooks/openpgp_verify.py","--keyring","PUBLIC_KEYRING","--fingerprint","{signer_id}","{bundle_json}","{sig}"]'

The placeholders are the existing kb_bundle.py contract. GnuPG and its verifier
are installed from the sealed Debian closure, and the existing fingerprint-
checking OpenPGP hook is copied from the bundle tool. Supply the operator's designated
key directory and public keyring explicitly. bundle runs unpack, verifies
the export fingerprint, then invokes the existing C3 write/check and signer.
An unsigned fallback is not selected. Raw plaintext and operator signing keys
remain runtime material, outside the image and release source.
Both C3 commands use the existing --allow-path-prefix option to permit the
neutral /var/lib/deep32-forge/ state prefix; the upstream defaults cover the
older /var/lib/deep32/ layout instead.
