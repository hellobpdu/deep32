# Alpha.2 shared contracts

These versioned JSON contracts freeze the interfaces in T-0201 PLAN J01 and
designs D1-D4. They describe metadata; they do not assemble media, choose a GPU
class, run diagnostics, execute searches, generate SPDX, compare real builds,
authenticate a signature or authorise installation. The adjacent capability
manifest supplies T-0200's packaging interface separately.

Every file under `fixtures/` is synthetic test material. All descriptor hashes,
image lengths, source commits, generation identities, run identifiers, component
inventories and measurements are invented. No signature, real media or measured
release claim is supplied. The concept DOI, licences, version and pinned release
fingerprint are the lead's fixed public metadata. The fixture's version DOI
`https://doi.org/10.0000/invented-release` is deliberately invented; the actual
alpha.2 version DOI remains a required release-owner input before signing.

## Files and consumers

| Contract kind | Consumer and boundary |
|---|---|
| `release-metadata` | Shared public version `0.1.0-alpha.2`, release tag `v0.1.0-alpha.2`, concept DOI `10.5281/zenodo.24272647`, code `Apache-2.0` (the PDF converter and the Library renderer `e26-render` use MuPDF and stay `AGPL-3.0-or-later`), documentation `CC-BY-4.0`, both Jina models `CC-BY-NC-4.0`. |
| `mirrors` | J02-J04: version 2 has disc 1 and disc 2 plus explicitly bound GPU capability bundle transports, ordered mirrors, 64 MiB chunk hashes and final whole hashes. Version 1 remains readable for signed legacy media. No SBOM fields or signing-key override. |
| `asset-map` | J02/J13-J15: unique flat public basenames mapped explicitly to relative metadata destinations. Empty arrays mean external-only metadata. |
| `doctor-install` | J05-J09: non-secret installer/importer receipt. A reader cannot infer trust, ownership or installed health from receipt claims alone. |
| `doctor-output` | J09: typed ordered rows, fixed states, summary and exit. L3 adds `observed.path_kind` for each enrolled peer's direct or relayed path. |
| `selftest-questions` | J10: public ordered questions and literal document/span labels; fixtures use only the upstream invented beacons and kites. |
| `selftest-reference` | J10/L02: exact question-byte hash, release, model identities, effective settings, three-run reference and metric policy. |
| `selftest-result` | J10: PASS/FAIL complete metrics; INCOMPLETE has null metrics, no details and exit 2. |
| `selftest-calibration` | L02: separate owner-signed pre-query candidate binding and three complete rank receipts. No shipped reference-writing mode. |
| `sbom-input` | J11: declared input roots/manifests, canonical subjects, explicit aliases, components, evidence, dependencies and approved exceptions. |
| `sbom-index` | J12-J15: one exact measured subject per shipped path, one public SBOM binding, separate on-media paths, component/licence coverage and gaps. |
| `sbom-gap` | J11-J12: precise missing inputs and scope; approval is a checksum-bound external record, never implicit permission. |
| `reproducibility` | J16: one row per SBOM subject, sealed inputs/recipe/toolchain identities and honest per-subject status. |
| `hash-graph` | J01/J02/J12-J16: explicit hash-binding DAG for producer/index/checksum composition. |

Schemas use JSON Schema draft 2020-12. They are project contracts, not an official
SPDX schema. Every object vocabulary is closed. `selftest-reference.settings`
is closed to the fields exposed by the inspected engine `StatusService._settings`
and `DenseSearchSettings.shown`: reranker, search_many, grep, serving and the
optional dense/fold_identical fields. A future status field requires a reviewed
contract revision, not silent acceptance. The synthetic reference settings were
updated to that vocabulary and its settings/question-byte hashes recomputed.

`fixtures/contracts.json` enumerates `{kind,path,valid}` cases, with fixture paths
relative to that directory. Several fixture basenames exercise the same schema.
Valid fixtures test shape and semantics only; they are never publishing inputs.

## Strict loading and semantic validation

## Shared core and class transports

MIRRORS version 2 contains exactly `disc1` and `disc2` in `discs`, with GPU
transports in `bundles`. Each bundle descriptor has `bundle_id`,
`bundle_sha256`, `file`, `size`, `sha256`, `chunk_bytes`, `chunk_sha256` and
`mirrors`. The canonical GPU name is
`deep32-<version>-disc3-gpu-pack-<bundle_id>.iso`. The required runtime graph is
one `core`, one `embedder-<class>` for each of turing, ampere80, ampere86, ada,
hopper, blackwell100 and blackwell120, and one `ingestion`; an optional `full`
is required when declared by the capability manifest. Core contains reranker,
door, embedding/reranking models and runtime support. Each embedder contains its
one TEI image and small licence/provenance evidence. It has no model or runtime
kit payload. Selection downloads core plus exactly the requested class, then
only explicitly requested optional runtime capabilities. CPU TEI is no Forge class.

`capability_manifest:{file,sha256}` names the exact public capability asset.
Authenticate the root, hash MIRRORS bytes, validate its shape and root image
bindings, then fetch/read the capability asset and check its original bytes
against both root and index before parsing. Each `bundle_sha256` is the SHA-256
of `contract_validate.canonical()` applied to that exact capability bundle object:
sorted keys, ASCII JSON, two-space indentation and one final LF. Runtime bundle
coverage is exact; explicit source/build transports may name declared companions
but are never selected automatically. Unknown IDs, changed definitions, missing
classes and identical whole size/hash across distinct runtime transports refuse.
Capability validation separately refuses duplicated image/model bytes and other
large members across bundles. These checks bind a producer declaration to opaque
transport bytes; they do not prove an ISO's internal membership. Sealed producer
member inventories and real-media checks remain required upstream inputs.

New alpha.2 assembly and signing preflight require MIRRORS version 2 and refuse
the monolithic version 1 GPU layout. Version 1's explicit schema and signed
read/verification path remain for existing media; capability version 1 is refused
for the new bundle graph. No missing or rejected version triggers an unsigned
fallback. Disc 1 and disc 2 retain their child checksum names. All GPU transports
share `deep32-<version>-disc3-gpu-pack-SHA256SUMS` and the matching fixed README,
so the owner still signs three child lists and one root. SBOM subject agreement
includes every transport. There is no self-hash: capability bytes precede
MIRRORS and the root; neither is inserted into an enclosing object it hashes.

Assembly uses `--bundle-inputs bundle-inputs.json` instead of `--disc3`:

```json
{"format":"deep32-release-bundle-inputs","format_version":1,"bundles":[
  {"bundle_id":"core","source":"core.iso","sums":"core.sums"},
  {"bundle_id":"embedder-turing","source":"embedder-turing.iso","sums":"embedder-turing.sums"}
]}
```

The example shows two rows; the real list must name every signed transport,
including all seven class add-ons and ingestion. Paths resolve beside that local
input list. Each producer checksum file declares its one image. Assembly hashes
and independently reads back producer bytes, creates same-volume hard links and
never copies payloads. Metadata rows retain their explicit `discs` membership;
the capability asset can be external-only. Use `--image disc1=PATH`,
`--image disc2=PATH` and `--image <bundle_id>=PATH` for explicit whole verification.
Missing, repeated or extra producer IDs refuse before staging.

The explicitly bound capability file alone may contain up to 8 MiB, including
whitespace. Other release metadata retains the 5 MiB transfer/assembly limit;
the individual contract loader limits below still apply. The synthetic version 2
fixture is `fixtures/valid/mirrors-v2.json`; `mirrors.json` retains version 1.

## Loading contracts

Use the sibling `contract_validate.py` before any downstream action:

```text
python -I -B src/release-tools/contract_validate.py mirrors MIRRORS.json
```

`strict_load`/`strict_loads` reject duplicate properties, trailing data, invalid
UTF-8 and non-finite values. `validate_schema` checks the bundled schema profile;
`validate_contract` adds cross-field rules. Shape validation alone is insufficient.
Use `load_contract(kind, path)` for untrusted files: it applies raw byte limits
before parsing (doctor output 256 KiB, questions 1 MiB, reference 64 KiB,
capability manifest 8 MiB, other contracts 4 MiB). Object validation cannot
recover the size of whitespace discarded by an earlier parser.
`validate_contract_set` checks supplied shared inventories and bindings together.
It does not authenticate bytes or claim a supplied partial set is a complete
release. J02's authenticated allowlist and producer jobs enforce release closure.

Required semantic rules include:

* All shipped/metadata paths are safe relative ASCII paths. Public assets are flat
  basenames. Refuse traversal, absolute paths, drive/UNC syntax, controls, Windows
  reserved names, trailing dots/spaces, unsafe separators and case collisions.
  File-opening owners additionally refuse links/reparse points and unexpected
  files; a JSON schema cannot verify filesystem identity or ownership.
* Mirrors use safe public HTTP(S) URLs without credentials, queries, fragments,
  unsafe percent escapes, private hosts or unsupported schemes. The first mirror
  is the exact archive.org item/file URL. Validate every redirect separately.
  Release/role/kind/filename, chunk count, archive item and signed-root size/hash
  agreement are mandatory. Integers exclude booleans, floats and exponent forms.
* Asset-map entries grant no destination permission. The owning installer uses
  its compiled metadata allowlist, refuses self/root/signature/image mappings and
  verifies source/copied bytes against the authenticated root. Never flatten a
  media path by guessing its public basename. Map hashes come only from the root.
* Doctor row IDs are unique; prerequisites resolve without cycles, summaries and
  exits agree, and role/platform pairs are supported. Each non-OK row has a fixed
  plain fix sentence. An unsupported required check is WARN. L3's exact relayed
  path fix is retained in the synthetic output fixture. A receipt carries only
  protected slot references, never credentials or token-derived hashes.
* Question IDs and labels are unique. Question bytes, bundle identity, counts,
  effective settings hash, metric numerators and reference values agree. Settings
  hashes use compact sorted ASCII JSON without a final LF. Calibration uses three
  distinct complete runs with identical rank hashes; its input and receipt paths
  are explicit. Corpus span/public-rights validation remains the lead's task.
* SBOM subject paths are exact and unique. A shared archive appears once;
  `aliases:[{role,path}]` refers each role to that same canonical stored path,
  never to another copy. Input/component references resolve; source/build-only and
  runtime scopes remain distinct. Container inheritance requires a sealed base
  inventory or an explicit approved partial-inventory exception. Unknown optional
  licence/source metadata is `NOASSERTION`; no fabricated identity closes a gap.
  Partial component or licence coverage requires a public checksum-bound gap
  record. All public SBOM assets and media paths agree with ASSET-MAP.
* Reproducibility `proven` requires two distinct clean successful runs, identical
  final size/hash and a successful full byte comparison with an external receipt.
  `not-proven` records a precise reason without a fabricated run pair.
  A genuine failed comparison may retain its observed distinct runs, differing
  outputs and external receipt under `not-proven`; evidence is not discarded.
  `reused` records retained provenance and no build pair; upstream reuse never proves a
  local build or model training. Media/runtime assembly and local container build
  are separate scopes. Invented passing receipts test refusal/acceptance logic
  and establish no real reproducibility claim.

## Cycle-free hash graph and trust order

`hash-graph.nodes` has `{path,size,sha256,depends_on}`. A node's own measured
identity is metadata outside that subject; `depends_on` names files whose hashes
the subject contains. The validator refuses unknown references, duplicate/case
colliding paths, self edges and cycles. Root signatures are separate evidence;
the root checksum list excludes its own bytes and all detached signatures.

```text
sealed inputs
  -> child/container SBOMs
  -> runtime contents SBOM -> runtime archive
  -> disc contents SBOM -> frozen disc image
  -> external measured wrappers
  -> SBOM index / reproducibility records / statement
  -> checksum lists -> owner signatures
```

Contents SBOMs explicitly exclude their own bytes and generated checksum/SBOM
metadata from exhaustive self-hashing. They never contain the final enclosing
image/archive hash. Rebuild inner manifests after adding metadata. External final
wrappers are produced only after their subjects freeze and never inserted back
inside those subjects. On-media contents and published copies have identical
bytes. MIRRORS carries transport only; the signed root plus SBOM-INDEX binds
whole images to inventories and public selftest files.

The expected primary fingerprint is
`B205B406548627AABF5250D8A078C5F25D025DD6`, checked against
`install/release-key.fingerprint`. Authenticate the detached root and exact pinned
primary first, hash raw metadata bytes against the unique root entry second,
then strictly parse and validate. A schema, receipt hash or matching fingerprint
string alone is not signature verification. No fixture grants signing authority.

## Requirement coverage and remaining inputs

J01's frozen schema/fixture and DAG work is supplied here. The capability schema
freezes T-0200's structural interface without selecting or modifying payloads.
No image bytes, packaging selection, runtime probes or product interactions are
changed. Adversarial cases include unknown fields/settings, duplicate keys,
boolean lengths, traversal, unsafe URLs, duplicate subjects, alias mismatch,
self-hashes, cycles, inconsistent doctor output and false reproducibility claims.

Real release qualification still needs the producer owners' final measured
packaging/member/alias maps, resolved source/base/package/wheel/model/licence
inventories, the actual release version DOI, owner-observed mirror/torrent URLs,
public question/label and calibration receipts, qualified read-only signature
verifiers, actual build/comparison receipts and the sealed official SPDX schema
if full SPDX conformance is claimed. These inputs block their corresponding
release claims; they do not block synthetic contract implementation. No network,
Docker, WSL, installers, service changes, signing or publishing belongs here.
