# C4. Corpus block

Contract C4, version 2, draft of 2026-11-17. Not frozen. A block is one collection of documents with its record,
`block.json`. Written by the builder in stage 3 (the system block again in stage 9, after the documents freeze).
Read by the Forge's builder (rebuild from blocks, add a document by a catalogue entry), the importer on the Forge,
which keeps the blocks, gate G3, and every knowledge base built from them (C3 `blocks`). On disc 2 the blocks sit
under `/corpus/<block>/`.

Changed 2026-11-17: rebuilt system census, split archives, exact byte limits, block versions,
converter and page-map fields, and supplied derived layers.


## The four blocks

| Block | Documents | Text | What is in it | Searched |
|---|---|---|---|---|
| `rfc` | 365 | 27 MB | IETF RFCs, with status, errata and IANA tables | by default |
| `cisco` | 489 | 112 MB | TechNotes 263, configuration guides 126, command references 48, API guides 17, design guides 15, white papers 14, MIBs 3, release notes 3 | by default |
| `powershell` | 241 | 9 MB | about topics 156, conceptual 39, cmdlet reference 28, 7.6 differences 14, others 4 | only when named |
| `system` | 85 in the standard generation-2 census | rebuilt | the distribution's own documents, its source as searchable text, the kept research skill, model cards, vendor documents for the parts it uses | only when named |

The counts come from the corpus manifest (1,493 documents: 1,451 kept, 42 cut). The cut folders are never opened
or copied. Today's system collection (356 documents) is not carried: the block is rebuilt from the distribution.

## Layout

```
/corpus/<block>/
  block.json                the record (below)
  <block>-<version>.tar      unsplit archive when size <= part_size
  <block>-<version>.tar.partNNN  otherwise: ordered parts from 001, at most 999
```

The archive holds `docs/<doc_key>.txt` for every document and `pages/<doc_key>.tsv` for each
recorded page map.

The archive is a tar of regular files only, names in sorted order, owner 0:0, mode 0444, every mtime set to
`created`, so the same documents give the same bytes.
No member exceeds 64,000,000 bytes.
The whole archive may exceed 2 GiB; every part is at most `part_size`, which is between
512 and 1,992,294,400 bytes, and every part except the last is exactly `part_size` bytes.
The folder contains only `block.json` and the unsplit archive or its listed parts.

## block.json

| Field | Value |
|---|---|
| `format`, `format_version` | `"deep32-corpus-block"`, `1`; readers refuse any other |
| `block` | `rfc`, `cisco`, `powershell` or `system`; equal to every document's `doc_type` |
| `version`, `created` | The block's version, three numeric components `x.y.z`; a UTC time given to the builder |
| `searched_by_default` | `true` for rfc and cisco, `false` for powershell and system |
| `archive` | `file`, `size`, `sha256`, `part_size`, `parts`; `parts` is empty for an unsplit archive or an ordered list of `{index, name, size, sha256}` |
| `documents`, `bytes` | the document count and total text bytes |
| `converter` | `{name, version, note}`, with `version` a string or `null`; PDFs go through a PyMuPDF chain today, and RFCs are plain text |
| `families` | cisco only: ordered `{name, documents}` counts for the checked family vocabulary (ISE, IOS XE, SD-WAN, Catalyst Center and SD-Access, Secure Client) |
| `docs` | one entry per document, sorted by `doc_key` |

Each `docs` entry keeps the corpus manifest's field names: `doc_key`, `title`, `doc_kind`, `platform`, `release`,
`release_note`, `path` (inside the archive), `bytes`, `sha256`, `license`, `terms_url`, `source_url`, `page_url`,
`redistribute`, `near_duplicate_of`.
It also records `page_map`: `null` or `{path, bytes, sha256}` for the exact corresponding
archive member.
The cisco block adds `family`, a nonempty ordered list of checked family names.
Today's `platform` is free text with about 250
values, so `family` comes from the checked list, which lets ISE be selected and reported alone.

## Rules

- `doc_key` is unique across the four blocks; membership is by `doc_type` alone.
- None of the 42 cut document keys appears. No catalogue field mapping documents to certification topics, and no
  release pins of the old lab, survive into a block.
- Word lists are never run on document text. Documents are checked by collection and handling
  marking only; nobody edits a document to pass a scan.
- `block.json` is covered by disc 2's signed manifest (C2, C5) and named by its SHA-256 in every knowledge base
  built from it (C3).
- A no-Cisco disc 2 fallback is not provided in alpha.2. The corpus manifest marks every cisco document `redistribute: false`.

## Open points

- Decided: block versions are three numeric components `x.y.z`, also used in the archive name.
  Sources: `src/gpu-pack/import/vendor/validate_block.py`
- Decided: the corpus-package builder accepts supplied derived JSON layers under `kb/`,
  copied from a rebuilt and pruned kept-document layer set; C3 carries the serving lookup files and source KB JSON.
  This path requires no fifth C4 archive and does not automatically regenerate layers.
  Sources: `src/kb-tools/linux-line/s3-pre/corpus_from_blocks.py`,
  `src/gpu-pack/containers/builder/derived_layers.py`, `src/kb-tools/kb-bundle/kb_bundle.py`
- OPEN: the cisco family list. The builder drafts it in stage 3; the maintainer may correct it.
- OPEN: whether every PDF-derived document ships its page map. Decides: the builder in stage 3.
- Decided: the standard generation-2 content assembly requires 85 rebuilt system documents.
  The document list and subsequent documentation-freeze rebuild remain OPEN producer inputs.
  Sources: `src/kb-tools/linux-line/disc2/disc2_assemble.py`
- OPEN: where the catalogue entries for "add a document" live: inside `block.json`, or a `catalogue.jsonl` beside it.
  Decides: the builder in stage 3.

Sources: `src/gpu-pack/import/vendor/validate_block.py`,
`src/kb-tools/linux-line/s3-pre/corpus_from_blocks.py`,
`src/gpu-pack/containers/builder/derived_layers.py`,
`src/kb-tools/linux-line/disc2/disc2_assemble.py`, `src/kb-tools/kb-bundle/kb_bundle.py`
