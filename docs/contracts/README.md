# Deep32 contracts

The parts of Deep32 meet at ten small written contracts. Each is one page with a version number. A part is built
and tested against its contract alone, so the discs, the installer and the relay can be built in any order and
still fit together.

## The ten

| # | Contract | File | Fixes | Status |
|---|---|---|---|---|
| C1 | Install matrix | `C1-install-matrix.md` | the schema of `install/matrix.json`: detection rules, forms, sizing, steps, status, reference figures | draft, version 1, 2026-11-14 |
| C2 | Release manifest | `C2-release-manifest-draft.md` | parts, sizes, hashes, the bill of materials tying three disc versions together, the signature | draft, version 1, 2026-11-14 (written with the release tooling) |
| C3 | Knowledge-base bundle | `C3-knowledge-base-bundle.md` | one layout for both platforms, the compatibility fields, the signature, what it may hold | draft, version 1, 2026-11-14 |
| C4 | Corpus block | `C4-corpus-block.md` | `block.json` and the archive layout of the four blocks | draft, version 1, 2026-11-14 |
| C5 | Library pack | `C5-library-pack.md` | the existing pack layout, parts under 2 GiB, cite maps bound to a knowledge-base fingerprint | draft, version 1, 2026-11-14 |
| C6 | Console status channel | `C6-console-status.md` | the status lines a new node writes: stage, sign-in address, pairing code, error | draft, version 1, 2026-11-14 |
| C7 | Settings disc | `C7-settings-disc.md` | names and choices for one node; the rule that no secret is ever added | draft, version 1, 2026-11-14 |
| C8 | Network | `C8-network.md` | node names, ports, tokens, discovery, one pairing procedure, the transport switch | draft, version 1, 2026-11-14 |
| C9 | Headend | `C9-headend.md` | the D32U/1 link, the engine adapters, what the relay reports, the MCP entry, the box an Ask session runs in | draft, version 1, 2026-11-14 |
| C10 | Installer and inventory | `C10-inventory-draft.md` | verbs, JSON output, exit codes, the state file, human-only moments, inventory and benchmark results | draft, version 1, 2026-11-14: the inventory half; the installer half comes in stage 9 (written with the inventory program) |

None is frozen yet. They are frozen at the end of stage 1, before parts are built against them.

## How they connect

- C1 feeds C10: the inventory judges a machine by the matrix; the installer plans from it.
- C3, C4 and C5 describe what disc 2 holds; C2 signs the discs that carry them. C3 names the blocks (C4) it was
  built from; C5's cite maps name the knowledge base (C3) they belong to.
- C6 carries the sign-in address and pairing codes that C8's pairing needs; its error codes come from C10.
- C7 sets the choices C8 reads (`transport`, `publish`) and the profile C1 names.
- C8 gives C9 its pairing, its tokens and the relay's reach on the tailnet.

## Words used on these pages

- **Stage.** The build runs in stages, and an open point names the stage whose builder settles it: 1 the
  contracts and the inventory program; 2 a headless engine on a small fixture; 3 the engine, the corpus
  blocks and the full knowledge base; 4 disc 3 (the Forge); 5 disc 1 (the Workstation); 6 disc 2 and its
  importer; 7 the network; 8 the headend; 9 the installer, the matrix's steps, the benchmarks and the
  documents; 10 the release checks and the maintainer's review; 11 the release.
- **Gate.** A release check that must pass on the exact build before it is published. The pages name: G1
  restricted wording (term lists against the tree and the discs); G2 secrets (keys, tokens, sign-in files,
  long random strings); G3 corpus (the blocks and the knowledge base hold only the four shipped
  collections, counted from the data); G10 same answers on both systems (the Workstation and the Linux
  engine return the same ranked hits for the check sets); G11 the relay's agent is boxed in (an Ask session
  can call the search tools and nothing else); G13 no defaults, no collision (no default password or key on
  any disc; the installed copy has its own names and refuses to run where an existing install is found);
  G15 public-grade (no personal values; every picture, sound, font and icon has a recorded source; the
  licence and notice files are present).

## Conventions

- Each page opens with its number, version, date and state, who writes it and who reads it.
- **OPEN:** marks a point not settled yet. Each names who decides: the maintainer (the project
  owner) or the builder of the stage named.
- **Sources:** a page that rests on files in this repository ends with a line naming them.

## Changing a contract

Before a contract is frozen, edit it freely and keep version 1. After that, any change raises its version and is
recorded in the build record with the reason and the list of parts that must be retested. C2's draft says which
programs that means for the release manifest.
