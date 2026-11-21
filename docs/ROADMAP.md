# Roadmap

What I intend to do with Deep32 through November 2027, and what Deep32 will not do. It is for network and security
engineers deciding whether to run Deep32, build on it or contribute to it.

<sub>Applies to 0.1.0-alpha.2 · [1 Reading this page](#1-reading-this-page) · [2 Before alpha.3](#2-before-010-alpha3) · [3 0.1.0-alpha.3](#3-010-alpha3) · [4 The next 12 months](#4-the-next-12-months) · [5 Project practices](#5-project-practices) · [6 Under consideration](#6-under-consideration) · [7 What Deep32 will not do](#7-what-deep32-will-not-do)</sub>

## 1. Reading this page

This is my plan as of 19 November 2026. Everything on it is something I intend to do; none of it is a promise of a
feature or a date. I'll update this page with each release, and the [release notes](../CHANGELOG.md) record what
actually shipped.

The page has three horizons. Section 3 lists what I plan for 0.1.0-alpha.3, the next release with new images, item by
item, so you can hold its release notes against it. Section 4 gives the direction through November 2027, in no
fixed order. Section 7 says what Deep32 will not do, each point tied to a recorded design decision. Section 2 covers
what comes before 0.1.0-alpha.3, section 5 the engineering practices behind several of the items, and section 6 ideas
I haven't decided on.

Releases go from the 0.1.0 alphas to a beta, then release candidates and 1.0.0
([DR-88](DECISIONS.md#dr-88-alpha-versions-first)). The beta waits for the answer-level evaluation in
[section 4.5](#45-evaluation-supply-chain-and-the-project)
([DR-104](DECISIONS.md#dr-104-answer-level-evaluation-is-designed-and-not-yet-run)), and I don't know yet whether it
comes before November 2027.

The code the images are built from changes only with a release that rebuilds them
([CONTRIBUTING.md 4.3](../CONTRIBUTING.md#43-what-changes-only-with-a-release)). That decides most of the grouping
below: fixes to that code wait for new images, while the tests, CI, the setup scripts, the agent adapters, the Ask
relay and the documentation can change at any time. Each row links to where the item, or the decision behind it, is
described.

## 2. Before 0.1.0-alpha.3

| Item | What changes | Described in |
|---|---|---|
| Forge acceptance | Full install runs on Linux, in WSL 2 and on Docker Desktop, and measured storage budgets in place of the declared upper bounds | [Overview 11.1](../README.md#111-roadmap); [D32-TR-1](tr/README.md), Implementation Status |
| OpenSSF Best Practices | The passing badge for [project 16932](https://www.bestpractices.dev/projects/16932). My answers for this release meet every passing criterion marked MUST | [Section 5](#5-project-practices) |

Forge acceptance comes in a signed revision of 0.1.0-alpha.2, which keeps the version number.

## 3. 0.1.0-alpha.3

The next release with new Workstation and Forge images.

### 3.1 New for people running Deep32

| Item | What changes | Described in |
|---|---|---|
| An HTTPS API beside MCP | The gateway's eleven tools over HTTPS with JSON, described by an OpenAPI document, with the same tokens and the same limits as MCP, so scripts, dashboards and other programs can call them without an MCP client. A small client library may follow | Not designed yet; the tools are in [DR-73](DECISIONS.md#dr-73-mcp-with-eleven-read-only-tools) |
| Headend pairing on the console | The Workstation can show a headend's pairing code on its console status line, its serial line and First Start, the way the Forge shows its own pairing code on its console, so a headend can pair without the full-screen pairing window | [Installation 8](../INSTALL.md#8-pair-the-parts); [Agent adapters 2](../adapters/README.md#2-connect-an-agent) |
| `deep32 doctor` and `deep32 selftest` | doctor reads the install record and checks every link of an install; selftest ships with signed reference values and scores a run of its RFC-only question set | [Installation 9](../INSTALL.md#9-verify); [DR-94](DECISIONS.md#dr-94-deep32-doctor-and-deep32-selftest) |
| PDFium in the PDF converter | The converter's own steps move from PyMuPDF to PDFium, which MinerU already uses to render pages, after a page-by-page comparison with today's output. Without PyMuPDF, the converter's code can move from AGPL-3.0-or-later to Apache-2.0 | [Section 5.5](#55-license-metadata) |
| A setup wizard on the Workstation | First Start walks through the Tailscale sign-in, the disc 2 import, a Publish button that shows the certificate-log notice, and a Pair a PC step that shows the code, so no terminal command is needed | This page |
| One installer on the PC | One command or download runs Connect and sets up the Ask relay together: it asks for the code once and handles the Linux linger setting, the Tailscale operator setting and the relay service | This page |
| Relay pairing without service steps | The relay pauses its own service while pairing and starts it again afterwards; the ten-word comparison stays on the Workstation's text console | This page |
| Reinstall-aware pairing | When a reinstalled Workstation pairs with a relay that still knows the old one, the relay offers to forget the old Workstation | This page |

### 3.2 Fixes

| Item | What changes | Described in |
|---|---|---|
| Docker's containerd image store | The Forge's images load into it as well as into the classic store | [Overview 11.1](../README.md#111-roadmap); [Hardware 5](HARDWARE.md#5-graphics-card) |
| Pairing on appliance hosts | The Forge's pairing helper runs with the installer's own Python, so hosts such as Unraid can pair | [Hardware 5](HARDWARE.md#5-graphics-card) |
| Revoking a Workstation | Revoking a Workstation on the Forge no longer leaves the Forge unable to restart | [Installation 10](../INSTALL.md#10-afterwards) |
| Optional ingestion | A Forge that never converts a PDF no longer needs the 15.3 GB ingestion image | [DR-106](DECISIONS.md#dr-106-in-alpha2-a-forge-installs-the-ingestion-add-on-too) |
| Boot and first-start messages | `kiwix_serve` no longer shows as failed at boot before the content image is imported, and the terminal hints of `d32-link` and the gateway's publication tool give full command paths | [Installation 6](../INSTALL.md#6-install-the-workstation) |
| Python 3.12 and 3.13 | The one release-tools test the runner leaves out below Python 3.14 runs again | [Building 2.4](BUILDING.md#24-results-for-010-alpha2) |

### 3.3 Security, supply chain and the project

| Item | What changes | Described in |
|---|---|---|
| Dependency updates | Pins in the images move to fixed versions: opentelemetry-api 1.45.2 and certifi 2026.10.19 or later on the Workstation, and aiohttp, certifi, filelock and setuptools in the index builder. sentence-transformers, transformers and ujson wait for a pylate release that allows newer versions, and vLLM for a MinerU release that allows 0.32.0 | [Known vulnerabilities](security/README.md); [section 5.3](#53-static-analysis-coding-standards-and-dependencies) |
| The engine token in its own file | The token between the Workstation's gateway and engine moves out of their root-only environment files into a protected file of its own, so it can be replaced like every other credential | [Assurance case 9.2](ASSURANCE-CASE.md#92-criteria) |
| Analysis findings | Fixes for the five low-severity defects the fuzz targets found in the Library's JSON, WAVE and RFC text readers, and for the four findings worth a change in the recorded Ruff and Bandit run | [Fuzzing 5](../tests/fuzz/README.md#5-findings); [Building 3](BUILDING.md#3-lint-and-static-analysis) |
| Coding standards | A named coding standard for each language, each checked by a linter in CI, with the existing ShellCheck, PSScriptAnalyzer and wider Ruff findings fixed in the image source | [Section 5.3](#53-static-analysis-coding-standards-and-dependencies) |
| C builds | The standard `CC`, `CFLAGS`, `CXXFLAGS` and `LDFLAGS` variables in every C build, a non-recursive build for the programs in `src/workstation/ship/u10/src`, warnings as errors for every C program, and stripping that can be turned off | [Section 5.3](#53-static-analysis-coding-standards-and-dependencies) |
| The Workstation's C tests | The Workstation's C unit tests published under `tests/` with a recipe for an OpenBSD build host, and the portable ones run under the sanitizers on Linux | [Section 5.1](#51-tests-and-coverage) |
| Each image built twice | Every 0.1.0-alpha.3 image built twice from the same commit, with the byte-for-byte comparison in the release notes | [Section 5.4](#54-builds) |
| License headers | SPDX copyright and license lines in the image source, the license texts moved to `LICENSES/`, and `reuse lint` in CI | [Section 5.5](#55-license-metadata) |
| Artwork and sounds | Deep32's own artwork and sounds are under CC BY-NC 4.0. I'll decide whether they move to CC BY 4.0, the documentation's license | [LICENSING.md](../licenses/LICENSING.md) |
| Pull requests | From 0.1.0-alpha.3 on, changes reach the main branch through pull requests | [GOVERNANCE.md](../GOVERNANCE.md#reviews) |

## 4. The next 12 months

These follow 0.1.0-alpha.3. They set the direction; their order isn't fixed, and some may reach past November 2027.

### 4.1 Knowledge and lookups

| Item | What I intend | Background |
|---|---|---|
| More vendor documentation | The Cisco collection covers IOS XE, SD-WAN, Catalyst Center and ISE in depth, and NX-OS, the Catalyst 9800 wireless controllers, the ASA and Secure Firewall Threat Defense through a few documents each. I intend to add more of those four and Meraki, and other vendors where the publisher's terms allow the documents to ship on the content image | [DR-17](DECISIONS.md#dr-17-the-public-corpus-holds-four-collections); [DR-18](DECISIONS.md#dr-18-vendor-documentation-ships-credited-to-its-owner) |
| Content updates without reinstalling | New RFCs and errata, documentation for new software releases and corrections, as signed content packs that an installed Workstation imports beside its current generation and switches to after the same validation, keeping the previous generation for rollback | [DR-41](DECISIONS.md#dr-41-immutable-index-generations); [DR-56](DECISIONS.md#dr-56-signed-updates-and-no-outside-contact-by-default) |
| YANG model lookup | A lookup table of YANG modules, their revisions and data-node paths, built from the published models where their licenses allow, so the agent can give the exact NETCONF or RESTCONF path for a feature on a given release, cited like the CLI lookups | [DR-16](DECISIONS.md#dr-16-lookup-tables-beside-ranked-search) |
| Your own documents | Steps to build a generation from your own documents on the Forge and switch the Workstation to it | [Overview 13](../README.md#13-questions) |

### 4.2 Working with Deep32

| Item | What I intend | Background |
|---|---|---|
| Configuration review | A skill that reviews a device configuration against the vendor's hardening guidance in the corpus, with every finding citing the passage it rests on and each quotation checked. The configuration goes to whatever model your agent runs on; with a locally served model (below) it stays on your machines | [DR-37](DECISIONS.md#dr-37-quotations-checked-by-string-match); [DR-40](DECISIONS.md#dr-40-a-fixed-answer-protocol-shipped-as-a-skill) |
| Cisco Modeling Labs topologies | A topology file for Cisco Modeling Labs made from an answer's example configuration, which you import into your own lab to try it. Deep32 writes the file and needs no access to the lab | Not designed yet |
| Local models as the agent | An OpenAI-compatible adapter for agents on locally served models, with the same tools | [DR-77](DECISIONS.md#dr-77-claude-code-and-codex-first) |

### 4.3 Installing and upgrading

| Item | What I intend | Background |
|---|---|---|
| In-place upgrades | A Workstation that moves to a new release with a signed update set and keeps its content, keys and pairings, so the Forge, the agents and the Ask relay don't pair again; and the Forge's `upgrade` action tested from one release to the next. Today a new Workstation release is a fresh install | [Upgrading 2](UPGRADING.md#2-what-each-part-keeps); [DR-56](DECISIONS.md#dr-56-signed-updates-and-no-outside-contact-by-default) |
| Everything in Docker on one Linux host | The engine and gateway in containers on the GPU host, with no Workstation VM (journey J3) | [Installation 1](../INSTALL.md#1-choose-a-layout); [D32-TR-1](tr/README.md), Implementation Status |
| Broader hardware | Forge cards with 8 to 10 GB, once reranking and the converter are measured on them, and ARM64 hosts; every Workstation binary is plain amd64 today | [DR-108](DECISIONS.md#dr-108-the-alpha2-forge-needs-105-gb-of-graphics-memory); [DR-53](DECISIONS.md#dr-53-plain-amd64-for-every-workstation-binary) |
| A bootable GPU installer | Sets up the driver and container runtime on the Forge | [Overview 11.1](../README.md#111-roadmap); [DR-59](DECISIONS.md#dr-59-containers-now-a-bootable-gpu-installer-later) |

### 4.4 Licensing

| Item | What I intend | Background |
|---|---|---|
| Permissively licensed default models | A default embedding model and reranker under a permissive license, with the current Jina models, which are CC BY-NC 4.0, kept as an option. A new embedding model means embedding the whole corpus again, and it becomes the default only if it loses no retrieval quality at any level | [LICENSING.md](../licenses/LICENSING.md); [DR-24](DECISIONS.md#dr-24-embedding-model); [DR-02](DECISIONS.md#dr-02-no-loss-in-retrieval-quality-at-any-level) |
| PDFium in the Library's renderer | The Library's renderer `e26-render` moves from MuPDF to PDFium. With the converter's move in 0.1.0-alpha.3, every part of Deep32's own code could then be under Apache-2.0 | [DR-110](DECISIONS.md#dr-110-apache-20-with-a-notice-file-for-code); [section 5.5](#55-license-metadata) |

### 4.5 Evaluation, supply chain and the project

| Item | What I intend | Background |
|---|---|---|
| Answer-level evaluation and the beta | Answers graded claim by claim, with verification on and off, on held-out questions and negative probes, against pass marks set before any result; then the beta, with a compact guided Workstation layout in place of the alpha's 160 GB | [Overview 8](../README.md#8-evaluation); [DR-104](DECISIONS.md#dr-104-answer-level-evaluation-is-designed-and-not-yet-run); [DR-57](DECISIONS.md#dr-57-guided-install-defaults) |
| Reproducible builds | A public build environment and rebuild guide for each of the three images, so someone else can rebuild one and compare it with the release | [Section 5.4](#54-builds) |
| SBOMs and VEX for whole images | The vulnerability triage extended from the Python pins to every package the images' bills of materials list, including the Debian packages in the Forge's images and the Workstation's OpenBSD packages, with a weekly scan | [Section 5.3](#53-static-analysis-coding-standards-and-dependencies); [Known vulnerabilities 4](security/README.md#4-what-it-does-not-cover) |
| Sigstore signatures | Sigstore signatures beside the OpenPGP signatures on the checksum list, commits and tags, so a release can also be checked against a public transparency log | [Section 5.4](#54-builds); [DR-86](DECISIONS.md#dr-86-openpgp-signatures-on-everything) |
| Test coverage | 80% of the Python statements, then 90% of statements and 80% of branches, with the C, shell and PowerShell code measured too | [Section 5.1](#51-tests-and-coverage) |
| OpenSSF Silver, then Gold | Every Silver criterion, then every Gold criterion I can meet | [Section 5](#5-project-practices) |
| A co-maintainer | Contributors invited through small issues labeled `good first issue`, and later a co-maintainer who reviews at least half of each release's changes | [Section 5.7](#57-people) |

## 5. Project practices

I'm working through the OpenSSF Best Practices criteria for Deep32
([project 16932](https://www.bestpractices.dev/projects/16932)): the passing level first, then every Silver and Gold
criterion I can meet. As of 19 November 2026 my answers for 0.1.0-alpha.2 meet every passing criterion marked MUST,
and 37 of the 55 Silver criteria; the project page shows the level the badge site has awarded. Work outside the image
source can land at any time; the rest waits for 0.1.0-alpha.3 ([section 3](#3-010-alpha3)).

### 5.1 Tests and coverage

Each push and pull request to `main` runs `tests/run_all.py` on Windows with Python 3.12
([Building 2.5](BUILDING.md#25-continuous-integration)). On that runner the four suites that need Gpg4win are skipped,
and below Python 3.14 the runner leaves out one release-tools test that hits a known problem 0.1.0-alpha.3 fixes
([Building 2.4](BUILDING.md#24-results-for-010-alpha2)). On each push to `main` the Coverage workflow also measures
statement and branch coverage of the Python, and `make coverage` does the same on a clone
([Building 2.6](BUILDING.md#26-coverage)).

A coverage measurement on 19 November 2026 (Windows, coverage.py 7.17.3 with branch coverage, every suite the runner
starts plus the engine and gateway suites; the engine suite on CPython 3.12.14, the gateway suite on 3.14.4 and the rest
on 3.15.2) found 54.0% of statements and 46.0% of branches covered across the 487 Python files under `src/`,
`adapters/` and `install/`, tests excluded. 207 of those files weren't run by any suite. The engine's and the gateway's
own suites are in `tests/`; the Workstation's C unit tests aren't in this repository yet.

What I intend:

- Publish the Workstation's C unit tests under `tests/`, with a recipe for an OpenBSD build host
  ([section 3.3](#33-security-supply-chain-and-the-project)).
- Run the suites on Linux as well as on Windows.
- Measure coverage on every push for the C (gcov or llvm-cov), the shell scripts (kcov) and the PowerShell scripts
  (Pester), alongside the Python.
- Raise coverage area by area to 80% of statements, the Silver mark, and then to 90% of statements and 80% of
  branches, the Gold mark. The next step is to run the existing importer tests over the importer's identical copies,
  which by my estimate brings statements to about 60%. This is the largest piece of work on this page, and it will
  take months.

### 5.2 Dynamic analysis

The Workstation's programs include about 91,000 lines of C under `src/workstation`, and parts of that code read
documents, media and network input. OpenBSD has neither AddressSanitizer nor libFuzzer, so the fuzzing runs on Linux.
[`tests/fuzz/`](../tests/fuzz/README.md) has 17 libFuzzer targets, built with AddressSanitizer and
UndefinedBehaviorSanitizer, for the vector kernel, the Library's JSON, base64, text, image, document and media readers
and its decompressor, the Ask link's framing and message parsing, and the address parser. The Fuzz workflow runs each
target for two minutes on every push and pull request to `main` and for thirty minutes every week. A 15-minute run of
every target on 19 November 2026 found five low-severity defects, which 0.1.0-alpha.3 fixes
([section 3.3](#33-security-supply-chain-and-the-project)). What I intend:

- A fuzzing run before each release, recorded in the release notes.
- The Workstation's C unit tests built with the sanitizers on Linux, once they are public (section 5.1).
- Fuzz targets for the parts of the C that read input and have none yet: the Library's interface and renderer, and
  the Ask link's TLS and process code.
- Atheris harnesses for the Python parsers: the pairing wire format, the contract validator and the knowledge-base
  bundle checks.

Fixes in the image source wait for the next release that rebuilds the images.

### 5.3 Static analysis, coding standards and dependencies

Ruff and Bandit run on each push and pull request to `main`, and CodeQL analyzes the Python and C code
([Building 3](BUILDING.md#3-lint-and-static-analysis)). OSV-Scanner checks every pinned Python package against the OSV
database on the same events and once a week ([.github/workflows/osv-scanner.yml](../.github/workflows/osv-scanner.yml)),
and Dependabot keeps the GitHub Actions current ([.github/dependabot.yml](../.github/dependabot.yml)). On
19 November 2026 OSV-Scanner 2.7.1 reported 74 advisories against the pins; the release's
[OpenVEX document](security/README.md) records, advisory by advisory, why none of them affects Deep32 as built.

What I intend:

- A named coding standard for each language (Python, C, shell, PowerShell and the JavaScript build tools) in
  [CONTRIBUTING.md](../CONTRIBUTING.md), each checked by a linter in CI: in full outside the image source as soon as
  it runs, and in the image source once 0.1.0-alpha.3 fixes the existing findings.
- The C build changes listed in [section 3.3](#33-security-supply-chain-and-the-project).
- The pins the images are built from moved to fixed versions when the images are rebuilt, and a new OpenVEX document
  for each release.
- A weekly scan of the images' software bills of materials, which also list the Debian and OpenBSD packages.
  OpenBSD packages have no OSV feed, so I'll check them against OpenBSD's errata by hand.

### 5.4 Builds

Each image has a software bill of materials (SPDX 2.3) beside it and a provenance record that names the source
revision it was built from ([DR-95](DECISIONS.md#dr-95-software-bills-of-materials-beside-the-images)). The GPU pack's
producer builds its shared core twice and stops unless both builds are byte-identical. There is no guide yet for
building the images on your own machine ([Building 4](BUILDING.md#4-building-the-images)). What I intend:

- Build every image twice from the same commit and publish the byte-for-byte comparison in the release notes, from
  0.1.0-alpha.3 on. The design document's section on bills of materials and reproducibility says the release notes
  name exactly the artifacts that were built twice and compared ([D32-TR-1](tr/README.md)).
- Publish the build environments and a rebuild guide for each image, so that someone else can rebuild an image and
  compare it with the release. Where an input can't be published, the guide will say which one and why.
- Sign releases with Sigstore as well as OpenPGP.

### 5.5 License metadata

Deep32's code is under the Apache License 2.0 with a NOTICE file, except the two parts that use MuPDF, the Forge's PDF
converter and the Library's renderer `e26-render`, which are under AGPL-3.0-or-later; the documentation is under
CC BY 4.0 ([DR-110](DECISIONS.md#dr-110-apache-20-with-a-notice-file-for-code)). MinerU, which the converter runs, keeps
its own license, the MinerU Open Source License, which is Apache-2.0 with additional terms.
[`REUSE.toml`](../REUSE.toml) records the copyright holder and license of every file in the REUSE 3.3 format. What I
intend:

- SPDX lines (`SPDX-FileCopyrightText` and `SPDX-License-Identifier`) in every file that takes a comment, a
  `REUSE.toml` for the files that can't, and `reuse lint` in CI.
- Files outside the image source can get them at any time. The image source gets them in 0.1.0-alpha.3, together with
  moving the license texts from `licenses/` to `LICENSES/`, which changes the image build.
- PDFium in place of MuPDF: in the converter for 0.1.0-alpha.3, and in `e26-render` later
  ([section 4.4](#44-licensing)).

### 5.6 Security

The security design is in [Overview 10](../README.md#10-security-considerations), D32-TR-1 section 14 and the
[network contract](contracts/C8-network.md); [SECURITY.md](../SECURITY.md) covers reporting. The
[assurance case](ASSURANCE-CASE.md) argues why the design holds up, and the
[security review of November 2026](SECURITY-REVIEW-2026-11.md) records what I checked against it. What I intend:

- The open items in [section 7 of the review](SECURITY-REVIEW-2026-11.md#7-open-items).
- A review by someone independent of the project. None is arranged yet.

### 5.7 People

Deep32 has one maintainer today, and the co-maintainer role in [GOVERNANCE.md](../GOVERNANCE.md#roles) is empty. I'll
invite contributors and a co-maintainer later, starting with small issues labeled `good first issue`. Three Gold
criteria need other people: a second person who could keep the project going, two significant contributors who aren't
paid by the same organization, and review of at least half of the changes by someone other than their author. Until
then, Gold is out of reach.

## 6. Under consideration

Ideas I've recorded and not decided on:

- A LAN-only mode without Tailscale, deferred in [DR-66](DECISIONS.md#dr-66-tailscale-with-the-users-own-sign-in).
- A download mirror I control, listed in the signed mirror list beside the Internet Archive
  ([DR-90](DECISIONS.md#dr-90-whole-images-on-a-permanent-host)). The Archive's pages don't send every security header
  a hardened download site should, and I can't change that.
- Native daemons that replace the Workstation's Python engine and gateway one at a time, each behind a byte-for-byte
  comparison ([DR-52](DECISIONS.md#dr-52-the-production-python-engine-first)).

## 7. What Deep32 will not do

These follow from the design, and I don't intend to change them.

- **Run as a hosted service.** The corpus, the index and the search run on your machines, on your own tailnet, and an
  install contacts nothing of mine ([DR-01](DECISIONS.md#dr-01-a-hosted-agent-working-from-local-retrieval),
  [DR-66](DECISIONS.md#dr-66-tailscale-with-the-users-own-sign-in),
  [Optional features 4](OPTIONAL-FEATURES.md#4-network-egress)). The language model your agent uses is the only hosted
  part.
- **Write the answers.** The eleven tools search, read, look up and verify, and none of them writes an answer
  ([DR-73](DECISIONS.md#dr-73-mcp-with-eleven-read-only-tools)). The agent writes the answer from what it retrieves,
  and Deep32 puts no language model of its own between the question and the search
  ([DR-01](DECISIONS.md#dr-01-a-hosted-agent-working-from-local-retrieval)). Once the adapter for local models in
  [section 4.2](#42-working-with-deep32) exists, a locally served model can be the agent, with the same tools.
- **Reach the web from a research session.** The research skill has no web access, and an Ask session can only search
  and read the corpus ([DR-01](DECISIONS.md#dr-01-a-hosted-agent-working-from-local-retrieval),
  [DR-76](DECISIONS.md#dr-76-an-ask-session-can-only-search)).
- **Accept administration over the network.** Remote access can only query, and operations go through a separate
  account with a fixed set of commands ([DR-54](DECISIONS.md#dr-54-remote-access-may-only-query),
  [DR-72](DECISIONS.md#dr-72-management-kept-off-the-data-plane)). The one exception is the Workstation's maintenance
  SSH, which stays off unless you supply a key for it at install
  ([assurance case 3.3](ASSURANCE-CASE.md#33-entry-points-and-threats), E8).
- **Turn on an optional feature for you.** Features that contact anything ship switched off, with no address, key or
  account filled in, and none of them contacts me ([DR-96](DECISIONS.md#dr-96-optional-features-ship-off),
  [Optional features 1](OPTIONAL-FEATURES.md#1-defaults)).
- **Ship anything unsigned.** Commits, tags and release checksum lists are signed, and the Workstation takes only
  signed update sets ([DR-86](DECISIONS.md#dr-86-openpgp-signatures-on-everything),
  [DR-56](DECISIONS.md#dr-56-signed-updates-and-no-outside-contact-by-default)).
- **Carry my settings or change yours.** The images carry none of my private keys, logins or network settings (the
  public keys on them only check signatures), and the installers never change your tailnet's settings
  ([Overview 11](../README.md#11-release-and-verification),
  [DR-66](DECISIONS.md#dr-66-tailscale-with-the-users-own-sign-in)).
- **Trade retrieval quality for speed.** A change that loses retrieval quality at any level doesn't ship, and a change
  made only for speed has to leave the rankings identical
  ([DR-02](DECISIONS.md#dr-02-no-loss-in-retrieval-quality-at-any-level),
  [DR-06](DECISIONS.md#dr-06-speed-only-changes-must-leave-rankings-identical)).
- **Put evaluation data in the corpus.** Question sets, runs and answer records are never ingested
  ([DR-103](DECISIONS.md#dr-103-evaluation-data-never-enters-the-corpus)).

Not planned for the next year: translations of the interface and the documentation. The corpus is in English.

---

<p align="center"><sub><a href="../README.md">Overview</a> · <a href="../INSTALL.md">Installation</a> · <a href="HARDWARE.md">Hardware</a> · <a href="OPTIONAL-FEATURES.md">Optional features</a> · <a href="tr/README.md">Design</a> · <a href="DECISIONS.md">Decision record</a> · <a href="contracts/README.md">Contracts</a> · <a href="../adapters/README.md">Agent adapters</a> · <a href="../SECURITY.md">Security</a> · <a href="../CHANGELOG.md">Release notes</a></sub></p>
