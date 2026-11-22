<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/figures/head-changelog-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="docs/figures/head-changelog-light.svg">
  <img alt="Deep32 Release Notes, D32-RN: what changed in each release." src="docs/figures/head-changelog-light.svg" width="100%">
</picture>
<br>

</div>

Releases, newest first. Versions follow [Semantic Versioning](https://semver.org), and each release is signed and
archived on Zenodo under [10.5281/zenodo.24272647](https://doi.org/10.5281/zenodo.24272647).

## 0.1.0-alpha.3 · 22 November 2026

After alpha.2 I audited where answer quality gets lost between the index and the agent's answer, and this release is
the quick fixes from that audit, plus the fixes the roadmap listed for it. The Workstation and the Forge have new
images. Disc 2 is alpha.2's content image, unchanged, and an alpha.3 Workstation imports it. The new features the
roadmap had listed for alpha.3 moved to alpha.4, and so did some fixes I promised in alpha.2's notes; the last group
below lists each one.

**Added**

- `lookup_id` takes a `kind`. With `feature` it answers which releases introduced or changed a feature, from the 2,578
  feature-history entries the build has always made and no tool read until now. The kinds `history`, `default` and
  `deprecation` are there as well, and answer once a disc 2 is built with their tables.
- `verify_quote` checks up to 16 quotations in one call.
- `get_section` prints the section's citation and release, can open at a hit's passage (`around`), and names the same
  section in the other releases' books. `list_documents` filters by key prefix, release and platform, and every hit
  carries its document's platform.
- A settings overlay on disc 1 (`/usr/local/d32/etc/engine-overlay`, named by `RAG_CONFIG_OVERLAY_DIR`). The engine
  merges it key by key over the settings of the index generation it serves, so a release can change how a Workstation
  serves without a new disc 2. This release's changes to search and the quotation check below come in that way.
- A script that checks a finished Ask answer against the tool calls of its turn, with no model:
  `adapters/relay/deep32_relay/answer_check.py`. It looks at the quotations against their `verify_quote` results, the
  cited RFCs against their status lookups, a "not covered" against an outline read, a degraded search against the
  answer's first lines, and numbers, releases and commands against the text the tools returned. Everything it can't
  confirm is listed as "to check"; it never edits or blocks an answer. With `DEEP32_RELAY_CHECKS=footer` the relay
  adds its result under each answer as a Checks section. That ships off. On the 12 answers graded in my November
  pilot it flagged 8 of the 19 unsupported claims, with 23 false alarms, and it can't see a wrong recommendation or
  inference at all. You can also run it from the command line on a saved answer.
- Relay settings for a fixed model and a record of each answer. `DEEP32_RELAY_MODEL` and `DEEP32_RELAY_EFFORT` say
  what Ask's default choice runs, and the relay refuses a turn whose agent reports another model.
  `DEEP32_RELAY_ANSWER_RECORD=on` writes one small file per answer with the agent, the model, the hash of the
  instruction it was given, the index generation and the ids each tool returned. It holds no question, answer,
  passage or quotation. Both are off when absent ([Agent adapters 5](adapters/README.md#5-the-ask-relay)).
- The Forge's builder compares a new generation with an earlier one you name (`--baseline-root`) when you supply a
  labeled question set. It stops before export on a net loss at hit@1 or hit@8, or delivery@8 more than 0.005 lower.
- A Workstation can check its Forge's models with known inputs even when the reported revision matches
  (`GPU_EXT_PROBE_ALWAYS`). With it on, long inputs (a 15-passage rerank call and a 7,089-character document for the
  embedder) run when the link starts and whenever the Forge reports another model, revision or card. It ships off
  until the long inputs' reference values are recorded on both card generations.

**Changed**

- The top 8 hits carry up to 3,200 characters each, where they carried 2,400, whenever the request asks for 2,400 or
  more. The hits and their order are chosen as before; only the delivered text is wider. I measured it by replaying
  recorded runs of my production setup: with identical hit lists, delivery@8 went from 0.9092 to 0.9566, 142
  questions got more of their labeled answer text and none got less, for about 21% more text per hit.
- A hit whose window starts inside a table carries the table's caption and header row, and a passage that merges
  several sections lists their titles.
- When a search names no release, the copy from the pinned releases is the one shown among identical copies. Ranks and
  text stay the same.
- `verify_quote` accepts a quotation with "..." only when all its parts lie in one section and no two neighboring
  parts are more than 300 characters of source text apart; otherwise it answers `not_found` with the reason
  `parts-too-far`. Each result also says whether the match lies in the section the agent cited, and where it was found
  when it doesn't.
- `lookup_rfc` follows the obsoleted-by chain to its end and gives the date.
- `degraded` also names a backend that is switched off in the settings, so a keyword-only or unreranked answer never
  reads as complete. In `search_many` a failed reranker call now costs only its own query's scores, and every hit says
  whether the reranker scored it.
- The research skill, which Connect installs and the Ask relay carries: a quotation is one unbroken run of the source
  text, every search asks for the pinned releases' copies, an answer whose main point the corpus doesn't cover is short
  and says so, each depth has one budget and one stop rule, an answer from a search with a backend down is marked
  degraded, and an RFC's obsoleted-by chain is followed to its end. On an alpha.2 Workstation the skill still works;
  the parts that need this release's tools say what to do when a reply lacks them.
- Ask gives each agent its own skill, with the rules every default answer follows first and the exam blueprint last,
  and nothing left out. Codex now gets its own skill once per conversation; before, it got the Claude Code skill twice
  in every turn.
- An Ask conversation keeps its context for 15 minutes between questions, where it was 60 seconds (still 60 while
  another conversation waits for the relay), and the agent sees the earlier questions and answers marked as such.
- `deep32 selftest` compares a labeled span with a hit the way `verify_quote` does. Before, 104 of its 107 spans could
  never match an RFC's wrapped lines. It still has no signed reference values, so it can't score a run yet.
- The Linux getter installs a Forge with the core and the card's embedder, about 21.7 to 21.9 GB, and adds the
  15.3 GB ingestion image only with `--ingestion`. The Windows getter still includes it.
- On Linux, the Forge's images load into Docker Engine's default containerd image store as well as the classic store.
  Docker Desktop still needs the classic store.

**Fixed**

- The Forge's pairing helper runs on the installer's own Python, so a host whose system Python is missing or old, such
  as Unraid, can pair.
- Revoking a Workstation on the Forge with `deep32-pair unpair` no longer leaves the Forge unable to restart.
- The VM commands that `deep32-get --prepare-vm` writes make a 200 GB disk.
- The installer's second space check counts the joined copy of the images that is already on disk, so the joined copy
  no longer has to be free on top of the budget.
- A Forge install that is missing settings lists all of them at once, before it reads the media.
- The Forge's prerequisite check confirms that containerd is running.
- `deep32-setup.sh forge` uses the one-host KVM layout's journey (J4) when the Workstation VM is on the same host.
- `deep32-setup.sh` no longer counts a Tailscale sign-in it has just completed as a problem.
- The terminal hints of `d32-link` and the gateway's publication tool give full command paths.
- `d32upd` names a disc 2 download that holds its lock, instead of "another d32upd is running (pid 1)".
- The Library's progress line in First Start labels its two measures.
- Two agent messages in one Ask answer no longer run together; each starts a new paragraph.
- An Ask answer no longer fails with `native-output-limit` on text the agent wrote that never reaches the window, and
  a tool call over the turn's limits gets a result that says what to do.
- The Workstation image's `/etc/rc.local` comes from the published source.
- The release-tools test that was left out below Python 3.14 runs again, and a test imports every diagnostics module.

**Security**

- The token between the Workstation's gateway and engine moved out of their environment files into a protected file
  for each service, so it can be replaced like every other credential.
- The five low-severity defects the fuzz targets found in the Library's JSON, WAVE and RFC text readers are fixed
  ([fuzzing page, section 5](tests/fuzz/README.md#5-findings)), and so are three of the four Ruff and Bandit findings
  worth a change. The fourth is in the PDF converter, whose image isn't rebuilt in this release.
- Pins moved to fixed versions: opentelemetry-api 1.45.2 and certifi 2026.10.23 on the Workstation, and aiohttp
  3.14.5, certifi 2026.10.23 and filelock 4.2.10 in the index builder. setuptools stays at 81.0.0 in the builder,
  because torch 2.11.0 requires a version below 82. The [OpenVEX document](docs/security/README.md) covers this
  release's pins.

**Moving from 0.1.0-alpha.2**

Both the Workstation and the Forge have new images, so follow Procedure 1 in [Upgrading](docs/UPGRADING.md#33-when-the-workstation-changes):
check out `v0.1.0-alpha.3` on each machine, install the Forge again into an empty data folder, install the new
Workstation from disc 1 and import disc 2 (the alpha.2 content image you already have works), pair the Forge, publish
the gateway, connect each agent into a new profile, and install the Ask relay again under a new install ID. Connect
installs this release's research skill, and the relay carries its own copy of the skill, so reinstalling both is also
how they get this release's skill.

**Known issues**

- The guided installer prints a status line between asking a question and asking it again. A key typed in that
  moment can be lost, and in my lab runs the installer twice stopped reading keys after it. If it stops responding,
  restart the VM: the installer starts over. Answer each question once it is the last line on the screen.
- The disc 2 import still copies the Library into the Library partition before it installs it, so the Workstation
  needs a disk of 200 GB or more. On a smaller disk the knowledge base still goes into use, and the Library stops with
  IMPORT_SPACE before any of it is copied.
- The GPU pack's images are still stored uncompressed, so a Forge with ingestion downloads about 37 GB instead of about
  26.5 GB, and the installer's space check still reserves room for unpacking at a factor set for compressed archives.
- At boot the Workstation lists `kiwix_serve(failed)` among its services until disc 2 is imported.
- Behind a router whose DNS strips DNSSEC signatures from some answers, the Workstation's validating resolver can
  refuse the name that Let's Encrypt's service resolves to, and publishing the gateway fails with exit 14.
- In my alpha.3 lab run, publishing the gateway while the disc 2 import was still installing the Library stopped twice
  with exit 14 (`gateway-unverified`), although the gateway answered correctly when I checked it by hand. Once the
  import had finished, `/usr/local/d32/sbin/d32-publish resume` completed. Publish after the import, or resume then.
- Ask lists each citation as "not in the Library" and shows no Library links. The Library itself works; Ask, which runs as the desktop user, can't read which knowledge base is in use, so it never binds the Library's citation maps. This was already so in 0.1.0-alpha.2. Fixing it is the first item for 0.1.0-alpha.4.
- In WSL, `sudo` drops `/usr/lib/wsl/lib`, where WSL keeps `nvidia-smi`, so a Forge install started with plain `sudo
  bash` fails its GPU checks with exit 10. Start it with `sudo env "PATH=$PATH" bash ...` as
  [INSTALL.md](INSTALL.md) shows.
- A Forge in a WSL 2 distro shares the network stack of every other distro in that WSL VM. If another Docker engine
  there sets its `FORWARD` policy to `DROP`, traffic between the Forge's containers is dropped and its door reports
  unhealthy. Run one Docker engine per WSL VM, or add `DOCKER-USER` accept rules for the Forge's bridges.
- A Linux host whose containerd root was moved in `/etc/containerd/config.toml` is measured on the wrong filesystem by
  the space check. Docker Desktop's containerd store isn't supported yet.
- The Ask window has no password of its own. In it the relay on the paired PC picks the engine, so the window can't
  switch the engine for each conversation or sign in to Codex. [SECURITY.md section 5](.github/SECURITY.md#5-known-limitations)
  says who can use Ask.
- Stop the Ask relay service before pairing (`systemctl --user stop 'deep32-relay-<id>'` on Linux), then start it
  again afterwards. Publish the gateway before pairing the relay. Unpublishing it stops Ask until you publish again.
  To pair a different relay, remove the old one first (`/usr/local/d32/sbin/d32-chatctl unpair work`).
- If the Forge's five-minute pairing window closes before you pair, its services keep running but it records itself
  as stopped; start it again to open a new window.
- `deep32 doctor` doesn't read the install record, so it always reports the installation check as failed, and
  `deep32 selftest` has no signed reference values. `deep32-setup.sh` checks an install meanwhile.
- The Workstation offers only machines whose Tailscale name starts with `deep32-` and that are in its own tailnet, for
  both the Forge and the Ask relay.
- Adding ingestion to a Forge installed without it means installing again into a new data folder with `--ingestion`.
- The repository doesn't pass the REUSE check yet: the license texts are still in `licenses/`.
- Deep32 has one maintainer, and I review every change myself.

**Moved to 0.1.0-alpha.4**

These were promised for alpha.3, in alpha.2's notes or on the roadmap. I kept this release to fixes I could make and
test quickly, and each of these needs more than that.

- From alpha.2's notes: staging the Library copy where there is more room, storing the GPU pack's images compressed
  with the space factor corrected, a working `deep32 doctor` and `deep32 selftest`, the license texts moved to
  `LICENSES/` for the REUSE check, and changes reaching the main branch through pull requests.
- From the roadmap's alpha.3 section: the HTTPS API beside MCP, headend pairing on the console, PDFium in the PDF
  converter, a setup wizard on the Workstation, one installer on the PC, relay pairing without service steps,
  reinstall-aware pairing, exact model choice in Ask, the `kiwix_serve` boot message, coding standards checked in CI,
  the C build changes, the Workstation's C tests, every image built twice, license headers, and the decision on the
  artwork's license.
- The PDF converter's NOTICE file and its fix for invisible characters, which need the converter image rebuilt.

## 0.1.0-alpha.2 · 20 November 2026

The first release whose Workstation installs from its image and joins your own tailnet, and whose parts pair with each
other there by short code. Each machine also has a one-command setup script, which I haven't yet run from start to
finish; INSTALL gives every step by hand as well.

**Added**

- Three ways to give the Workstation disc 2. Besides an optical drive (or a VM's virtual CD), a USB stick of 32 GB or
  more with the disc 2 image written to it (balenaEtcher or `dd`), or over the network: in First Start, **Get Disc 2
  over the Network** downloads it from a PC on your tailnet, which sends it with the setup wizard's **Send disc 2** and
  a one-time code, or from the release mirror. The download resumes after a stop, shows its progress, waits on the
  empty system update slot and is deleted after the import. The import itself is unchanged: the signature first, then
  every file against the signed manifest.
- A setup wizard for bash and PowerShell (`install/deep32-wizard.sh`, `install\deep32-wizard.ps1`) that drives the
  setup scripts through the whole install with numbered steps, live progress and every error code explained. Its VM
  guide (`--vm-guide`) walks through the Workstation VM on Proxmox VE (boot-tested), Hyper-V, KVM, VMware Workstation,
  ESXi, VirtualBox or bare metal (all but Proxmox VE not boot-tested yet) and checks each step it can see. `--demo` shows
  it without changing anything.
- Installing with an agent: in a clone, Claude Code's `/deep32-install` or Codex's `$deep32-install` walks you through
  the wizard on each machine and stops at every step that needs you ([AGENTS.md](AGENTS.md)).
- A project site at https://hellobpdu.github.io/deep32/ with a step-by-step general setup and an advanced install guide.
- Ask lets you choose the model and the strength for each conversation. A Claude Code relay offers the models Claude
  Code lists for its profile (default, opus, fable, sonnet and haiku with Claude Code 2.1.326) and the effort levels
  low, medium, high, xhigh and max, with default first; a Codex relay offers its one checked model and effort. An
  answer has no time limit of its own; it runs until the agent is done, within the relay session's one hour. The
  limit was 170 seconds, and a researched question at xhigh ran past it. The relay answers one question at a time
  ([adapters/README.md section 5](adapters/README.md#5-the-ask-relay)).
- Ask's agent follows the same research skill that Connect installs, inlined because it can't open files. Before,
  it had a short summary of that skill, and its answers were much thinner than the same question through Connect.
- The research skill has a map of the ENARSI 300-410 and CCIE Enterprise Infrastructure exam topics: each topic
  with its RFCs, Cisco documents and where to look. The agent searches it first to find the governing documents.
  The topic titles are Cisco's published exam topics; the mappings are mine.
- `install/deep32-setup.sh`: one command per machine that checks it, fixes what it safely can, downloads and installs
  its part, and stops only for a sign-in, the installer's plan and the pairing code. AGENTS.md tells Claude Code or
  Codex how to run it for you.
- `install/deep32-setup.ps1`, the same one command for Windows: a Forge on Docker Desktop, a Workstation on Hyper-V,
  or a headend. Both setup scripts check the card's memory and the host's Python before they download anything.
- The Forge installs from the signed disc 3 images. `deep32-get` joins the shared core, your card's embedder and the
  ingestion image into one verified copy, and the installer checks the host before it writes anything: the host
  recipe, the driver and memory floors, the ports, the free space against a signed storage budget, and your tailnet.
- `install/forge-host-python.sh`: checks the Forge host's Python for the pairing helper and installs it where the
  distribution allows.
- Pairing by short code between the parts, and publication of each part's services on your tailnet with routes Deep32
  owns and removes cleanly.
- Configurable service ports, with a check that refuses to take a port or route another service already uses.
- Isolated agent profiles, for a PC that already has Claude Code or Codex configured for other work.
- The Ask relay for Windows, Linux and macOS: questions typed in the Workstation's Ask window are answered by the agent
  on your PC, which can only search the corpus while it does. Pairing and the link work, and on a Linux headend a
  question sent to the relay completes with Claude Code 2.1, tool call and answer included. See the known issues for
  Ask from the Workstation.
- A software bill of materials (SPDX 2.3) for every image.
- The design document [D32-TR-1](docs/tr/README.md) and the [Decision Record](docs/DECISIONS.md).
- The script that builds the Workstation's install medium, `src/workstation/ship/tools/mkimage-ship.sh`, is now part of
  the published source.
- [CONTRIBUTING.md](.github/CONTRIBUTING.md), with a Developer Certificate of Origin sign-off on every commit; a code of conduct
  based on the Contributor Covenant 2.1; and [GOVERNANCE.md](.github/GOVERNANCE.md), with the project's roles, how decisions
  are made and how changes are reviewed.
- Continuous integration on GitHub. The Tests workflow fails on any new Ruff or Bandit finding and runs every test
  suite on Windows with Python 3.12. The Coverage workflow, and `make coverage` on a clone, measure statement and
  branch coverage of the Python. [docs/BUILDING.md](docs/BUILDING.md) says how to build and test locally.
- The existing test suites for the engine, the gateway, the GPU pack kit, the disc 3 pin resolver and `exact_local`,
  now published under `tests/`. With them the suites cover 54.0% of the statements and 46.0% of the branches in the
  487 Python files under `src/`, `adapters/` and `install/`, tests excluded (measured 19 November 2026 on Windows with
  coverage.py 7.17.3, branch coverage on).
- Fuzz targets for the Workstation's C parsers: 17 libFuzzer targets built with AddressSanitizer and
  UndefinedBehaviorSanitizer, run in CI on every push and pull request and for longer once a week
  ([tests/fuzz](tests/fuzz/README.md)).
- `REUSE.toml`, which records the copyright holder and license of every file in the REUSE 3.3 format.
- The [roadmap](docs/ROADMAP.md) through November 2027, and a guide to [upgrading](docs/UPGRADING.md) an install to a
  new release.

**Changed**

- Pairing on the PC shows what you type. At the relay's prompt each of the ten words appears as you type it and is
  checked when you press Space or Enter; a slip is asked for again, and a different pairing word stops the attempt with
  nothing stored. Connect's one-time code stays hidden but shows a dot for each character.
- The Workstation reads disc 2 once, in two parts, and search works long before the Library is in. The import checks
  the signature first. Then it copies and checks only what search needs, the knowledge base and the corpus records
  (about 1.5 GB of the 19.6 GB disc), installs the knowledge base and switches it on by itself: search, Ask,
  publication and pairing work from then on, about 8 minutes after sign-in on my lab VM. The
  Library (the encyclopedia, the Cisco papers, the RFC shelf) follows in the same run in the background, at a lower
  priority, and switches on when all of it has checked out. Each file is checked against the signed manifest
  (size and SHA-256) as it is copied, nothing is used before its own part has checked out, and a stop resumes
  where it stopped. If the Library part fails, the knowledge base stays in use and the Import Report says so
  (`IMPORT_KB_ONLY`). Until now the import read the whole disc twice and nothing worked until all of it was in,
  about 2.5 hours on my lab VM; a complete import now takes about 2 hours there, most of it the Library.
- On Windows without Hyper-V, `install\deep32-setup.ps1 workstation` writes the raw-image VM kit for VirtualBox or
  VMware instead of stopping.
- The GPU pack is one Linux Docker form on every host. On Windows it runs through Docker Desktop or in an Ubuntu 24.04
  WSL 2 distro.
- The GPU pack is a shared core plus one small add-on for your card's generation and an ingestion add-on with the PDF
  converter and index builder. A Forge needs all three, about 37.1 GB: the 18.6 GB core, a 3.1 to 3.3 GB embedder
  and the 15.3 GB ingestion image.
- A Forge needs at least 10.5 GB of graphics memory, an 11 GB card or larger. Smaller cards come back once reranking
  and the converter are measured on them.
- Images download whole from permanent mirrors listed in a signed index; `deep32-get` falls through the mirrors and
  resumes interrupted downloads.
- The Forge installer now accepts three host setups under a dated owner waiver, ahead of their full install runs:
  Ubuntu 24.04 with Docker Engine, on the host itself or in WSL 2, for journeys J2 and J4, and Windows 11 with Docker
  Desktop for journey J1. When a host matches a waived setup, the installer prints a note that says so and names
  the signed file that lists what wasn't run. Every other host check is unchanged.
- The Windows setup is matched by version floors: Windows 11 build 26100 or later in the Home, Pro, Enterprise or
  Education edition, and Docker Desktop 4.99.0 or later with Docker Engine 29.0.0 or later on the WSL 2 backend.
  Windows 10 and Windows Server are refused.
- The GPU pack's storage budgets are declared upper bounds, computed from the image sizes and labeled as declared in
  the installer's plan. I plan to replace them with measured budgets in a signed revision. A Forge with the core, one
  embedder and the ingestion image declares about 244 to 247 GB when its data folder and Docker's storage share one
  Linux filesystem or one Windows volume. The installer checks that budget again after it has joined the downloaded
  images, about 37 GB, so that disk needs about 281 to 284 GB free when the install starts.
- Deep32's code is now under the Apache License 2.0, with a NOTICE file whose attribution goes with every copy and
  every changed version ([DR-110](docs/DECISIONS.md#dr-110-apache-20-with-a-notice-file-for-code)). The two parts that
  use MuPDF, which is AGPL, stay under AGPL-3.0-or-later: the Forge's PDF converter and the Workstation Library's
  renderer, `e26-render`. Documentation stays under CC BY 4.0.
- Every image carries the license texts. The Workstation has LICENSE, NOTICE, the AGPL text and the third-party
  notices in `/usr/local/share/doc/d32`. The content image's `/code` folder holds a README, LICENSE, NOTICE and the
  AGPL text in place of the alpha.1 source archive. The GPU pack ships LICENSE and NOTICE
  with every copy of Deep32's code, and MinerU's license with the converter.
- The PDF converter uses MinerU 4.0.7, which is under the MinerU Open Source License: Apache-2.0 with additional
  terms. If you and your affiliates together have more than 100 million monthly active users or more than USD 20
  million in total monthly revenue, commercial use needs a separate commercial license from the MinerU team, and an
  online service for third parties built on MinerU must say that it uses MinerU. The license text is in
  `src/gpu-pack/licenses/`.
- The content image is rebuilt as 0.1.0-alpha.2 with the same corpus and search index. Deep32's own documents in it
  are labeled Apache-2.0 and CC BY 4.0, and it stays about 19.6 GB. I built it twice from the same staged files and
  the two images were byte-identical. Its software bill of materials lists every file on it.
- Cisco documentation is labeled with its copyright and its source in the content image's corpus and Library records
  and third-party notices, and with its copyright on the Workstation's Cisco papers page. This replaces the alpha.1
  wording about permission and personal use.

**Fixed**

- The guided install's last status line tells you to turn the machine off and remove the install medium. It said the
  machine powers off, but on the Proxmox virtual machines I tested the installer halts and the machine stays on.
- The Ask guide on the Workstation (`/usr/local/share/doc/d32/ASK.txt`) describes this release's Ask: the relay on the
  paired PC picks the engine and offers the models and strengths, `/engine` only sets the label, and the `work` link
  pairs at the text console by comparing ten words.
- The Workstation image now includes the Tailscale package. Without it, the alpha.1 Workstation could not join a
  tailnet.
- At first start the Workstation shows the Tailscale sign-in address on the text console, on the serial port and in
  the First Start window. Tailscale's status output ends without a newline, and the first-start check never saw
  the state Tailscale reported, so no address appeared and disc 2 was never imported by itself.
- After you sign in, the Workstation finds disc 2 in a CD drive and imports it by itself. In a fresh install on a lab
  VM on 19 November 2026, the import started 15 seconds after the Workstation joined the tailnet.
- The Workstation imports all of disc 2. On alpha.1 the import could not finish: its scripts were not executable, the
  image had no release key to check disc 2's signature, and the importer failed on its own read-only disc mount. The
  importer also miscounted the corpus pack, which carries a file of its own named `MANIFEST.tsv`. An alpha.2
  Workstation accepts the unchanged alpha.1 content image.
- The First Start, Import Report and Pairing entries in the desktop's Application Manager open their windows. They
  were installed without the execute permission CDE needs to run an entry.
- The Ask link service no longer prints `d32chat(failed)` at boot before an Ask relay is paired.
- The Ask relay starts after a turn that ended while Claude Code was writing one of its built-in plugin records.
  That left an empty lock folder in the relay's own profile, and the relay took it for a customised profile and
  refused to start (`native-profile-customised`).
- First Start's hint for a switched-off link gives the command with the full path that `doas` accepts:
  `doas /usr/local/d32/sbin/d32-link enable`.
- The Forge's model servers start on an offline host. The alpha.1 GPU pack shipped both model caches without the
  pointer files the loaders look up, so neither the embedding model nor the reranker could load without internet
  access.
- PDF conversion on the Forge works. The alpha.1 converter image lacked the graphics libraries and the Accelerate
  package that MinerU loads, and the installed model folder lacked the empty folder the converter mounts for its lock
  files, so a job stopped before the first page. On 19 November 2026 the alpha.2 ingestion image converted a one-page
  PDF with `--tier standard` on an RTX 2080 Ti in about 2 minutes 22 seconds, with Docker Engine 29.1.3 in an Ubuntu
  24.04 WSL 2 distro. I haven't yet run a conversion with the default `--tier advanced`, in Docker Desktop on Windows,
  or in a full Forge install with pairing.
- Guided install: the root SSH key question no longer fails with `printf: not found`; the root disk question no longer
  offers the install medium as its default; the keyboard question accepts Enter for the default.
- The guided install stops when it finishes. Before, it restarted into the installer. It now halts at
  `Please press any key to reboot.`; turn the machine off there and remove disc 1 before you start it.
- The Workstation's firewall allows the Ask link on the relay's port, 7452.
- The published source now builds the Deep32 Library: a generated document-type table the build needs was missing.
- The files the Workstation image installs from the published `distrib/site` and `ship/site` folders match the
  published source byte for byte, except three. The image's `/etc/rc.local` comes from a build overlay outside the
  published source and doesn't run `d32-publish resume` at boot. From the code, publication survives a restart without
  it, but I haven't tested that yet. The firewall table `/etc/d32/pf/relay_hub` differs in a comment, and on the image
  `/etc/d32/pf/ts_infra` holds the address list that the published copy leaves empty.
- A paired Forge now serves meaning search and reranking. Before, the Workstation couldn't read the Forge's token,
  pairing never opened the firewall to the Forge, and the model check only accepted the exact numbers of one graphics
  card; it now allows the small differences between card generations. The firewall also let no pairing connection
  reach the Forge.
- The Workstation publishes its gateway over HTTPS. Its resolver no longer fails every lookup behind routers that strip
  DNSSEC records, it can reach Let's Encrypt for the certificate, and publication checks the gateway in a way that
  works on OpenBSD.
- Pairing with the Ask relay sets up the Workstation's Ask link: its address, firewall entry, identity and gateway
  token. The relay's Linux service unit loads, and Connect waits a few seconds for the gateway to load a new
  credential instead of failing right after pairing.
- The Ask relay accepts the `rate_limit_event` that Claude Code now reports while it answers. A question used to stop
  with `native-output-failed` right after the agent's first tool call.
- The Ask relay names a signed-out agent. When the agent's sign-in is missing or refused, at start or during a
  question, the relay reports `agent-sign-in-required`; before, it passed Claude Code's "Failed to authenticate" line
  on as the answer. The relay's install check also answers with the server details Claude Code requires, so its live
  self-test can start.
- The Ask relay's service keeps running after you remove and reinstall the relay, or restart it soon after a
  Workstation link closed. The install now starts the service only after it has written everything, and the relay
  waits up to 90 seconds for its port while a just-closed link still holds it. Before, systemd gave up after three
  quick failures, and on Windows the relay stayed down until the next sign-in.
- Ask questions reach the relay. Ask sent an engine name with each new conversation, and the Workstation's chat broker
  refuses that, so every question ended in `Error 1 PROTOCOL`. The relay on the paired PC picks the engine, and
  `--engine` and `/engine` now only set the label in Ask's header. When you don't name a link, Ask uses the
  Workstation's own, `work`; it asked for `home`, which alpha.2 doesn't configure. The Ask window opens straight to the
  question box. Before, it waited at a password prompt that this release's broker doesn't support.
- `deep32 doctor --role headend` loads and runs on Python 3.12 and 3.13. Before, it stopped at an import error on any
  Python older than 3.14.

**Security**

- This release fixes no publicly known vulnerability.
- I checked every advisory OSV-Scanner reports for a package Deep32 pins, 74 advisories in 11 package versions,
  against how Deep32 uses that package. None affects Deep32 as built. The result is an OpenVEX document in
  [docs/security](docs/security/README.md), and the OSV-Scanner workflow applies it on every push and pull request to
  the main branch and once a week. Its exceptions expire on 19 February 2027, so a finding that is still there by then
  shows again. The check doesn't cover the Debian packages inside the GPU pack's images, native libraries inside
  Python wheels or the Workstation's OpenBSD packages; section 4 of that page says more.
- CodeQL analyzes the Python and C code on every push and pull request to the main branch and once a week, OpenSSF
  Scorecard checks the repository's practices once a week, and Dependabot keeps the actions the workflows use up to
  date.
- The [assurance case](docs/ASSURANCE-CASE.md) argues claim by claim why the design meets its security requirements,
  and the record of the [security review of November 2026](docs/SECURITY-REVIEW-2026-11.md) says what I checked and
  found.

**Known issues**

- The guided installer prints a status line between asking a question and asking it again. A key typed in that
  moment can be lost, and in my lab runs the installer twice stopped reading keys after it. If it stops responding,
  restart the VM: the installer starts over. Answer each question once it is the last line on the screen.
- The disc 2 import copies the Library, most of the disc, into the Library partition before it installs it, and
  needs that much plus 5% free there. The guided install sizes that partition from the disk, so the Workstation now
  needs a disk of 200 GB or more. On a smaller disk the knowledge base still goes into use, and the Library stops
  with IMPORT_SPACE before any of it is copied. The VM commands that `deep32-get --prepare-vm` writes still make a
  160 GB disk; change that to 200 GB before you run them. 0.1.0-alpha.3 stages the copy where there is more room and
  fixes the commands.
- The GPU pack's rebuilt images are stored uncompressed, so a Forge downloads about 37 GB instead of about 26.5 GB,
  and because the installer's space check reserves room for unpacking at a factor set for compressed archives, a Forge
  on one disk needs about 281 to 284 GB free at the start. The contents are the same. 0.1.0-alpha.3 stores the images
  compressed again and corrects that factor.
- The installer checks free space twice, before it joins the images and again after it has written the joined copy.
  The second check still asks for the whole budget, including room for the joined copy that is already on disk, so
  the joined copy, about 37 GB, has to be free on top of the budget when the install starts. INSTALL section 5 has the
  figures. I'll correct the second check in 0.1.0-alpha.3.
- The Ask window has no password of its own in this release. In it the relay on the paired PC picks the engine, so the
  window can't switch the engine for each conversation or sign in to Codex. [SECURITY.md section 5](.github/SECURITY.md#5-known-limitations)
  says who can use Ask. The per-window password returns with a later Workstation release.
- Stop the Ask relay service before pairing (`systemctl --user stop 'deep32-relay-<id>'` on Linux), then start it
  again afterwards. Publish the gateway before pairing the relay. Unpublishing it stops Ask until you publish again.
  To pair a different relay, remove the old one first (`/usr/local/d32/sbin/d32-chatctl unpair work`).
- If the Forge's five-minute pairing window closes before you pair, its services keep running but it records itself
  as stopped; start it again to open a new window.
- `deep32 doctor` and `deep32 selftest` are on the images but don't work yet: doctor doesn't read the install record,
  so it always reports the installation check as failed, and selftest has no signed reference values. Both are
  planned for alpha.3. `deep32-setup.sh` checks an install meanwhile.
- The Forge's pairing helper runs on the host's `/usr/bin/python3`, so a host whose system Python is missing, broken or
  older than 3.10 can't pair. Unraid is one. `install/forge-host-python.sh` checks a host and installs Python where
  the distribution allows it.
- Revoking a paired Workstation on the Forge leaves the Forge unable to restart. Until alpha.3, revoke by reinstalling
  the Forge into a new data folder.
- The Workstation offers only machines whose Tailscale name starts with `deep32-` and that are in its own tailnet, for
  both the Forge and the Ask relay. INSTALL and the Agent Adapters page say so.
- The Forge's images load only into Docker's classic image store, and Docker Engine 29 and Docker Desktop default to
  containerd. The installer and the setup scripts stop with how to switch; INSTALL section 5 has the details.
- At boot the Workstation lists `kiwix_serve(failed)` among its services until disc 2 is imported. The encyclopedia
  server has no library to serve before then; the import starts it.
- The Forge install on Linux, in WSL 2 and on Docker Desktop ships under the owner waiver above, before its full
  end-to-end acceptance run, and with declared storage budgets. Its parts were each checked on an RTX 2080 Ti;
  INSTALL section 5 lists what.
- The fuzz targets found five low-severity defects in the Library's JSON, WAVE and RFC text readers. Their fixes are
  planned for alpha.3; section 5 of the [fuzzing page](tests/fuzz/README.md#5-findings) describes each one.
- The repository doesn't pass the REUSE check yet. `REUSE.toml` records every file's license, but the REUSE format
  expects the license texts in `LICENSES/`. Moving them from `licenses/` changes the Workstation image build, so it is
  planned for alpha.3.
- Deep32 has one maintainer, and I review every change myself. From alpha.3 on, changes reach the main branch through
  pull requests, and once a co-maintainer joins, at least half of the changes in each release are reviewed by someone
  other than their author ([GOVERNANCE.md](.github/GOVERNANCE.md#reviews)).
- The archive item's collection is Community Texts; it should be Community Data. Downloads aren't affected.

**Removed**

- The separate WSL 2 form of the GPU pack. Windows hosts run the same Linux Docker form as every other host.
- Split image parts on the GitHub release.

## 0.1.0-alpha.1 · 16 November 2026 · images withdrawn

First public release, with the source under AGPL-3.0 and three signed images. The images downloaded and verified, but
the install did not yet join the Workstation, the GPU pack and the agent end to end, so I withdrew them on
17 November 2026. The source stays archived at [10.5281/zenodo.24272648](https://doi.org/10.5281/zenodo.24272648).

---

<p align="center"><sub><a href="README.md">Overview</a> · <a href="INSTALL.md">Installation</a> · <a href="docs/HARDWARE.md">Hardware</a> · <a href="docs/OPTIONAL-FEATURES.md">Optional features</a> · <a href="docs/tr/README.md">Design</a> · <a href="docs/DECISIONS.md">Decision record</a> · <a href="docs/contracts/README.md">Contracts</a> · <a href="adapters/README.md">Agent adapters</a> · <a href=".github/SECURITY.md">Security</a> · <b>Release notes</b></sub></p>
