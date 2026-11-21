# Tests

Cross-component tests with their fixtures. Tests for a single component live next to its code: `adapters/tests`,
`src/gpu-pack/tests`, `src/kb-tools/linux-line/tests` and `src/build/alpha2-tree`. Every suite runs offline, with
synthetic keys and stand-ins for the services it talks to; none needs a GPU, a VM or a network.
`python3 -I -B tests/run_all.py` runs them all, each the way it expects to be run, and
[docs/BUILDING.md](../docs/BUILDING.md#2-running-the-tests) has the command for each suite.

| Folder | What it covers |
|---|---|
| [`release-tools/`](release-tools/) | Release assembly, download, verification, signing, SBOMs and the install matrix |
| [`inventory/`](inventory/) | The machine inventory on Linux and Windows |
| [`relay/`](relay/) | The Ask relay: pairing, link, TLS, sessions, service install and agent processes |
| [`headend/`](headend/) | The agent connector setup and the research skills |
| [`pairing/`](pairing/) | Pairing by short code between the parts, and the network contract's reference behavior |
| [`publication/`](publication/) | Publishing services on the tailnet with owned routes, and refusing to touch routes Deep32 did not add |
| [`docker_role/`](docker_role/) | The Forge's Docker lifecycle, host paths and the model bridge |
| [`workstation/`](workstation/) | The Workstation's importer and installer, and the Ask window's citations |
| [`contracts/`](contracts/) | The console status contract's size limits |
| [`diagnostics/`](diagnostics/) | `deep32 doctor` and `deep32 selftest` |
| [`hardening/`](hardening/) | The checklist in [docs/PRE-RUN.md](../docs/PRE-RUN.md): every gate is present and fails closed |
| [`fuzz/`](fuzz/README.md) | Fuzz targets for the Workstation's C parsers, built with sanitizers on Linux, and a check on any system that their seeds and CI jobs match |

---

<p align="center"><sub><a href="../README.md">Overview</a> · <a href="../INSTALL.md">Installation</a> · <a href="../docs/HARDWARE.md">Hardware</a> · <a href="../docs/OPTIONAL-FEATURES.md">Optional features</a> · <a href="../docs/tr/README.md">Design</a> · <a href="../docs/DECISIONS.md">Decision record</a> · <a href="../docs/contracts/README.md">Contracts</a> · <a href="../adapters/README.md">Agent adapters</a> · <a href="../.github/SECURITY.md">Security</a> · <a href="../CHANGELOG.md">Release notes</a></sub></p>
