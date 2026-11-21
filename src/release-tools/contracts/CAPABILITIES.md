# Capability manifest version 2

Version 2 implements the alpha.2 L8 and L9 rulings. Version 1 class-specific
cores are incompatible and refused; there is no legacy fallback. Release-root
signature and pinned primary verification still precede the raw manifest hash
check, strict parsing and semantic validation. A matching manifest alone does
not authenticate media or authorise installation.

The manifest lists all seven classes exactly once: `turing`, `ampere80`,
`ampere86`, `ada`, `hopper`, `blackwell100`, `blackwell120`. TEI's experimental
label does not remove a shipped class. CPU TEI is not a Forge class.

| Bundle | Hardware class | Runtime contents | Requires |
|---|---|---|---|
| `core` | null | Reranker and door images, complete embedding and reranking model snapshots, runtime kit and evidence | none |
| `embedder-<class>` | exact class | Exactly that class's TEI image | `core` |
| `ingestion` | null | Converter, builder, Qdrant, complete MinerU snapshots and runtime evidence | `core` |
| `full` (optional) | null | Existing full/model-bridge image roles, which may alias one archive | `core`, `ingestion` |

Every runtime bundle has exactly one `source-build-<bundle-id>` companion of kind
`corresponding-source-build`. Source/build companions remain separate from runtime
payload and contain the existing source, lock, build, licence and provenance
closure. They contain no images or models. Every bundle retains its existing
storage budgets and licence/provenance evidence requirements.

An embedder add-on carries its selected TEI archive and licence and provenance
evidence. Evidence must be smaller than 1 MiB. The sealed alpha.2 MinerU wheel
and PyMuPDF source archive qualify as licence evidence at their exact released
sizes and SHA-256 values under `licenses/source` or `licences/source`. These
source copies must be smaller than 256 MiB. Runtime wheel, package and build
members remain disallowed. The core has no TEI image.

Across bundles, image and model members and other members of at least 1 MiB
require distinct pairs of SHA-256 and size. The exceptions are identical copies
of the two released licence files and the exact shared `merges.txt`, `vocab.json`
and `.gitattributes` identities at their pinned Jina and MinerU paths. Any
shared member path must retain the same size, checksum and category across
bundles. The full/model-bridge alias refers to one member within one bundle.

The four released model snapshots admit `.gitattributes` as a pinned model file.
The three empty `.mineru_complete` files at the exact sealed ingestion paths
are generated provenance. Include each file in media closure and storage
accounting. Model snapshot equality uses the upstream pinned files, and storage
budgets require separate evidence in `STORAGE-MEASUREMENTS.json`.

## Storage budgets

Each bundle has one budget per backend, `docker-engine` and `docker-desktop`,
with one entry per storage domain. The detached `STORAGE-MEASUREMENTS.json`
holds one row per bundle and backend, and a row is either measured or declared
as a whole. A document may mix measured and declared rows.

- A measured row has, for each domain, the method `allocated-filesystem-bytes`
  or `allocated-vhd-bytes`, the digest of its trace and the allocated bytes of
  each phase. The budget peak must be at least the measured peak.
- A declared row has, for each domain, the method `declared-upper-bound`, the
  formula `{"name": "deep32-copy-path", "version": 1}` and the formula's exact
  inputs, each with its bytes and where they come from. It has no trace. The
  budget in the capability manifest carries `"method": "declared-upper-bound"`,
  so the signed manifest says which budgets were not measured.

The validator recomputes `member_bytes`, `image_archive_bytes` and
`member_slack_bytes` from the bundle's members. The assembler and the getter
compare `iso_bytes` with the length of the ISO in the release, and the C2
helper compares `carrier_bytes` with the source carriers. A declared budget is
refused when its peak is below the sum of its inputs or when any of its fields is
below the formula. It may ask for more than the formula, never less. Readers
treat a declared budget exactly like a measured one; the Forge preflight and the
inventory add the label to their storage line. The formula is
`capabilities.declared_budget`, and `storage_declare.py` writes the declared
rows from the media without measuring. A later signed revision replaces
declared rows with measured ones.

`select_runtime_bundles(manifest, hardware_class, include_ingestion=False,
include_full=False)` returns bundle objects in the order core, exact embedder,
requested ingestion and requested full. Full implies ingestion. It refuses an
unknown class or an absent full bundle and never substitutes a different class
or CPU runtime. Downloaders bind those exact IDs to the authenticated mirror
descriptors; filenames do not select a class.

The three capability fixtures contain deterministic synthetic bytes, invented
image IDs and measurements only. Their resolved-pins and image-index hashes
bind the exact canonical fixture files. They make no build, GPU or release
qualification claim.
