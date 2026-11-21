# C3. Knowledge-base bundle

Contract C3, version 2, draft of 2026-11-17. Not frozen. One layout for the ready-built knowledge base on both
platforms. Written by the Forge's builder (stage 3 and every rebuild). Read by the Workstation's
importer (OpenBSD, exact search), the Linux engine loader (Qdrant) and gate G3. On disc 2 it sits
under `/kb/<generation>/`.

Changed 2026-11-17: serving-file selection, embedding and chunking fields, signature hooks,
engine versions, Qdrant loader, tolerance and tie checks, glossary pruning and excluded scroll pages.


## Layout

The base is the engine's existing export format `deep32-generation-export/1` (`scripts/export_generation.py`),
which the Workstation already reads (`exact_local.DenseExport`).
C3 adds `BUNDLE.json` and supports a detached signature.

```
<generation>/                 name matches ^[a-z0-9][a-z0-9._-]{0,39}$
  BUNDLE.json, BUNDLE.json.sig  the compatibility record (below) and optional detached signature
  MANIFEST.json                 payload members with size and SHA-256; counts; versions; exclusions
  root/                         only the named serving files below, copied byte for byte
    sqlite/rag.db               documents, chunks, sections; FTS5 tables (opt-in collections in their own table)
    kb/kb.sqlite                lookup tables: rfc, rfc_alias, errata, iana, glossary, cli, feature
    sources/                    the corpus it was built from: manifest.json, <collection>/*.txt, kb/, pages/
    config/, BUILD_REPORT.json  the root's serving overlay and its build record
  dense/vectors.f32             N x D float32, little-endian, rows in point-id order
  dense/points.bin              N records of 96 bytes: point id, chunk id, document id, flags
  dense/EXPORT.json             format deep32-dense/1: collection settings, counts, SHA-256 of both files
  repo/config/*.json            the engine's own configuration layer at the build commit
```

Ids: `doc_` and `chk_` followed by 64 hex digits; a point id is the UUID version 5 (URL namespace) of the engine's
fixed point-name prefix and the chunk id.

## BUNDLE.json

| Field | Value |
|---|---|
| `format`, `format_version` | `"deep32-kb-bundle"`, `1`; readers refuse any other |
| `generation`, `fingerprint` | the generation name; the engine's corpus fingerprint (16 hex digits) |
| `created` | a UTC time given to the builder, never read from the clock: same inputs, same bytes |
| `export_format`, `dense_format` | `"deep32-generation-export/1"`, `"deep32-dense/1"` |
| `engine` | `min`, `max`: inclusive engine-version bounds, each three numeric components `x.y.z` |
| `embedding` | `model_id`, `served_name`, `revision`, `dimension` (1024 today), `number_format` (`float32-le`), `distance` (`cosine`), `pooling` (`last-token`), `query_prompt` (`"Query: {text}"`), `document_prompt` (`"Document: {title}\nSection: {section path}\n\n{text}"`) |
| `reranker` | `model_id`, `revision`: the reranker the build's checks ran with |
| `chunking` | `{policy, version}`, both nonempty strings, equal to the build record's `chunker` |
| `blocks` | per corpus block (C4): `name`, `version`, SHA-256 of its `block.json` |
| `counts` | `documents`, `chunks`, `points`; `by_collection`: rfc, cisco, powershell, system, each with `documents`, `chunks` |
| `opt_in` | collections searched only when named: `["powershell", "system"]` |
| `manifest_sha256` | SHA-256 of `MANIFEST.json`, so the signature covers every file |
| `signer` | `{type, id}`: `openpgp` with a 40-digit uppercase primary fingerprint, `external` with a printable identifier, or `none` with `null` when the caller supplies outer trust |

## Trust and acceptance

Today's export is encrypted for one recipient and unsigned; a C3 bundle is unencrypted and supports a detached signature.
Disc 2's bundle is trusted through disc 2's signed content manifest (C2, C5), so its own
signature may be omitted; a bundle a person's Forge builds requires signing and verification hooks and is signed with that Forge's
knowledge-base key, enrolled at pairing (C8).
A Forge-built bundle is verified with its configured signature hook; for a disc 2 import,
the verified outer content-manifest signature supplies trust even when `BUNDLE.json.sig` is omitted.
The importer requires every file to match `MANIFEST.json` and `manifest_sha256`, both formats
to be known, and the installed engine to lie within `engine`.
The Workstation compares the bundle's embedding id, revision and dimension with the paired
Forge's served identity; a mismatch leaves meaning search off and the importer reports why.
The GPU link already checks served model ids and probe answers.

## What it may hold (gate G3, counted from the data)

- Documents of rfc, cisco, powershell and system only; none of the 42 cut document keys; no handling markings.
- No row derived from a cut collection. Today's corpus package has such rows: the glossary categories
  `nist-control` (1,189), `compliance` (427) and `crypto` (200, plus 21 mixed), two more glossary categories (152
  and 118 rows) and 578 CLI-index rows of one platform. Rule: every glossary, CLI and feature row cites kept
  documents only; a row left with none is dropped.
  The derived layers are rebuilt from the kept blocks. The catalogue's certification-topic field goes.

## Loading, and identity across platforms

| | Workstation (OpenBSD) | Linux engine (Forge with engine, headless) |
|---|---|---|
| Text, keyword search, lookups | `rag.db` and `kb.sqlite`, opened read-only and immutable | the same files |
| Vectors | exact scan of `vectors.f32`; the C kernel keeps one operation order (SSE, four 128-bit accumulators) on any amd64 and refuses FMA or AVX code | Qdrant collection `rag_dense_` plus the generation with characters outside `A-Za-z0-9_-` replaced by `_`, filled from `vectors.f32` and `points.bin`: one unnamed float32 vector, cosine, no quantization; HNSW in service |

Gate G10 compares the Workstation with Qdrant's exact search mode. Qdrant's kernel depends on the
processor: it takes the AVX order only when the processor has both AVX and FMA, and the SSE order
otherwise. On a host with both, scores can therefore differ from the Workstation's in the last bits, and a tie at
the cut can change order.
Every G10 run records the processor flags (avx, avx2, fma, sse4_1) and applies the inclusive
score-tolerance, ranking and cut rules below.

## Open points

- Decided: default absolute tolerance is `1e-5`, configurable to a finite nonnegative value.
  The comparison requires the expected hit count, unique known points, each score within
  tolerance, no reference score below the cut minus tolerance, every point above the cut plus tolerance, and no
  rank inversion larger than tolerance; ties within tolerance may change order or membership at the cut.
  Sources: `src/kb-tools/kb-bundle/kb_qdrant.py`
- Decided: `kb_qdrant.py load` creates the collection, loads the flat vectors and payloads,
  creates document/chunk indexes, reads them back and compares exact query results.
  Sources: `src/kb-tools/kb-bundle/kb_qdrant.py`
- Decided: engine versions are three numeric components `x.y.z`, compared as integer tuples
  against inclusive `min` and `max` bounds.
  Sources: `src/kb-tools/kb-bundle/kb_bundle.py`
- Decided: Forge-built bundles require OpenPGP or external signing and verification hooks;
  disc 2 may omit the bundle signature under its signed content manifest.
  A single mandatory cryptographic algorithm for an external hook remains OPEN.
  Sources: `src/gpu-pack/containers/builder/builder.py`, `src/kb-tools/kb-bundle/kb_bundle.py`,
  `src/kb-tools/linux-line/disc2/disc2_assemble.py`
- Decided: a serving reranker revision differing from an explicitly expected revision emits
  `reranker-revision-differs`, a warning rather than a refusal.
  Sources: `src/kb-tools/kb-bundle/kb_bundle.py`
- Decided: allowed glossary categories retain only valid kept citations, dropping the row
  when none remain; disallowed categories are dropped entirely.
  Sources: `src/gpu-pack/containers/builder/derived_layers.py`
- Decided: raw `dense/scroll/` pages remain export build records and are omitted from C3
  bundles; the bundle retains their count and the source EXPORT hash as provenance.
  Sources: `src/kb-tools/kb-bundle/kb_bundle.py`

Sources: `src/kb-tools/kb-bundle/kb_bundle.py`, `src/kb-tools/kb-bundle/kb_qdrant.py`,
`src/gpu-pack/containers/builder/builder.py`, `src/gpu-pack/containers/builder/derived_layers.py`,
`src/kb-tools/linux-line/disc2/disc2_assemble.py`,
`src/workstation/distrib/site/usr/local/d32/libexec/d32-import/imp_units.py`,
`src/workstation/distrib/site/usr/local/d32/libexec/d32-import/imp_switch.py`
