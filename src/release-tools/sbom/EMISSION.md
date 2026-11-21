# Offline SPDX emission

J12 consumes the explicit sealed interface in `INPUTS.md`. It uses only the
Python standard library and the build's existing pure archive/lock validators.
It does not run Docker, scan a host, install a scanner, fetch schemas or sign.
No real release component completeness or byte reproducibility is established
by the synthetic tests. Validation covers this selected SPDX 2.3 profile; an
official hash-sealed schema and an offline validator are still needed before
claiming full official-schema conformance.

Run the following from the source checkout with an isolated interpreter:

```text
python3 -I -B src/release-tools/sbom.py generator-hash
python3 -I -B src/release-tools/sbom.py contents --manifest INPUT.json --root media=STAGED-TREE --out NEW-CONTENTS
python3 -I -B src/release-tools/sbom.py finalise --manifest MEASURED.json --contract FROZEN-CONTRACT.json --root media=FROZEN-TREE --contents NEW-CONTENTS --out NEW-METADATA
python3 -I -B src/release-tools/sbom.py verify --manifest MEASURED.json --contract FROZEN-CONTRACT.json --root media=FROZEN-TREE --sboms NEW-METADATA
```

Repeat `--root ID=PATH` for each declared root. Local paths never enter output.
Set the manifest's `generator_sha256` to `generator-hash` before sealing inputs.
That value binds this CLI, all Python modules in the trusted SBOM package and
the three reused pure build implementations. No source hash is inferred from
Git status. The immutable producer source revision remains a separate input.
Exit 0 means this operation passed; exit 2 means invalid, missing or mismatched
inputs. A successful verify is not signature authentication.

The manifest's explicit non-negative `source_date_epoch` sets creation time.
Wall clock, source timestamps, absolute paths and the process environment have
no effect on metadata bytes. Canonical JSON is ASCII, sorted, LF terminated.
Missing required identities and mandatory source/licence evidence refuse
publication; optional unknown declarations are `NOASSERTION` with public gaps.

In contents mode a disc/runtime subject's `{root,path}` names its future
artefact, which need not exist or be listed in the sealed roots yet. It must
name a declared root. Runtime member rows require explicit modes and measured
source bytes; the producer is responsible for the install-name/source mapping.
Container archives must already be frozen and have their sealed complete
config-bound inventories. `normalise_inputs(..., contents=True)` exposes this
prefreeze mode; the default continues to require all final artefact bytes.

Contents files omit the future subject hash and exclude generated SBOM and
checksum metadata from the self-inventory. Public finalization and verification
require the frozen shared `sbom-input` contract through `--contract`, alongside
the detailed measured inventory in `--manifest`. SBOM files remain external;
the tool never changes a subject, and refuses embedded runtime SBOM metadata.
Finalization remeasures every frozen subject, regenerates the expected contents, and refuses
if the supplied contents differ. It writes external final wrappers with
checksum-bound contents references. Never place those wrappers back into their
subjects. `emit_document(..., child_documents=...)` also supports explicit
checksum-bound child associations; actual inter-subject media/member exports
and placement are owned by J13-J15, rather than guessed from role names.

Every final subject has exactly one index row. Aliases label its same bytes and
omitted optional roles remain explicit. All public metadata assets are flat;
the owning media/export job supplies its explicit media-to-public asset map.
Filename collisions refuse the operation. `GAPS.json` and `PROVENANCE.json`
preserve coverage and measured immutable image identities separately from the
saved archive hash; index references bind both companions by size and SHA-256.
`METADATA.json` lists every other emitted file, and API/CLI results list that
manifest too. Verify compares the entire exact directory to independently
regenerated canonical bytes, rejecting extras, omissions and changes.

Output directories must be new, with an existing ordinary parent, and disjoint
from every input root and the supplied contents directory. Files are computed
and validated before transactional publication. Temporary publication folders
are created only within the output parent. Callers must choose an authorised
work/output parent; tests set all temporary roots inside their task folder.

The frozen adapter preserves exact subject paths, hashes, sizes, aliases and
component/evidence bindings, and emits the shared `sbom-index` and `sbom-gap`
contracts with empty on-media destinations. Approved partial inventories,
source gaps, components without one measured archive/source-file hash, and
external-model scope have no lossless mapping in this adapter and are refused.
The measurement-only library retains its provisional format for component
qualification; it is unavailable as public final output through the CLI.

The release owner must authenticate the pinned signer and signed root first,
then verify root checksums, metadata and subject bindings. This tool's explicit
metadata list is input to that later checkpoint. The index is not a trust root.
Producer integration, inner manifest resealing, signing, release assembly and
actual two-build comparison receipts remain separate jobs.
