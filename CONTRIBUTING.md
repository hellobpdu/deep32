# Contributing to Deep32

How to ask a question, report a bug or a security problem, and send a change. I maintain Deep32 by myself, so it can
take me a few days to answer, but I read everything that comes in.

<sub>Applies to 0.1.0-alpha.2 · [1 Questions](#1-questions) · [2 Bug reports](#2-bug-reports) · [3 Security problems](#3-security-problems) · [4 Changes](#4-proposing-a-change) · [5 Licensing](#5-licensing-of-contributions) · [6 Conduct](#6-conduct)</sub>

## 1. Questions

Ask in [Discussions](https://github.com/hellobpdu/deep32/discussions). That covers help with an install, how a part
works and ideas you want to talk through before they become a proposal. Please write in English.

## 2. Bug reports

Check the known issues in the [release notes](CHANGELOG.md) first, then open an
[issue](https://github.com/hellobpdu/deep32/issues/new/choose) with the bug report form. The form asks for:

- **The release** you installed, or the commit of your clone (`git describe --tags --always`).
- **The layout**: one of the journeys in [INSTALL.md section 1](INSTALL.md#1-choose-a-layout), or a short description
  of yours.
- **Each machine involved**: its operating system and version, the hypervisor for a Workstation VM, and for a Forge the
  graphics card, the NVIDIA driver version and the Docker version.
- **The tagged lines from the setup script.** Run the check on each machine where you used the setup script. It only
  reports and changes nothing:

  ```sh
  bash install/deep32-setup.sh check
  ```

  ```powershell
  powershell -NoProfile -ExecutionPolicy Bypass -File install\deep32-setup.ps1 check
  ```

  Paste the lines that start with a tag: `[ok]`, `[fix]`, `[you]`, `[fail]` or `[info]`.
- **What you did, what happened and what you expected**, with the exact commands.
- **Logs**, cut to the part that shows the problem: for example the end of `/var/log/d32-import.log` on the Workstation,
  or the JSON line the Forge prints for `--status` ([INSTALL.md section 10](INSTALL.md#10-afterwards)).

Remove secrets before you paste anything: tokens, bearer values, pairing codes, passwords, and the names and addresses
in your tailnet. Issues are public.

## 3. Security problems

Please don't open an issue for a vulnerability. [SECURITY.md](SECURITY.md) has the two private ways to report one: the
**Report a vulnerability** button on the repository's Security tab, or an email encrypted to the release key.

## 4. Proposing a change

### 4.1 Before you start

For a small fix, such as a typo, a broken link or a bug with an obvious cause, send a pull request directly.

For anything larger, open an issue with the change proposal form first: a new feature, a change in behavior, a new
dependency, or a change to one of the [contracts](docs/contracts/README.md) between the parts. We can agree on the
approach there before you spend time on code. The [roadmap](docs/ROADMAP.md) says what I plan to work on and what
Deep32 will not do.

Pull requests go against `main`. Please keep each one to a single topic. [GOVERNANCE.md](GOVERNANCE.md) says who
reviews and decides on a change.

### 4.2 What a change includes

- **Tests.** New behavior comes with tests, and a bug fix comes with a test that fails without the fix. That applies
  to every change I merge, mine included. The suites run offline with synthetic keys, fake services and temporary
  folders; please keep new tests that way, with no real tokens, hosts or addresses in them.
- **Passing suites.** One command runs every suite, from the root of a clone; `--only` runs the ones whose name
  contains a word, and `--list` shows the plan. The same runner checks every pull request.

  ```sh
  python3 -I -B tests/run_all.py
  python3 -I -B tests/run_all.py --only relay --only adapters
  ```

  ```powershell
  python -I -B tests/run_all.py
  python -I -B tests/run_all.py --only relay --only adapters
  ```

  [docs/BUILDING.md](docs/BUILDING.md) has the command for each suite and what each one covers. A new test file is
  picked up by the runner on its own; if it needs a scratch folder or a tool, add its entry to `tests/run_all.py`.
  With GNU make, `make test` runs the suites and `make lint` the two linters below.
- **No new lint findings.** These pass on every pull request, and should pass before you push
  ([docs/BUILDING.md section 3](docs/BUILDING.md#3-lint-and-static-analysis) has the setup):

  ```sh
  ruff check --no-cache .
  bandit -c bandit.yaml -r . -b bandit-baseline.json
  ```

- **Each file's own style.** Keep the style of the file you are in, and don't reformat code you didn't change (no
  `ruff format` or `black` runs over existing files). Use American spelling in comments, messages and documentation.
- **Documentation** for anything a user can see: update the page that describes the behavior, and add a line for the
  release notes to the pull request description. A change to a trust boundary, a credential or a protocol also
  updates the [assurance case](docs/ASSURANCE-CASE.md).
- **A clear commit message**: a short summary line that names the area, as in the existing history
  (`fix(relay): ...`, `docs: ...`), and a body that says why when the summary doesn't.

### 4.3 What changes only with a release

The published images are built from these paths, and each release checks that the public source matches what is on
the images, file by file:

- `src/engine`, `src/gateway`, `src/gpu-pack`, `src/model-bridge`, `src/release-tools`, `src/workstation` and
  `src/diagnostics`
- `src/build/alpha2-tree`
- `install/release-key.*`
- the license and notice files that ship on the images: `LICENSE`, `NOTICE`, `licenses/AGPL-3.0-or-later.txt` and
  `notices/`

A change to any of them therefore waits for the next release that rebuilds the images. I still review it when it
arrives, and I schedule it with that rebuild. Everything else, including the setup scripts, the machine inventory, the
agent adapters, the tests and the documentation, can change between releases.

## 5. Licensing of contributions

| Part | License |
|---|---|
| Code | Apache License 2.0 (SPDX: `Apache-2.0`), with the `NOTICE` file |
| Code that uses MuPDF: the Forge's PDF converter in `src/gpu-pack/containers/converter-agpl` and the Workstation Library's renderer `e26-render` ([licenses/LICENSING.md](licenses/LICENSING.md) lists the files) | AGPL-3.0-or-later |
| Documentation | CC BY 4.0 (SPDX: `CC-BY-4.0`) |

For code, section 5 of the Apache License 2.0 already covers what you send: unless you say otherwise, a contribution
you submit for inclusion is licensed under the Apache License 2.0, with no additional terms. A change to the AGPL parts
is licensed under AGPL-3.0-or-later, and a change to the documentation under CC BY 4.0. You keep your copyright. Please only send work you have the right to license that way. If you include code from somewhere
else, say where it comes from and under which license; it has to be compatible with the license of the part it goes
into.

[REUSE.toml](REUSE.toml) records the copyright holder and license of every file. A new file that takes comments
starts with its own two lines, with your name if you wrote it:

```text
SPDX-FileCopyrightText: 2026 Your Name
SPDX-License-Identifier: Apache-2.0
```

Sign off each commit with `git commit -s`. The `Signed-off-by:` line certifies the
[Developer Certificate of Origin 1.1](https://developercertificate.org): that you wrote the change, or otherwise have
the right to submit it under the license of the part it changes. Section 5 of the Apache License 2.0 covers the
license of code you send; the sign-off records that you were entitled to send it.

## 6. Conduct

Everyone taking part in this project's issues, discussions and pull requests is expected to follow the
[Code of Conduct](CODE_OF_CONDUCT.md). To report a problem with someone's conduct, email
[jacummings@acm.org](mailto:jacummings@acm.org).

---

<p align="center"><sub><a href="README.md">Overview</a> · <a href="INSTALL.md">Installation</a> · <a href="docs/HARDWARE.md">Hardware</a> · <a href="docs/OPTIONAL-FEATURES.md">Optional features</a> · <a href="docs/tr/README.md">Design</a> · <a href="docs/DECISIONS.md">Decision record</a> · <a href="docs/contracts/README.md">Contracts</a> · <a href="adapters/README.md">Agent adapters</a> · <a href="SECURITY.md">Security</a> · <a href="CHANGELOG.md">Release notes</a></sub></p>
