# C5. Library pack

Contract C5, version 1, draft of 2026-11-14. Not frozen. The Library's packs as they are made and installed
today, written down, plus five changes: parts under 2 GiB, cite maps bound to a knowledge-base fingerprint, two
content rules (no US-government works; only pictures whose licence can be passed on), no private packs, and
repeatable pack bytes. Written by the Library build
(stage 6). Read by the Workstation's importer, the Deep32 Library and its Reader, Ask (cite maps) and gates G1, G3
and G15. On disc 2 the packs sit under `/library/`.

## A pack today

- One uncompressed ustar stream: files and symbolic links only (no directories); owner root:wheel; mode without
  group or other write; one fixed mtime; each source folder walked in sorted order. The pack's own `MANIFEST.tsv`
  and `INFO` come last, under `var/db/d32/packs/<name>/`.
- `MANIFEST.tsv`, one row per member: path, type (`f` or `l`), mode, owner, group, bytes, sha256 (or `->target`),
  rights. Installing checks every file against it.
- `INFO`: the lines `name=`, `class=`, `release=`, `rights=`, `files=`, `bytes=`, `made=` (`made=` is read from the
  clock). Classes today: system, library and private.
- A reader pack installs one generation folder, `/d32/libcontent/.<prefix>-<generation>` (root:_d32users, folders
  0550, files 0440), and a stable pointer beside it. A new generation goes in beside the old one, which stays as the
  way back, and the pointer moves.
- Later generations travel as parts: the pack's delta tar with its `MANIFEST.tsv`, `INFO` and `SHA256.sig`, each
  part named with size and SHA-256 in the update's `library/PARTS.tsv`. Library files are signed with signify under
  a library key kept apart from the system-update key.
- Each generation holds Ask's `cite-map.tsv`: UTF-8 without BOM, LF line ends, the header
  `rag_doc_id rag_anchor pack article_id article_anchor title` (six TAB-separated columns; no tab or control
  character inside a field; no quoting), rows sorted by `(rag_doc_id, rag_anchor)`. `*` is the whole-document row
  and every mapped document has one. An unknown anchor falls back to it; an unknown document shows as not in the
  Library.
- `cisco-public.sqlite` pins `reader_generation`, `catalogue_sha256` and `doc_key` for each Cisco title.

## The packs of disc 2

| Pack | What it holds |
|---|---|
| main generation (today `lib-e26`) | the encyclopedia subset, IETF documents, Cisco papers and design guides, every picture, with index and cite map, in one generation |
| `lib-corpus` | the reader edition of the C3 knowledge base, rebuilt from it, with its cite map |
| `lib-encyclopedia` | the Wikipedia subset as a Kiwix ZIM (CC BY-SA 4.0) |
| `lib-catalogue` | catalogue, facts, word wheels |
| `cisco-public.sqlite` | the Cisco titles index, moved here from disc 1 |

## What changes

1. **Under 2 GiB.** No file on disc 2 reaches 2 GiB. Today lib-cisco is 98.9 % of 2 GiB, one update's delta tar is
   6,500,014,080 bytes, and one search database is 1.69 GB on its own. A pack stream longer than the part size is
   cut into `<pack>-<generation>.tar.partNNN` (from 001), every part but the last exactly the part size. The
   importer checks each part, then the joined stream's SHA-256, before it unpacks anything.
2. **Bound to a knowledge base.** Every generation with a cite map, and `cisco-public.sqlite`, records
   the C3 `generation` and `fingerprint` it was built from (new `INFO` keys `kb_generation=` and `kb_fingerprint=`).
   Ask uses a map only when its fingerprint equals the active bundle's. The importer switches the knowledge base and
   the Library pointers together and keeps the previous pair for roll-back.
3. **Content.** No US-government work of any agency anywhere in the Library, the reader edition of the corpus
   included. Pictures only under a licence that can be passed on; the update-7 "credited fair-use"
   pictures go. Every picture, sound and font has a recorded source and licence in the asset ledger
   (G15), and each file's `rights` value is taken from that ledger. The Cisco papers can be left out as a build
   option: the fallback if Cisco does not permit their redistribution, kept working.
4. **No private packs.** The private class, the encrypted packs and their pointers are not part of the distribution.
5. **Repeatable.** `made=` is given to the builder, as C3's `created` is, so the same inputs give the same pack
   bytes.

## Open points

- OPEN: the Library's own signing lane. Either (a) a new library key, made in the new build VM, signs
  every pack and part as today, or (b) disc 2's manifest, which the release key signs, takes that job
  and the per-part signatures go; the Workstation then checks that signature with `gpgv`. Decides: the maintainer.
- OPEN: the part size (C2 uses 1,900 MiB). Decides: the builder in stage 6.
- OPEN: new names for `lib-e26` and its pointer, which carry the old program's name. Decides: the
  maintainer.
- OPEN: whether `lib-encyclopedia` stays beside the main generation, which also holds the encyclopedia. Decides:
  the maintainer.
- OPEN: the order Ask searches the maps in, once the private map is gone (today: the reader edition first, then the
  main generation). Decides: the builder in stage 6.
