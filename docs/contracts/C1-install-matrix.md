# C1. Install matrix

Contract C1, version 2, 2026-11-17. Not frozen. This page describes `install/matrix.json` as it stands
(schema `deep32.matrix.v1`, matrix version 0.3.1). The builder writes it by hand; `status`, `last_tested` and
`bench_reference` change only from a recorded test run. Five things read it, so they cannot drift: the inventory's
verdict (C10), the installer's plan, the README's compatibility tables, the benchmark comparison and the record of
the profiles a release tested. It is JSON because Windows PowerShell 5.1 reads JSON with nothing installed.

Changed (2026-11-17): state the measured GPU-memory tier thresholds and null tier, and close the converter port decision.

## Rules for every reader

- Refuse a file whose `schema` is not `deep32.matrix.v1`, compared case-sensitively: the member is `schema` exactly
  and its value that string exactly (both programs, since 2026-11-14). `contract` and `contract_version` name this page,
  `matrix_version` names the content, and `draft` stays true until the first release.
- Facts are case-sensitive dotted names (`os.family`, `cpu.avx2`, `gpus`). A missing or null fact is unknown, never
  false. Members a reader does not use are ignored.
- Both inventory programs (PowerShell and shell) give the same verdict for the same facts and matrix.

## Members

| Member | Holds | Derived facts it gives |
|---|---|---|
| `status_words` | reference, supported, experimental, planned, each with its meaning | |
| `rule_language` | the grammar of every `when` and `match`: `[fact, op, value]`, `{"any": [...]}`, `{"all": [...]}`, a plain list meaning all; ops `eq ne in nin ge gt le lt re present absent nottrue notfalse`; three-valued logic for unknown facts; a small lower-case regex subset | |
| `ports` | port number and use | `port.<n>` (busy or free), `ports.busy_count` |
| `cpu_classes` | `id`, `when`, `workstation`, `forge`, `text`; the first class whose `when` holds wins | `cpu.class`, `cpu.ws`, `cpu.forge` |
| `gpu_classes`, `gpu_class_rule`, `gpu_notes` | NVIDIA classes: name patterns, compute capabilities, architecture, embeddings-server image tag (`tei`), upstream status, Forge verdict; the rule that classes a card and picks the best one | `gpu.nvidia`, `gpu.usable`, `gpu.class`, `gpu.forge`, `gpu.tei`, `gpu.upstream`, `gpu.mem_gb`, `gpu.driver` |
| `gpu_mem_tiers` | `id`, `min_gb`, what runs; the first tier the card's memory reaches: `11` at 10.5 GiB, `8` at 7.5 GiB, `6` at 5.5 GiB, `0` below 5.5 GiB; no selected card or unknown memory gives null. Tier `0` is keyword search only; meaning search needs a separately paired NVIDIA Forge. | `gpu.mem_tier` |
| `hw_profiles` | known hardware (today `latitude-5430`) | `hw.profile` |
| `sizing` | what the host keeps (2 threads, 4 GB), four tiers with VM ranges, the minimums, the allocation rule | `mem.basis_gb`, `size.threads`, `size.tier`, `size.both`, `size.ws_fits`, `size.forge_fits`, sizes per profile |
| `disk_gb` | free space per layout, marked `provisional` | `disk.free_gb` (largest free volume), `disk.ssd` |
| `requirements` | id: `kind`, `when`, `need` (one sentence), optional `do` (the fix) | |
| `requirement_kinds`, `fit_words` | hardware, fix, human, confirm, auto; ready, unconfirmed, changes, no | |
| `warnings`, `human_moments` | id, `when`, `text`; id: the sentence shown when the installer stops for the person | |
| `adapters`, `hosts_and_guests` | adapter name and its state; the rows of the README's host table | |
| `profiles`, `ranking`, `steps_note` | the profiles (below); the ranking rule in words; where steps will come from | |

Layout facts (`layout.second_machine`, `layout.forge_elsewhere`, `layout.headend_elsewhere`) come only from a facts
file: no machine can check another machine.

The relay port in `ports`, 7452, is production's relay port. The inventory checks it today so that a machine that
already runs a relay is noticed; the installed relay gets its own port in stage 8 (the installed copy
has its own folder, task, port, key and credential names: C8), and then this row and the `ports-relay` requirement
change together with the inventory's port check.

## A profile

| Field | Meaning |
|---|---|
| `id`, `name` | stable id (used by the installer and the settings disc, C7) and a name for people |
| `target` | the aim: `reference`, `supported` or `experimental` |
| `status`, `last_tested` | `planned` and null until a recorded run; then the word that run earned, and its date |
| `rank` | order among profiles of the same group (below) and equal fit, lower first |
| `machines` | 1: everything the profile installs runs on this machine; 2: the layout needs another machine for the Workstation or the Forge (`two-machines`, `baremetal`, `headend-only`). Where the headend sits does not count. Only a JSON number of 2 or more counts as 2; absent or anything else is 1. Each ranked entry of the verdict repeats it (C10) |
| `found`, `match` | what the machine has, in words; the conditions that make the profile a candidate |
| `requires` | requirement ids; each is met, missing or unknown |
| `size` | `both`, `ws`, `forge`, `each` or `none`: which VMs this machine sizes |
| `parts` | `workstation`, `forge`, `relay`: where each part runs, in words |
| `adapters`, `human` | the adapters its steps use; ids from `human_moments` |
| `steps`, `bench_reference` | ordered steps (empty until stage 9); reference benchmark figures (null until a reference run) |

Version 0.3.1 holds twelve profiles. Reference: `win-nvidia`, `proxmox-passthrough`. Supported: `linux-nvidia`,
`no-gpu`, `two-machines`, `headless`, `baremetal`, `headend-only`. Experimental: `win-home`, `virtualbox`, `vmware`,
`esxi`. The verdict: candidates are the profiles whose `match` holds; a hardware requirement missing rules a
profile out; the rest are ordered single machine first: every possible profile with `machines` 1,
whatever its fit, before every alternative with `machines` 2; within each group by fit first, then `rank`, then
fewer missing requirements, then file order. So an alternative is the best only when no one-machine profile can
run on that hardware. The `ranking` member is the normative text; C10 shows the output.

Changed (2026-11-14, end of stage 1): matrix 0.3.0. The member `machines` and the single-machine-first order
(before, a ready `two-machines` or `headend-only` came first on almost any machine whose
one-machine profile needed a change); tier `0` text; the schema compared case-sensitively by the `.ps1` too.
Retest: `tests/inventory/run_tests.py` (fixtures worked again by hand; new alternatives and schema cases).

Changed (2026-11-14, later): matrix 0.3.1, wording only. The text members `gpu_notes`, `sizing.source`,
`disk_gb.note` and `ranking` stand on their own. The rules and every verdict are unchanged.

## Changing it

Every edit raises `matrix_version`. A new profile is a new entry that uses the same few adapter steps; it needs
no new logic. A status changes only when a recorded run says so. The inventory test suite (`tests/inventory`)
runs on every edit.

## Open points

- OPEN: the form of a step (adapter, verb, checks before and after, human moment, safe to repeat) and of
  `bench_reference`. Decides: the builder in stage 9; this contract then goes to version 2.
- OPEN: the numbering rule for `matrix_version`. Decides: the builder.
- OPEN: `ports` lists 7452 for the relay, which is production's number; the distribution gets its own
  relay port (C8). Decides: the builder in stage 8; the matrix and the inventory's port check change together.
- The PDF converter is an opt-in conversion-job container with `network_mode: none`; it exposes no service port, so the matrix needs no converter listener row.
- OPEN: the `disk_gb` figures are placeholders until stages 4 to 6 measure the discs. Decides: the builder.

Sources: `install/matrix.json` 0.3.1, `install/deep32-inventory.ps1` and `install/deep32-inventory.sh` (derived facts), `src/gpu-pack/containers/{compose.yaml,convert-job.sh}` and `src/gpu-pack/forms/forge_docker_role.py` (converter job), as read 2026-11-17; C10 (the verdict).
