# C5. Library pack

Contract C5, version 2, draft of 2026-11-17. Not frozen. The Library's packs as they are made and installed
today, written down, plus five changes: parts under 2 GiB, cite maps bound to a knowledge-base fingerprint, two
content rules (no US-government works; only pictures whose licence can be passed on), no private packs, and
repeatable pack bytes. Written by the Library build
(stage 6). Read by the Workstation's importer, the Deep32 Library and its Reader, Ask (cite maps) and gates G1, G3
and G15. On disc 2 the packs sit under `/library/`.

Changed 2026-11-17: regular-file import, pack ownership and creation time, distribution
signatures, standard stream roster, streamed hash checking, part limits, retained program identity and map order.
Changed 2026-11-20: the Workstation switches the knowledge base before the Library.


## A pack today

- One uncompressed ustar stream: regular files only, without symbolic links, directories,
  special members or pax headers; tar UID:GID 0:0, owner root and group wheel or _d32users; mode without
  group or other write; one fixed mtime; members in sorted order.
  The pack's own `MANIFEST.tsv`
  and `INFO` come last, under `var/db/d32/packs/<name>/`.
- `MANIFEST.tsv`, one row per member: path, type (`f`), mode, owner, group, bytes, SHA-256,
  rights.
  Installing checks every file against it.
- `INFO`: the lines `name=`, `class=`, `release=`, `rights=`, `files=`, `bytes=`, `made=`
  (`made=` is a supplied UTC epoch, matching the fixed tar mtime).
  Disc 2's Library imports accept class `library` only.
- A reader pack installs one generation folder, `/d32/libcontent/.<prefix>-<generation>` (root:_d32users, folders
  0550, files 0440), and a stable pointer beside it. A new generation goes in beside the old one, which stays as the
  way back, and the pointer moves.
- Distribution generations travel as tar-stream parts with `MANIFEST.tsv` and `INFO`, each
  part named with size and SHA-256 in the content manifest's `library[].parts`.
  The release key's OpenPGP signature on disc 2's `MANIFEST.json` authenticates that inventory;
  separate per-pack or per-part signify signatures are not required for this import path.
- Each generation holds Ask's `cite-map.tsv`: UTF-8 without BOM, LF line ends, the header
  `rag_doc_id rag_anchor pack article_id article_anchor title` (six TAB-separated columns; no tab or control
  character inside a field; no quoting), rows sorted by `(rag_doc_id, rag_anchor)`. `*` is the whole-document row
  and every mapped document has one. An unknown anchor falls back to it; an unknown document shows as not in the
  Library.
- `cisco-public.sqlite` pins `reader_generation`, `catalogue_sha256` and `doc_key` for each Cisco title.

## The packs of disc 2

| Pack | What it holds |
|---|---|
| main stream `lib-main` (program pack `lib-e26`) | the encyclopedia subset, IETF documents, Cisco papers and design guides, every picture, with index and cite map, in one generation |
| `lib-corpus` | the reader edition of the C3 knowledge base, rebuilt from it, with its cite map |
| `lib-encyclopedia` | the Wikipedia subset as a Kiwix ZIM (CC BY-SA 4.0) |
| `lib-catalogue` | catalogue, facts, word wheels |
| `lib-cisco` | the separately supplied Cisco shelf |
| `lib-ietf` | the separately supplied IETF shelf |
| `cisco-public.sqlite` | the Cisco titles index, moved here from disc 1 |

## What changes

1. **Under 2 GiB.** No file on disc 2 reaches 2 GiB. Today lib-cisco is 98.9 % of 2 GiB, one update's delta tar is
   6,500,014,080 bytes, and one search database is 1.69 GB on its own. A pack stream longer than the part size is
   cut into `<pack>-<generation>.tar.partNNN` (from 001), every part but the last exactly the part size.
   The importer checks every part before it unpacks any, then rechecks parts and the
   complete stream's SHA-256 while extracting into an inactive stage, before activating its generation.
   It does not need a physical joined tar copy.
2. **Bound to a knowledge base.** Every generation with a cite map, and `cisco-public.sqlite`, records
   the C3 `generation` and `fingerprint` it was built from (new `INFO` keys `kb_generation=` and `kb_fingerprint=`).
   Ask uses a map only when its fingerprint equals the active bundle's. The Workstation's importer switches the
   knowledge base first and the Library pointers after it, in the same import, and keeps the pair in use before the
   import for roll-back. Until the new Library is switched, the Library in use before stays in use and Ask finds no
   map bound to the new knowledge base (decision DR-112).
3. **Content.** No US-government work of any agency anywhere in the Library, the reader edition of the corpus
   included. Pictures only under a licence that can be passed on; the update-7 "credited fair-use"
   pictures go. Every picture, sound and font has a recorded source and licence in the asset ledger
   (G15), and each file's `rights` value is taken from that ledger. A no-Cisco disc 2 fallback is not provided in alpha.2.
4. **No private packs.** The private class, the encrypted packs and their pointers are not part of the distribution.
5. **Repeatable.** `made=` is given to the builder, as C3's `created` is, so the same inputs give the same pack
   bytes.

## Open points

- Decided: disc 2's `MANIFEST.json.asc`, signed by the release OpenPGP key and checked with
  `gpgv`, authenticates the Library parts; no separate Library or per-part signature is required.
  Sources: `src/workstation/distrib/site/usr/local/d32/libexec/d32-import/imp_manifest.py`,
  `src/kb-tools/linux-line/disc2/disc2_assemble.py`
- Decided: the content assembler cap is 1,900 MiB (1,992,294,400 bytes), with each stream's
  exact `part_size` recorded in the manifest and checked in the range 512 through 1,992,294,400 bytes.
  C2's outer whole-object transfer uses separate 64 MiB range hashes.
  Sources: `src/kb-tools/linux-line/disc2/disc2_assemble.py`,
  `src/workstation/distrib/site/usr/local/d32/libexec/d32-import/imp_manifest.py`
- Decided: the distribution stream is `lib-main`, while program identity `lib-e26` and the
  `e26` stable pointer remain.
  Sources: `src/kb-tools/linux-line/disc2/disc2_assemble.py`, `src/workstation/ask-client/bridge/d32-ask`
- Decided: the standard roster keeps `lib-encyclopedia` beside `lib-main`, along with
  `lib-corpus`, `lib-catalogue`, `lib-cisco` and `lib-ietf`.
  Sources: `src/kb-tools/linux-line/disc2/disc2_assemble.py`
- Decided: Ask loads the reader edition (`lib-corpus`) first, then `lib-e26`, keeping the
  first duplicate key; exact section matches take precedence over whole-document fallbacks.
  Sources: `src/workstation/ask-client/bridge/d32-ask`

Sources: `src/kb-tools/linux-line/disc2/disc2_assemble.py`,
`src/workstation/distrib/site/usr/local/d32/libexec/d32-import/imp_manifest.py`,
`src/workstation/distrib/site/usr/local/d32/libexec/d32-import/imp_units.py`,
`src/workstation/distrib/site/usr/local/d32/libexec/d32-import/imp_switch.py`,
`src/workstation/ask-client/bridge/d32-ask`
