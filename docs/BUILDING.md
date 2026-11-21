# Building and testing Deep32

How to run the test suites and the linters on a clone of this repository, and how the release images are made. It is
for anyone who wants to change Deep32 or check my work.

<sub>Applies to 0.1.0-alpha.2 · [1 What you need](#1-what-you-need) · [2 Tests](#2-running-the-tests) · [3 Lint and static analysis](#3-lint-and-static-analysis) · [4 Images](#4-building-the-images)</sub>

## 1. What you need

Most of Deep32 is Python and shell scripts that run from source, so nothing has to be compiled before the tests. The
suites use Python's standard library only. They work in temporary folders with synthetic keys and stand-ins for
Docker, Tailscale, the NVIDIA tools and the other parts, so none of them needs a graphics card, Docker, a VM, a
Tailscale sign-in or a network connection.

| Tool | Needed for | What I used on 19 November 2026 |
|---|---|---|
| Python 3.12 or newer | Every suite | 3.15.2 and 3.12.14 (section 2.4) |
| bash | The suites that run the shell scripts | Git for Windows (bash 5.3) |
| Windows PowerShell 5.1 | The suites that run the PowerShell scripts, on Windows | 5.1.26100 |
| GnuPG | The signing and verification suites | Gpg4win, `C:\Program Files\GnuPG\bin` |

> [!NOTE]
> On Windows the suites call Git for Windows' bash by its full path, `C:\Program Files\Git\bin\bash.exe`. A bare
> `bash` on Windows can start a WSL distribution, so don't pass one to a suite that asks for a shell.

## 2. Running the tests

### 2.1 One command

From the root of the clone:

```sh
python3 -I -B tests/run_all.py
```

```powershell
python -I -B tests\run_all.py
```

With GNU make, `make test` runs the same command and `make check` is another name for it; `make lint` runs the linters
of section 3, and `make help` lists the targets and their variables ([Makefile](../Makefile)). Git for Windows doesn't
include make.

`run_all.py` runs every suite the way it expects to be run. Each suite starts from the root of the clone with an empty
scratch folder of its own, given to it as an argument or an environment variable, so nothing is written into the
clone. A suite that needs a tool this machine lacks is skipped, with the reason on its line. A test file that no suite
lists runs as a plain `unittest` file, so a new test is never left out by accident. On Python 3.12 and 3.13 the
runner leaves out the one test that hits a known problem in code that changes only with a release (section 2.4), and
the suite's line names it. The runner prints one line per suite and a summary, and exits with 1 if any suite
failed.

| Option | Effect |
|---|---|
| `--list` | Show which suites would run and which would be skipped, and why; run nothing |
| `--only TEXT`, `--exclude TEXT` | Run only, or leave out, the suites whose name contains TEXT |
| `--without bash`, `gpg`, `powershell` or `openssl` | Treat a tool as missing, to see what a machine without it runs |
| `--keep` | Keep the scratch folders and logs after a passing run (they are always kept after a failure) |
| `--timeout SECONDS` | Time allowed per suite, 1800 by default |

```text
PASS  [1/59] release-tools: 340 tests, 1 skipped, 116 s
PASS  [2/59] release-tools/capabilities: 37 tests, 4 s
...
SKIP  [12/59] release-tools/docker_install: needs GnuPG (Gpg4win at C:\Program Files\GnuPG\bin)
...
PASS  [29/59] relay: 402 tests, 7 skipped, 42 s
...
55 passed, 0 failed, 4 skipped, 30.7 min
```

### 2.2 Each suite by hand

<details>
<summary><b>The commands <code>run_all.py</code> runs, for one suite at a time</b></summary>

`-B` keeps Python from writing `__pycache__` folders into the clone.

```sh
PY=python3
S=$(mktemp -d)

# Release tools: deep32-get, verification, assembly, signing and the SBOM tools
plain="alpha2_assembly bundle_assembly c2_prepare_signing_inputs forge_preflight_windows forge_requirements
       owner_inputs owner_media sbom_archives sbom_distributions sbom_frozen sbom_inputs sbom_public_path sbom_spdx
       sbom_versions sbom_workflow storage_declared whole_assembly"
"$PY" -B -m unittest $(printf 'tests/release-tools/test_%s.py ' $plain)
for t in capabilities disc3_join docker_install forge_pair_windows forge_ports forge_preflight get_powershell \
         get_whole_bash getter_kit gpg_discovery phase_a_helpers settings settings_bash signature_class \
         whole_get whole_trust; do
    mkdir -p "$S/$t" && "$PY" -B "tests/release-tools/test_$t.py" --scratch-root "$S/$t"
done
"$PY" -B tests/release-tools/test_contracts.py --scratch "$S/contracts"
"$PY" -B tests/release-tools/test_release_tools.py --tools-dir src/release-tools --scratch-root "$S/release-tools"
"$PY" -B tests/release-tools/test_phase_a_matrix.py --tools-dir src/release-tools --scratch-root "$S/phase-a"
"$PY" -B tests/test-whole-download.py --tmp "$S/whole-download"

# Machine inventory
"$PY" -B tests/inventory/run_tests.py --work "$S/inventory"
for t in run_prereqs test_contract_fixes test_storage_numbers; do
    "$PY" -B "tests/inventory/$t.py" --work "$S/inventory-$t"
done
"$PY" -B tests/inventory/test_linux_forge_prereqs.py --scratch-root "$S/linux-forge"

# Agent adapters (Connect), the headend setup and the Ask relay
DEEP32_CONNECT_TEST_ROOT="$S/connect" "$PY" -B -m unittest adapters/tests/test_connect.py
mkdir -p "$S/relay" && DEEP32_RELAY_TEST_ROOT="$S/relay" "$PY" -B -m unittest discover -s tests/relay
"$PY" -B -m unittest tests/headend/test_setup.py tests/headend/test_skills.py
"$PY" -B tests/headend/sync_twins.py --check

# Pairing, publication and the Forge's Docker role
for t in callers integration pairdesk_port; do "$PY" -B "tests/pairing/test_$t.py" --tmp "$S/pairing-$t"; done
for t in enrol forge forwarder; do "$PY" -B "tests/pairing/test_$t.py" --scratch "$S/pairing-$t"; done
"$PY" -B tests/pairing/reference/tests/run_all.py --tmp "$S/pairing-reference"
"$PY" -B tests/publication/test_publication.py --tmp "$S/publication"
mkdir -p "$S/docker" && D32_DOCKER_TEST_ROOT="$S/docker" "$PY" -B -m unittest tests/docker_role/test_full_runtime.py
"$PY" -B -m unittest tests/docker_role/test_host_paths.py
"$PY" -B tests/docker_role/test_lifecycle.py --scratch "$S/lifecycle"
"$PY" -B tests/docker_role/test_model_bridge.py --scratch "$S/model-bridge"

# Contracts, Workstation programs, diagnostics and documentation checks
"$PY" -B tests/contracts/test_c6_limits.py --scratch "$S/c6" --shell "$(command -v bash)"
"$PY" -B -m unittest tests/hardening/test_prerun_docs.py
"$PY" -B -m unittest tests/fuzz/test_seeds.py
mkdir -p "$S/workstation" && D32_TEST_WORK="$S/workstation" "$PY" -B -m unittest \
    $(printf 'tests/workstation/test_%s.py ' ask_cite_maps diagnostics_placement importer_content_compat \
      importer_mount_failure installer_alpha2)
mkdir -p "$S/diagnostics" && DEEP32_DIAG_TEST_ROOT="$S/diagnostics" D32_SUITE_SCRATCH="$S/diagnostics" \
    "$PY" -B -m unittest discover -s tests/diagnostics

# GPU pack, knowledge-base tools and the release tree builder
gpu="alpha2_disc3 alpha2_metadata alpha2_pipeline alpha2_qualify alpha2_rebuild builder_eval_paths converter_debs
     image_binding lean_image_recipes model_cache runtime_stage"
"$PY" -B -m unittest $(printf 'src/gpu-pack/tests/test_%s.py ' $gpu)
mkdir -p "$S/preflight" && D32_RUNTIME_TEST_SCRATCH="$S/preflight" "$PY" -B -m unittest \
    src/gpu-pack/tests/test_host_preflight_hardening.py
for t in forge_capabilities forge_docker_role forge_jobs forge_publication forge_retained_runtime \
         installed_runtime runtime_preflight storage_requirements; do
    mkdir -p "$S/gpu-$t" && "$PY" -B "src/gpu-pack/tests/test_$t.py" --scratch "$S/gpu-$t"
done
"$PY" -B -m unittest src/kb-tools/linux-line/tests/test_disc2_assemble.py src/kb-tools/linux-line/tests/test_w2_identity.py
"$PY" -B -m unittest $(printf 'src/build/alpha2-tree/test_%s.py ' alpha2_tree check_reuse release_key tree_references)
```

```powershell
$PY = 'python'
$S = Join-Path $env:TEMP ('d32-tests-' + [guid]::NewGuid())
function Scratch($name) { (New-Item -ItemType Directory -Force (Join-Path $S $name)).FullName }
$GitBash = 'C:\Program Files\Git\bin\bash.exe'

# Release tools
$plain = 'alpha2_assembly','bundle_assembly','c2_prepare_signing_inputs','forge_preflight_windows','forge_requirements',
         'owner_inputs','owner_media','sbom_archives','sbom_distributions','sbom_frozen','sbom_inputs','sbom_public_path',
         'sbom_spdx','sbom_versions','sbom_workflow','storage_declared','whole_assembly'
& $PY -B -m unittest ($plain | ForEach-Object { "tests/release-tools/test_$_.py" })
foreach ($t in 'capabilities','disc3_join','docker_install','forge_pair_windows','forge_ports','forge_preflight',
               'get_powershell','get_whole_bash','getter_kit','gpg_discovery','phase_a_helpers','settings',
               'settings_bash','signature_class','whole_get','whole_trust') {
    & $PY -B "tests/release-tools/test_$t.py" --scratch-root (Scratch $t)
}
& $PY -B tests/release-tools/test_contracts.py --scratch (Scratch 'contracts')
& $PY -B tests/release-tools/test_release_tools.py --tools-dir src/release-tools --scratch-root (Scratch 'release-tools')
& $PY -B tests/release-tools/test_phase_a_matrix.py --tools-dir src/release-tools --scratch-root (Scratch 'phase-a')
& $PY -B tests/test-whole-download.py --tmp (Scratch 'whole-download')
powershell -NoProfile -ExecutionPolicy Bypass -File tests/release-tools/test_settings_handoff.ps1 -Tools src/release-tools -Scratch (Scratch 'settings-handoff')

# Machine inventory
& $PY -B tests/inventory/run_tests.py --work (Scratch 'inventory')
foreach ($t in 'run_prereqs','test_contract_fixes','test_storage_numbers') {
    & $PY -B "tests/inventory/$t.py" --work (Scratch "inventory-$t")
}
& $PY -B tests/inventory/test_linux_forge_prereqs.py --scratch-root (Scratch 'linux-forge')

# Agent adapters (Connect), the headend setup and the Ask relay
$env:DEEP32_CONNECT_TEST_ROOT = Scratch 'connect'
& $PY -B -m unittest adapters/tests/test_connect.py
$env:DEEP32_RELAY_TEST_ROOT = Scratch 'relay'
& $PY -B -m unittest discover -s tests/relay
& $PY -B -m unittest tests/headend/test_setup.py tests/headend/test_skills.py
& $PY -B tests/headend/sync_twins.py --check

# Pairing, publication and the Forge's Docker role
foreach ($t in 'callers','integration','pairdesk_port') { & $PY -B "tests/pairing/test_$t.py" --tmp (Scratch "pairing-$t") }
foreach ($t in 'enrol','forge','forwarder') { & $PY -B "tests/pairing/test_$t.py" --scratch (Scratch "pairing-$t") }
& $PY -B tests/pairing/reference/tests/run_all.py --tmp (Scratch 'pairing-reference')
& $PY -B tests/publication/test_publication.py --tmp (Scratch 'publication')
$env:D32_DOCKER_TEST_ROOT = Scratch 'docker'
& $PY -B -m unittest tests/docker_role/test_full_runtime.py
& $PY -B -m unittest tests/docker_role/test_host_paths.py
& $PY -B tests/docker_role/test_lifecycle.py --scratch (Scratch 'lifecycle')
& $PY -B tests/docker_role/test_model_bridge.py --scratch (Scratch 'model-bridge')

# Contracts, Workstation programs, diagnostics and documentation checks
& $PY -B tests/contracts/test_c6_limits.py --scratch (Scratch 'c6') --shell $GitBash
& $PY -B -m unittest tests/hardening/test_prerun_docs.py
& $PY -B -m unittest tests/fuzz/test_seeds.py
$env:D32_TEST_WORK = Scratch 'workstation'
& $PY -B -m unittest ('ask_cite_maps','diagnostics_placement','importer_content_compat','importer_mount_failure',
                      'installer_alpha2' | ForEach-Object { "tests/workstation/test_$_.py" })
$env:DEEP32_DIAG_TEST_ROOT = Scratch 'diagnostics'
$env:D32_SUITE_SCRATCH = $env:DEEP32_DIAG_TEST_ROOT
& $PY -B -m unittest discover -s tests/diagnostics

# GPU pack, knowledge-base tools and the release tree builder
$gpu = 'alpha2_disc3','alpha2_metadata','alpha2_pipeline','alpha2_qualify','alpha2_rebuild','builder_eval_paths',
       'converter_debs','image_binding','lean_image_recipes','model_cache','runtime_stage'
& $PY -B -m unittest ($gpu | ForEach-Object { "src/gpu-pack/tests/test_$_.py" })
$env:D32_RUNTIME_TEST_SCRATCH = Scratch 'preflight'
& $PY -B -m unittest src/gpu-pack/tests/test_host_preflight_hardening.py
foreach ($t in 'forge_capabilities','forge_docker_role','forge_jobs','forge_publication','forge_retained_runtime',
               'installed_runtime','runtime_preflight','storage_requirements') {
    & $PY -B "src/gpu-pack/tests/test_$t.py" --scratch (Scratch "gpu-$t")
}
& $PY -B -m unittest src/kb-tools/linux-line/tests/test_disc2_assemble.py src/kb-tools/linux-line/tests/test_w2_identity.py
& $PY -B -m unittest ('alpha2_tree','check_reuse','release_key','tree_references' | ForEach-Object { "src/build/alpha2-tree/test_$_.py" })
```

A suite that passes ends with `OK` (sometimes with a count of skipped tests) or with a `PASS:` line that counts its
assertions. The release-tools suites that drive the shell and PowerShell scripts look for bash, Windows PowerShell and
GnuPG at their Windows install paths by default; each takes `--bash`, `--powershell`, `--gpg` and `--gpgconf` to point
elsewhere.

</details>

### 2.3 What each area covers

| Area | Folder | What it covers |
|---|---|---|
| Release tools | `tests/release-tools/`, `tests/test-whole-download.py` | `deep32-get` on Linux and Windows, signature checks, the disc 3 join and install plan, Forge preflight and ports, storage budgets, release assembly, signing, SBOMs |
| Inventory | `tests/inventory/` | The machine inventory in both its shell and PowerShell forms, on fixtures of fake machine facts |
| Agent adapters | `adapters/tests/`, `tests/headend/` | Connect, the setup of Claude Code and Codex, the research skills |
| Ask relay | `tests/relay/` | The relay's pairing, link, TLS, sessions, service install and agent processes |
| Pairing and publication | `tests/pairing/`, `tests/publication/` | Pairing by short code between the parts, the network reference package, and publishing services with routes Deep32 owns |
| Forge Docker role | `tests/docker_role/`, `src/gpu-pack/tests/` | The Forge's lifecycle, host paths, the model bridge, preflight, storage budgets, the disc 3 image build checks |
| Workstation | `tests/workstation/`, `tests/contracts/` | The importer, the installer, the Ask window's citations, the console status contract |
| Diagnostics | `tests/diagnostics/` | `deep32 doctor` and `deep32 selftest` |
| Fuzz targets | `tests/fuzz/` | That the seed corpora and the CI matrix match the fuzz targets; the fuzzing itself runs on Linux (section 2.7) |
| Knowledge base and release tree | `src/kb-tools/linux-line/tests/`, `src/build/alpha2-tree/` | Disc 2 assembly, embedding identity, the release tree and its references |

### 2.4 Results for 0.1.0-alpha.2

On 19 November 2026 I ran `tests/run_all.py` twice on the release branch on Windows 11, with Git for Windows (bash 5.3,
gawk 5.4.0) and Windows PowerShell 5.1, while other jobs kept the machine's processor busy. The second run left out
GnuPG, as on the CI runner.

| Run | Passed | Failed | Skipped | Time |
|---|---|---:|---:|---:|
| Python 3.15.2 with Gpg4win | 58 suites: 2,304 tests (22 skipped), 758 assertions and 317 inventory checks | 1 | 0 | 41 min |
| Python 3.12.14 without GnuPG | 55 suites: 2,345 tests (24 skipped), 234 assertions and 317 inventory checks | 0 | 4 | 31 min |

The four skipped suites need Gpg4win. In these runs, on Python 3.12 and 3.13 the runner left out the tests that hit
two known problems in code that changes only with a release. The first is fixed in this release, so the runner now
leaves out only the second, which is planned for the next release:

- `tests/diagnostics/test_doctor.py` and `test_headend.py`: before Python 3.14 they could not import
  `src/diagnostics/deep32_diag/headend.py`, because its field annotation on line 54 named the `profile` module inside
  a class that is defining a field of the same name, and `deep32 doctor` on a headend with Python 3.12 or 3.13 stopped
  at the same import. The module now postpones the evaluation of its annotations, and both tests run again.
- `Helpers.test_split_backing_filesystems_are_budgeted_separately` in `tests/release-tools/test_phase_a_helpers.py`:
  before Python 3.14 `Path.lstat` goes through `Path.stat`, which the test replaces with a stat result that has no
  `st_file_attributes`, and `src/release-tools/deep32_get_manifest.py` line 62 doesn't accept that.

The one failure in the first run was `pairing/reference`: an end-to-end pairing test got a connection error where
it expected the peer's refusal, while both runs were sharing the processor. The suite passed in the second run and
twice more on its own. A few other tests have short time limits (160 ms to 10 s) and can also fail on a fully loaded
machine, so run a failing suite again on its own before you look further.

The suites have not been run on Linux for this release. On Linux, `run_all.py` skips the suites that need Windows or
Windows PowerShell 5.1 (`docker_install`, `get_powershell`, `settings_bash`, `signature_class`, `release_tools`,
`phase_a_matrix` and `settings_handoff`). The others should run there as well, but I haven't checked.

### 2.5 Continuous integration

Every push and pull request to `main` runs [`.github/workflows/tests.yml`](../.github/workflows/tests.yml): Ruff and
Bandit on Ubuntu (section 3), then `run_all.py` on Windows with Python 3.12, the oldest Python the suites support. The suites run on Windows because that is
where I verify them for each release. CodeQL analyzes the Python and C code on the same events and once a week, and
OpenSSF Scorecard checks the repository's supply-chain practices once a week and on each push to `main`.
[`fuzz.yml`](../.github/workflows/fuzz.yml) fuzzes the C parsers on every push and pull request and for longer each
week (section 2.7), [`osv-scanner.yml`](../.github/workflows/osv-scanner.yml) checks the pinned Python packages for
known vulnerabilities on the same events and weekly (section 3), and on each push to `main`
[`coverage.yml`](../.github/workflows/coverage.yml) measures the coverage of the Python code (section 2.6).

### 2.6 Coverage

`make coverage` runs every suite under coverage.py with branch coverage, including the Python programs the suites
start as separate processes, and writes HTML, XML and JSON reports to `coverage-report/` (or to the folder named by
`D32_COVERAGE_DIR`). [`.github/coverage.rc`](../.github/coverage.rc) says what is measured: every Python file under
`src/`, `adapters/` and `install/`, without the test code. coverage.py is pinned by version and hash:

```sh
python3 -m pip install --require-hashes -r .github/requirements-coverage.txt
make coverage
```

The Coverage workflow ([`.github/workflows/coverage.yml`](../.github/workflows/coverage.yml)) does the same on Windows
with Python 3.12 for each push to `main`, writes the totals and a table by area to the job summary, and keeps the
reports for 30 days. It never fails on the percentage, only when a suite fails. Some suites take two or three times as
long under coverage, so a test with a short time limit can fail there and pass without coverage. The figures for
0.1.0-alpha.2 are in [the roadmap, section 5.1](ROADMAP.md#51-tests-and-coverage).

### 2.7 Fuzzing

The C parsers in the Workstation's source have libFuzzer targets in [`tests/fuzz/`](../tests/fuzz/README.md), built
with AddressSanitizer and UndefinedBehaviorSanitizer straight from their files under `src/workstation`. They build with
clang on Linux, because OpenBSD has neither AddressSanitizer nor libFuzzer. The Fuzz workflow
([`.github/workflows/fuzz.yml`](../.github/workflows/fuzz.yml)) runs each target for two minutes on every push and pull
request to `main` and for thirty minutes every week. [tests/fuzz/README.md](../tests/fuzz/README.md) says how to build
and run the targets and how to reproduce a crash, and records the run of 19 November 2026 and what it found. On any
system, the `fuzz/seeds` suite checks that the seed corpora and the workflow's list of targets match the targets.

## 3. Lint and static analysis

I use [Ruff](https://docs.astral.sh/ruff/) to find mistakes in the Python code and
[Bandit](https://bandit.readthedocs.io/) to look for common security problems. Both only read; neither changes a
file. `.github/requirements-lint.txt` pins both, with their dependencies, by version and hash. Install them in a
virtual environment of their own:

```sh
python3 -m venv ~/.venvs/d32lint
~/.venvs/d32lint/bin/pip install --require-hashes -r .github/requirements-lint.txt
~/.venvs/d32lint/bin/ruff check --no-cache .
~/.venvs/d32lint/bin/bandit -c bandit.yaml -r . -b bandit-baseline.json
```

```powershell
python -m venv "$env:LOCALAPPDATA\d32lint"
& "$env:LOCALAPPDATA\d32lint\Scripts\pip.exe" install --require-hashes -r .github\requirements-lint.txt
& "$env:LOCALAPPDATA\d32lint\Scripts\ruff.exe" check --no-cache .
& "$env:LOCALAPPDATA\d32lint\Scripts\bandit.exe" -c bandit.yaml -r .
```

Both commands pass only when there is nothing new. `ruff.toml` names the rule set: syntax errors, Pyflakes, Pylint's
error checks and flake8-bugbear. It ignores four bugbear rules whose findings in this tree have no effect on behavior,
leaves the existing findings in `src/gpu-pack` and `src/workstation` alone until the next release, because the images
are built from those files, and lists the remaining reviewed findings file by file. `bandit-baseline.json` holds the
reviewed Bandit findings, and `-b` reports only findings that aren't in it. Its paths are written the way Bandit
prints them on Linux and macOS, so on Windows run Bandit without `-b` and compare by hand, or use `make lint`, which
gives Bandit a copy of the baseline with Windows paths. Please don't run
`ruff format` or `ruff check --fix` over files you aren't otherwise changing.

The recorded run for 0.1.0-alpha.2, on 19 November 2026 with Ruff 0.17.5 and Bandit 1.9.4:

| Tool | Files | Findings | Review |
|---|---:|---:|---|
| Ruff | 670 | 41 outside the image source | 29 unused imports, 6 unused variables and 6 style findings. None is a defect |
| Bandit | 485 | 185: 4 high, 100 medium, 81 low | Every high and medium finding reviewed. None is an exploitable vulnerability: SHA-1 appears only where OpenPGP v4 fingerprints and git blob IDs require it, SQL is built from constant or catalog names with values passed as parameters, and the services that listen on all interfaces do so inside their containers |

Four findings are worth a change at the next release: a bidirectional control character and a zero-width space
written raw in a converter string, inconsistent quoting of SQL identifiers in the knowledge-base bundle checker, an
unused value in the importer, and a shadowed exception class in the Workstation's forwarder to the Forge. CodeQL also
analyzes the Python and C code on every push (section 2.5). The shell and PowerShell scripts have no linter run
recorded for this release.

[OSV-Scanner](https://google.github.io/osv-scanner/) checks the pinned Python packages against the
[OSV database](https://osv.dev) on each push and pull request to `main` and once a week
([`.github/workflows/osv-scanner.yml`](../.github/workflows/osv-scanner.yml)). Findings in the pins the release images
are built from go to the repository's code scanning alerts, because those pins change only when the images are
rebuilt. That scan leaves out the advisories listed in [`osv-scanner.toml`](../osv-scanner.toml), which are the ones
the release's OpenVEX document marks as not affecting Deep32; [docs/security/README.md](security/README.md) describes
the triage. A known vulnerability in the other pins fails the workflow: the CI tools, the Ask relay's lock file and the
test pins in `tests/engine` and `tests/gateway`, which are scanned without those exceptions.

## 4. Building the images

The three discs are built on dedicated build hosts as part of release work. The builds start from inputs that are not
in this repository: OpenBSD sets and packages, pinned Python sources and wheels, upstream container images, the model
files and the corpus. The build recipes check every input against its recorded hash, and each image carries a software
bill of materials and a provenance record that names the source revision it was built from. I haven't written a guide
for building the images on your own machine yet, and you don't need one to work on the code: the suites above cover
the programs that go onto the images.

- [src/README.md](../src/README.md) describes the source folders.
- [src/build/README.md](../src/build/README.md) covers the Workstation's image build stages and the GPU pack's input
  resolver.
- [src/gpu-pack/PACK.md](../src/gpu-pack/PACK.md) describes the GPU pack (disc 3).
- [src/workstation/BUILD-INPUTS.md](../src/workstation/BUILD-INPUTS.md) lists the Workstation's build inputs.

The Deep32 Library on the Workstation is C99. It builds with GNU make on OpenBSD with warnings treated as errors; its
[Makefile](../src/workstation/library/Makefile) lists the targets.

---

<p align="center"><sub><a href="../README.md">Overview</a> · <a href="../INSTALL.md">Installation</a> · <a href="HARDWARE.md">Hardware</a> · <a href="OPTIONAL-FEATURES.md">Optional features</a> · <a href="tr/README.md">Design</a> · <a href="DECISIONS.md">Decision record</a> · <a href="contracts/README.md">Contracts</a> · <a href="../adapters/README.md">Agent adapters</a> · <a href="../SECURITY.md">Security</a> · <a href="../CHANGELOG.md">Release notes</a></sub></p>
