# C3. Knowledge-base bundle

Contract C3, version 1, draft of 2026-11-14. Not frozen. One layout for the ready-built knowledge base on both
platforms. Written by the Forge's builder (stage 3 and every rebuild). Read by the Workstation's
importer (OpenBSD, exact search), the Linux engine loader (Qdrant) and gate G3. On disc 2 it sits
under `/kb/<generation>/`.

## Layout

The base is the engine's existing export format `deep32-generation-export/1` (`scripts/export_generation.py`),
which the Workstation already reads (`exact_local.DenseExport`). C3 adds `BUNDLE.json` and a signature.

```
<generation>/                 name matches ^[a-z0-9][a-z0-9._-]{0,39}$
  BUNDLE.json, BUNDLE.json.sig  the compatibility record (below) and its signature
  MANIFEST.json                 every other member with size and SHA-256; counts; versions; exclusions
  root/                         the engine root byte for byte, without logs/ and empty SQLite sidecars
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
| `engine` | `min`, `max`: the engine versions that may serve this bundle |
| `embedding` | `model_id`, `revision`, `dimension` (1024 today), `number_format` (`float32-le`), `distance` (`cosine`), `pooling` (`last-token`), `query_prompt` (`"Query: {text}"`), `document_prompt` (`"Document: {title}\nSection: {section path}\n\n{text}"`) |
| `reranker` | `model_id`, `revision`: the reranker the build's checks ran with |
| `chunking` | the engine's chunk policy id |
| `blocks` | per corpus block (C4): `name`, `version`, SHA-256 of its `block.json` |
| `counts` | `documents`, `chunks`, `points`; `by_collection`: rfc, cisco, powershell, system, each with `documents`, `chunks` |
| `opt_in` | collections searched only when named: `["powershell", "system"]` |
| `manifest_sha256` | SHA-256 of `MANIFEST.json`, so the signature covers every file |
| `signer` | the key that signed: a Forge's knowledge-base key, or the release chain for disc 2 |

## Trust and acceptance

Today's export is encrypted for one recipient and unsigned; a C3 bundle is signed and unencrypted. Disc 2's bundle
is trusted through disc 2's signed manifest (C2, C5); a bundle a person's Forge builds is signed with that Forge's
knowledge-base key, enrolled at pairing (C8). The importer accepts a bundle only when the signature holds, every
file matches `MANIFEST.json` and `manifest_sha256`, both formats are known, the installed engine lies within
`engine`, and `embedding` (id, revision, dimension) equals what the paired Forge serves. On that last mismatch,
meaning search stays off and the importer says why (the GPU link already checks served model ids and probe answers).

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
| Vectors | exact scan of `vectors.f32`; the C kernel keeps one operation order (SSE, four 128-bit accumulators) on any amd64 and refuses FMA or AVX code | Qdrant collection `rag_dense_<generation>` filled from `vectors.f32` and `points.bin`: one unnamed float32 vector, cosine, no quantisation; HNSW in service |

Gate G10 compares the Workstation with Qdrant's exact search mode. Qdrant's kernel depends on the
processor: it takes the AVX order only when the processor has both AVX and FMA, and the SSE order
otherwise. On a host with both, scores can therefore differ from the Workstation's in the last bits, and a tie at
the cut can change order. Every G10 run records the processor flags (avx, avx2, fma, sse4_1) and passes when the
ranked hits are equal except where scores differ by less than the stated tolerance.

## Open points

- OPEN: the tolerance and the tie rule at the cut. Decides: the builder proposes in stage 2; the maintainer accepts.
- OPEN: the loader that fills Qdrant from the flat form does not exist yet. Decides: the builder in stage 2.
- OPEN: the engine's version scheme for `engine`. Decides: the builder in stage 2.
- OPEN: signature tool and key type for Forge-built bundles, and whether disc 2's bundle also carries its own
  signature. Decides: the builder in stage 3, together with C5's signing choice.
- OPEN: a serving reranker whose revision differs: refused, or reported as degraded. Decides: the builder in
  stage 3.
- OPEN: mixed glossary rows: trimmed to their kept citations, or dropped. Decides: the builder in stage 3.
- OPEN: the raw scroll pages the export writes for its second-parser check: build record only, or on disc 2 too
  (the Workstation pack leaves them out today). Decides: the builder in stage 3.
