# Offline SBOM input adapters

These adapters implement J11 input measurement. J12 emission and verification
are documented in `EMISSION.md`. They do not select packs, assemble media,
fetch dependencies, run containers or sign files.
All interfaces use Python's standard library. Input trees are read only.

`inputs.normalise_inputs(manifest_path, roots)` returns a public dictionary.
`roots` maps public root IDs to local paths; those local paths are never returned.
`common.canonical(result)` supplies the build's existing ASCII JSON/LF writer.
All arrays produced by the normalisers have stable semantic ordering.
The optional `contents=True` argument permits prefreeze disc/runtime paths;
its stricter staged-member requirements are documented in `EMISSION.md`.

The following versioned interchange supplies detailed measurements for synthetic
qualification and the frozen public adapter. It supplements the shared J01
contract rather than replacing it. Public finalization and verification require
both this measured inventory and the frozen shared `sbom-input` sidecar; see
`EMISSION.md`. Unsupported mappings refuse publication.

## Sealed input manifest

`deep32-sbom-input/1` requires these fields, and refuses unknown fields:

| Field | Meaning |
| --- | --- |
| `format` | Exactly `deep32-sbom-input/1` |
| `release` | Public release identifier |
| `source_date_epoch` | Non-negative integer, not a boolean |
| `source_revision` | Resolved 40–64 lowercase hexadecimal source revision |
| `generator_sha256` | Recorded generator-source SHA-256; J12 must bind its actual emitter source |
| `roots` | Rows `{id, files}`; files are `{path, size, sha256}` |
| `subjects` | Exact shipped subjects described below |
| `components` | Explicit source/package/model records described below |
| `gaps` | Inline `deep32-sbom-gaps/1` object |
| `absent_roles` | Optional explicit list of omitted roles |
| `generated_from` | Optional `{result, source, evidence}` rows for distinct measured wheel/sdist components |
| `wheelhouses` | Optional measured selections described below; mandatory for wheel/sdist component records |

Every file reference is `{root, path}` naming a measured sealed file. Root trees
must be disjoint and exactly match their declared file lists. Files are hashed
before metadata is parsed. Traversal, absolute paths, links, reparse points,
special files, duplicate paths/JSON keys, non-finite numbers, unsupported versions
and unresolved checksum forms are refused. Private/exam/credential path patterns
are excluded; callers must supply authorised public metadata only. Data/content
files are hashed as opaque bytes, never parsed for corpus text. No cache sweep
or import of supplied Python files occurs.

Subjects have `id`, `kind` (`disc`, `container`, `runtime`), `file`, `role`,
`aliases`, `components` (component IDs), `component_coverage` (`complete`,
`partial`, `opaque-set`) and independent `licence_coverage` (`complete`,
`partial`). Each measured file is a subject once. Aliases are role labels for
that same file, not extra archives or extra subject rows. They cannot collide
with another subject/role/alias or an explicitly absent role. Class-specific
embedder roles are supplied by packaging; no class selection happens here.

Container subjects additionally require `image: {binding, inventory}`. `binding`
is the existing image-binding record, with config, index, manifest and archive
format identities. `inventory` references a sealed JSON file. Archive SHA-256
and size remain distinct from the image config digest. Runtime subjects require
`runtime_members`, the exact tar file rows `{path, size, sha256, mode?}`; all
members must be ordinary files, and modes are measured when specified.

## Components and gaps

Components require `id`, `kind`, `name`, `version`, `architecture`, `usage`,
`files` (file references) and `dependencies` (explicit component IDs).

Kinds are `source`, `deb`, `openbsd-package`, `openbsd-set`, `wheel`, `sdist`,
`model`, `tool`, `data`, `base-package` and `application`. Usages are `runtime`,
`build-only`, `bundled`, `source` and `external-model`. Co-location creates no
dependency. A mounted model remains `external-model`; the future SPDX emitter
must preserve this distinction when choosing relationships. All components
must attach to a shipped subject; all declared dependencies must resolve.

Optional fields are `licence_declared` (defaults to `NOASSERTION`),
`licence_evidence`, `required_licence`, `source_files`, `required_source`,
`source_url` (recorded public HTTPS provenance or `NOASSERTION`) and `revision`.
Licence/source evidence contains measured file references, not invented terms.
Mandatory absent licence/source evidence refuses the operation. Installed
Debian/OpenBSD/base package identity can come from a sealed complete inventory
without an archive file; this does not establish a signed archive pin or a
licence. Explicit dependency IDs must enumerate the owner's complete closure.
OpenBSD sets remain set components, with `opaque-set` subject coverage where
internals are not measured.

Models require immutable 40–64 hexadecimal `revision` and every file of one
dedicated snapshot root. Named weight hashes alone do not establish a snapshot.
No model licence is inferred from its name or the project's licence.

`deep32-sbom-gaps/1` has exactly `format` and `gaps`. Each gap is
`{id, target, field, reason, approval?}`, where `field` is `licence`, `source`
or `components`. Licence gaps name components and must exactly match unknown
declarations/evidence. Component gaps name subjects and must match partial
coverage, with a sealed lead-approval file reference. Approval is recorded
evidence, not cryptographic verification by this adapter. Required inputs cannot
be approved away. Public reasons are validated; operator paths, email addresses
and credentials are not emitted. J12 owns final gap policy and release coverage.

## Distribution and archive readers

`distributions.normalise_wheelhouse(folder, requirements, usage='runtime')`
reuses the build's exact hashed-lock grammar, canonical names, one selected
archive per row and sole lead-permitted sdist rule. Each result records the
selected file's actual SHA-256 separately from the lock's allowed hash set.
ZIP/tar metadata is read without extraction or executing setup code; `METADATA`,
`WHEEL`, `PKG-INFO` and licence-file evidence are measured. Metadata/filename
identities and compatibility tags must agree. Declared dependencies are retained
as metadata, not resolved or inferred. Personal author/email headers and free
descriptions are omitted. Missing optional declarations remain unknown.

A manifest wheelhouse is `{root, path, lock, usage, components}`. The lock is a
sealed file reference and component IDs must match the measured selected
archive's name/version/kind/usage/file hash. Source and built-wheel artefacts
remain distinct; `generated_from` requires separate component identities and a
sealed build-evidence reference. This adapter never claims a source build is
reproducible from an allowed input hash.

`archives.normalise_image(path, binding, inventory)` calls only the existing
pure `check_archive` image-binding path, never `resolve_loaded`. The complete
sealed final inventory is exactly `{format: 'deep32-sbom-image-inventory/1',
config_digest, coverage: 'complete', components}` and must be bound to the
measured config digest. It must be nonempty for this shipped-image profile.
Every inherited base/final component row is retained, then checked by the main
normaliser. Application locks cannot replace the base inventory.

This initial adapter deliberately requires a compatible sealed inventory for
all saved images, including zstd layers. It does not traverse layer filesystems
or interpret whiteouts. Missing/partial/mismatched inventory is a precise refusal:
`complete sealed inventory bound to config digest required`. The binder's
optional zstd diff-ID omission never becomes a completeness claim. Providing a
reviewed complete inventory is the supported route; no scanner is installed.

`archives.normalise_runtime_archive(path, members)` checks exact member names,
sizes, SHA-256 and optional modes without extraction. Missing/extra/duplicate
files, traversal, links, sparse entries and special members are refused.

No normaliser authenticates signatures, supplies upstream package metadata or
proves that an owner's asserted complete inventory actually enumerates every
filesystem component. The producer must measure/seal that closure. J12 now
provides selected-profile SPDX IDs, relationships and coverage indexes; see
`EMISSION.md`. Official schema validation, producer integration, reproducibility
receipts and signing remain separately gated work.
