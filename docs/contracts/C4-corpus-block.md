# C4. Corpus block

Contract C4, version 1, draft of 2026-11-14. Not frozen. A block is one collection of documents with its record,
`block.json`. Written by the builder in stage 3 (the system block again in stage 9, after the documents freeze).
Read by the Forge's builder (rebuild from blocks, add a document by a catalogue entry), the importer on the Forge,
which keeps the blocks, gate G3, and every knowledge base built from them (C3 `blocks`). On disc 2 the blocks sit
under `/corpus/<block>/`.

## The four blocks

| Block | Documents | Text | What is in it | Searched |
|---|---|---|---|---|
| `rfc` | 365 | 27 MB | IETF RFCs, with status, errata and IANA tables | by default |
| `cisco` | 489 | 112 MB | TechNotes 263, configuration guides 126, command references 48, API guides 17, design guides 15, white papers 14, MIBs 3, release notes 3 | by default |
| `powershell` | 241 | 9 MB | about topics 156, conceptual 39, cmdlet reference 28, 7.6 differences 14, others 4 | only when named |
| `system` | rebuilt | about 13 MB today | the distribution's own documents, its source as searchable text, the kept research skill, model cards, vendor documents for the parts it uses | only when named |

The counts come from the corpus manifest (1,493 documents: 1,451 kept, 42 cut). The cut folders are never opened
or copied. Today's system collection (356 documents) is not carried: the block is rebuilt from the distribution.

## Layout

```
/corpus/<block>/
  block.json                the record (below)
  <block>-<version>.tar     docs/<doc_key>.txt for every document; pages/<doc_key>.tsv where a page map exists
```

The archive is a tar of regular files only, names in sorted order, owner 0:0, mode 0444, every mtime set to
`created`, so the same documents give the same bytes. No member over 64 MB (the corpus packager's limit today);
the archive stays under 2 GiB (C2, C5).

## block.json

| Field | Value |
|---|---|
| `format`, `format_version` | `"deep32-corpus-block"`, `1`; readers refuse any other |
| `block` | `rfc`, `cisco`, `powershell` or `system`; equal to every document's `doc_type` |
| `version`, `created` | the block's version; a UTC time given to the builder |
| `searched_by_default` | `true` for rfc and cisco, `false` for powershell and system |
| `archive` | `file`, `size`, `sha256` |
| `documents`, `bytes` | the document count and total text bytes |
| `converter` | the conversion chain and its version (PDFs go through a PyMuPDF chain today; RFCs are plain text) |
| `families` | cisco only: the checked list of families (ISE, IOS XE, SD-WAN, Catalyst Center and SD-Access, Secure Client) |
| `docs` | one entry per document, sorted by `doc_key` |

Each `docs` entry keeps the corpus manifest's field names: `doc_key`, `title`, `doc_kind`, `platform`, `release`,
`release_note`, `path` (inside the archive), `bytes`, `sha256`, `license`, `terms_url`, `source_url`, `page_url`,
`redistribute`, `near_duplicate_of`. The cisco block adds `family`. Today's `platform` is free text with about 250
values, so `family` comes from the checked list, which lets ISE be selected and reported alone.

## Rules

- `doc_key` is unique across the four blocks; membership is by `doc_type` alone.
- None of the 42 cut document keys appears. No catalogue field mapping documents to certification topics, and no
  release pins of the old lab, survive into a block.
- Word lists are never run on document text. Documents are checked by collection and handling
  marking only; nobody edits a document to pass a scan.
- `block.json` is covered by disc 2's signed manifest (C2, C5) and named by its SHA-256 in every knowledge base
  built from it (C3).
- Disc 2 also builds without the cisco block: the fallback if Cisco does not permit redistribution, kept working
  as a named build option. The corpus manifest marks every cisco document `redistribute: false`.

## Open points

- OPEN: the version scheme for blocks. Decides: the builder in stage 3.
- OPEN: the derived layers (CLI index, feature history, glossary, IANA, errata, RFC index): a fifth archive on
  disc 2, rebuilt from the kept blocks, or regenerated on the Forge, which then needs network caches. Decides:
  the builder in stage 3.
- OPEN: the cisco family list. The builder drafts it in stage 3; the maintainer may correct it.
- OPEN: whether every PDF-derived document ships its page map. Decides: the builder in stage 3.
- OPEN: the system block's documents and count (rebuilt in stage 3, again in stage 9). Decides: the builder.
- OPEN: where the catalogue entries for "add a document" live: inside `block.json`, or a `catalogue.jsonl` beside it.
  Decides: the builder in stage 3.
