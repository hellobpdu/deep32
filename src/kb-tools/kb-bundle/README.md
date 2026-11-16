# Knowledge-base bundles

A bundle carries one search database, lookup database, source documents and
flat vector matrix. The Workstation reads these files directly. The Linux
loader fills a new Qdrant collection from the same rows. Both database readers
use SQLite read-only immutable connections, including when reading a generation.

The three command-line programs need Python 3.12 or later and the standard
library: kb_bundle.py builds and checks bundles, kb_qdrant.py plans and loads
collections and compares exact search, and hooks/openpgp_verify.py checks an
OpenPGP detached signature. f32_sse.py provides the Workstation operation order.

## Commands

Run python3 -I -B before each program name below. Paths are supplied by the
caller. kb_rules.json is the default rules file beside kb_bundle.py.

```
kb_bundle.py write --generation-dir DIR --out DIR --created YYYY-MM-DDTHH:MM:SSZ
  --compat FILE --block FILE [--block FILE ...] [--rules FILE]
  [--cut-keys FILE] [--markings FILE] [--public] [--allow-path-prefix PREFIX ...]
  (--unsigned | --signer-type openpgp|external --signer-id ID --sign-cmd JSON-ARGV)
  [--json] [--report FILE]
kb_bundle.py check --dir DIR [--rules FILE] [--cut-keys FILE] [--markings FILE]
  [--public] [--allow-path-prefix PREFIX ...] [--deep] [--block FILE ...]
  [--expect-embedding-model-id ID] [--expect-embedding-served-name NAME]
  [--expect-embedding-revision REV] [--expect-dimension N]
  [--engine-version X.Y.Z] [--expect-reranker-revision REV]
  (--verify-cmd JSON-ARGV | --allow-unsigned) [--json] [--report FILE]
kb_bundle.py info --dir DIR [--c5] [--json]
kb_bundle.py rules --kb-dir DIR --documents FILE [--rules FILE] [--json]

kb_qdrant.py plan --bundle DIR --qdrant-url URL [--collection NAME]
  [--allow-remote] [--batch 256] [--wait-seconds 1200] [--probes 8]
  [--queries FILE] [--limit 50] (--verify-cmd JSON-ARGV | --allow-unsigned) [--json]
kb_qdrant.py load --bundle DIR --qdrant-url URL [--collection NAME]
  [--allow-remote] [--api-key-env NAME] [--batch 256] [--wait-seconds 1200]
  [--probes 8] [--queries FILE] [--limit 50] [--tolerance 1e-5]
  (--verify-cmd JSON-ARGV | --allow-unsigned) [--report FILE] [--json]
kb_qdrant.py check --bundle DIR --qdrant-url URL [--collection NAME]
  [--allow-remote] [--api-key-env NAME] [--probes 8] [--queries FILE]
  [--limit 50] [--tolerance 1e-5]
  (--verify-cmd JSON-ARGV | --allow-unsigned) [--report FILE] [--json]
kb_qdrant.py reference --bundle DIR --out FILE [--probes 8] [--queries FILE]
  [--limit 50] (--verify-cmd JSON-ARGV | --allow-unsigned)
kb_qdrant.py compare --reference FILE --engine FILE [--tolerance 1e-5] [--json]

hooks/openpgp_verify.py --keyring FILE --fingerprint FPR
  [--gpgv-argv JSON-ARGV] [--tmp-dir DIR] BUNDLE_JSON SIG
```

Repeat --allow-path-prefix for additional public root prefixes. The writer
accepts only an unpacked deep32-generation-export/1 generation, never a live
root. It refuses existing output and overlapping input/output locations. It
creates a partial folder, copies allowed files, signs, runs its own full checker
and then renames the partial folder. Failure removes the partial folder.

Use --public for release checks. Without it, unexpected root paths are warnings.
Provenance still has to use root-relative paths. --deep checks every vector's
length; ordinary checks still validate file hashes and all point records.
info reads BUNDLE.json only and does not verify; run check. info --c5 emits only
kb_generation and kb_fingerprint assignments on two lines.

--json emits one compact object on standard output. Human messages go to
standard error. --report writes an indented canonical copy of that object.

## Data formats

Generation names match [a-z0-9][a-z0-9._-]{0,39}. JSON emitted for bundle members
is ASCII, LF, sorted keys, one-space indentation and a final newline. BUNDLE.json
and MANIFEST.json must use precisely these bytes.

| File | Contents |
|---|---|
| BUNDLE.json | deep32-kb-bundle version 1; generation, 16-hex fingerprint, supplied created time, export_format and dense_format; inclusive engine min/max; embedding model_id, served_name, revision, dimension, number_format float32-le, distance cosine, pooling and both prompts; reranker; chunking policy/version; sorted block name/version/hash records; counts and four by_collection records; opt_in; manifest_sha256; signer type/id |
| MANIFEST.json | deep32-kb-bundle-manifest version 1; generation and fingerprint; every regular member except itself and the two BUNDLE files, with path/size/sha256; counts; grouped exclusions; source hashes and versions; writer and rules hashes; content-check statuses |
| dense/EXPORT.json | deep32-dense/1; generation and collection; points_count/dim; dtype float32, byte_order little, distance Cosine, row_order; vectors and points path/bytes/hash; 96-byte point layout; rows, flag counts and ascending-id checks; norm and duplicate statistics; source export hash and scroll-page count |
| kb_rules.json | deep32-kb-rules version 1; positive collection, category, citation-form, manifest-field and public-path lists, plus maximum member size |
| compat file | deep32-kb-compat version 1; engine min/max, embedding model_id/served_name/revision/pooling/query_prompt/document_prompt, reranker model_id/revision |
| query file | One JSON object per line: id, vector of numbers, filter null or an object containing must_doc_ids and/or must_not_doc_ids lists |
| ranked-hits file | deep32-ranked-hits version 1; engine, bundle identity, limit, cpu_flags, queries with id/filter/hits; each hit has point_id, chunk_id, score and score_bits (four little-endian float32 bytes as eight lower-case hex digits) |
| bundle report | deep32.kb-bundle.v1; tool/version/verb/status/exit; bundle identity; checks with pass/fail/not-run, counts and details; at most 200 refusals, full refusal total, warnings and counts |
| loader report | deep32.kb-qdrant.v1; verb/status/exit; Qdrant loopback/version facts, collection and bundle identity, cpu_flags and expected_identical, load batches/points/wait, readback mismatch counts and row ids, tolerance and per-query results, warnings |

The ranked reference carries limit + 64 hits. A truncated reference that cannot
judge a hit near the cut gives reference-too-short. A reference is evidence for
its supplied bundle identity and query list only.

Cut-key lists are private UTF-8 files, one matching document key per line.
Marking lists are private UTF-8 files, one exact case-sensitive string of at least
three characters per line. Blank lines and lines beginning # are ignored. Neither
list is copied, echoed or counted by entry. Bad lines are named by number only.

## Content checks

Only the four positive-list collections may occur. Manifest fields, source
folders and glossary categories must be listed. Glossary rows need non-empty
citations in the accepted document, RFC or standard forms. Document citations,
CLI and feature rows and page maps must refer to documents in this bundle.
These rules apply to both SQLite tables and source JSON. The writer refuses a
mixed row; it never trims, rewrites or drops data to pass a content rule.

Optional private key and marking lists add release checks. The public-path rule
examines stored path fields and configuration/build metadata, preserving the
listed installation prefixes. Rule T7 rejects machine paths in provenance and
reports. Build-root provenance uses the literal <root> plus a forward-slash
relative path. The fixed public installation root in the build report remains
permitted; source document text is never rewritten or scanned by category words.

Root files are copied by an allowlist: BUILD_REPORT.json, direct config JSON,
sqlite/rag.db, kb/kb.sqlite, sources/manifest.json, direct collection text files,
lookup JSON and page maps. Other root folders are counted as exclusions. Raw
scroll pages are export build records and are omitted from bundles. Machine
facts from the export do not enter the rewritten manifests.

Every point must have all six flags, its UUID5 must match its chunk, its document
must agree with the database, and its vector must be finite and already unit
length under the Workstation operation order. The writer checks all rows before
copying. Chunks without vectors are counted as a warning.

The rules verb emits counts only, including all violations even when the
individual refusal list reaches its 200-entry limit.

## Signatures

The builder holds no key. Sign and verify commands are JSON arrays of strings,
run with no shell, closed standard input, captured output and a 300-second limit.
Placeholders {bundle_json}, {sig} and {signer_id} undergo plain substitution.
The sign hook must exit zero and create a regular 1 to 65,536 byte signature.
Verification passes only on hook exit zero. Hook standard output is discarded.

Signer type none has a null id and requires --unsigned. OpenPGP uses a 40-digit
upper-case primary-key fingerprint. External uses a printable name of 1 to 128
characters. --allow-unsigned means the caller vouches by another route, such as
the enclosing signed disc manifest; it does not claim signature verification.

An OpenPGP signing template is:

```
["gpg","--local-user","{signer_id}","--armor","--detach-sign","--output","{sig}","{bundle_json}"]
```

An OpenPGP verification template is:

```
["python3","-I","-B","hooks/openpgp_verify.py","--keyring","PUBLIC-KEYRING","--fingerprint","{signer_id}","{bundle_json}","{sig}"]
```

The verifier copies inputs and the pinned keyring to a temporary folder with
an empty home/ and uses relative names. It requires one GOODSIG, one VALIDSIG,
binary signature class 00 and the pinned primary fingerprint, with no bad or
expired status. It searches PATH then the common Windows gpgv locations. Use
--gpgv-argv and --tmp-dir to make discovery and temporary storage explicit.
Test checksum hooks and canned status hooks are test material only.

## Qdrant loading and tolerance

Plan opens no connection. Load refuses an existing collection. URLs default to
loopback; a non-loopback IP requires --allow-remote. Environment and registry
proxies are bypassed. The API key is read once from the named environment
variable and is never printed. Load creates an unnamed Cosine collection,
keyword indexes on doc_id and chunk_id, and upserts rows in batches. It polls
until the collection is no longer yellow or the wait expires, checks the last
configuration, then scrolls every point back and compares ids, payloads and
float32 bits. It runs exact queries only after that read-back passes. Check uses
only read requests against an existing collection.

Service can use HNSW and may return different hits (class C1). This check always
sends params.exact true. Eight deterministic probes each run twice, once over
all documents and once with the opt-in documents excluded. Supplied queries
exercise the same arithmetic and filters.

The proposed absolute tolerance is 1e-5 on float32 cosine scores. For 1,024
components the SSE order rounds at most 64 + 2 + 3 times per score, about
69 * 2^-24 = 4.1e-6; two kernel orders can differ by about 8.2e-6. This is a
proposal, not a replacement for the real-kernel lab cross-check. Every report
records the chosen tolerance. Pass requires all six rules:

1. T1: the engine returns min(limit, allowed rows) hits.
2. T2: ids are unique and belong to the bundle.
3. T3: every returned score differs from its reference by no more than tolerance.
4. T4: every returned reference score is at least cutoff minus tolerance.
5. T5: every reference score above cutoff plus tolerance is returned.
6. T6: no pair of engine positions is inverted by more than tolerance.

Reference scores use cosine preprocessing and the four-accumulator SSE operation
order, then score descending and row ascending at ties. Per-query results report
all failed rules, the first failure and position, identical bits/order, exact
positions and maximum absolute difference. Bit identity is reported separately
from tolerance. On x86-64 it is expected when SSE is present and AVX and FMA
are not both present. Remote or unavailable CPU flags give no identity claim.

## Example compatibility record

Example only; the release pins come from the GPU disc. Replace every revision
placeholder with the release's pinned revision before making a real bundle.

```
{
 "format": "deep32-kb-compat",
 "format_version": 1,
 "engine": {"min": "1.0.0", "max": "1.0.0"},
 "embedding": {
  "model_id": "jinaai/jina-embeddings-v5-text-small-retrieval",
  "served_name": "jina-embeddings-v5-text-small",
  "revision": "RELEASE-PIN",
  "pooling": "last-token",
  "query_prompt": "Query: {text}",
  "document_prompt": "Document: {title}\nSection: {section path}\n\n{text}"
 },
 "reranker": {"model_id": "jinaai/jina-reranker-v3.5", "revision": "RELEASE-PIN"}
}
```

## Exit codes

| kb_bundle | Meaning |
|---|---|
| 0 | Success |
| 1 | Internal error |
| 2 | Usage, bad list or JSON-ARGV |
| 3 | Input missing or unreadable |
| 4 | Format, version, canonical JSON, name, symlink or size refused |
| 5 | Integrity failure |
| 6 | Content refused |
| 7 | Compatibility mismatch |
| 8 | Signature refused |
| 9 | Output refused |

| kb_qdrant | Meaning |
|---|---|
| 0 | Success |
| 1 | Internal error |
| 2 | Usage, URL, collection or API-key environment refused |
| 3 | Bundle missing |
| 4 | Bundle refused by its checker |
| 5 | Query or ranked-hits file invalid |
| 10 | Request failed or unexpected HTTP response |
| 11 | Collection exists |
| 12 | Read-back mismatch |
| 13 | Tolerance failed |
| 14 | Collection configuration differs |

| openpgp_verify | Meaning |
|---|---|
| 0 | Signature good |
| 1 | Usage |
| 2 | Input missing |
| 3 | gpgv not found or startable |
| 4 | Signature not good, including timeout |
