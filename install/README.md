# Install

What you run first, from a clone of this repository. The inventory only reports and the verifiers only check; the
setup scripts change a machine only with your consent, and `check` mode never does. The [Installation Guide](../INSTALL.md) walks through the whole install.

| File | What it does |
|---|---|
| `deep32-setup.sh`, `deep32-setup.ps1` | One command per machine: works out its role (Forge, Workstation host or headend), checks it, fixes what it safely can with your consent, then downloads, verifies and installs its part. Every line is tagged `[ok]`, `[fix]`, `[you]`, `[fail]` or `[info]` |
| `forge-host-python.sh` | Checks the Forge host's own Python for the pairing helper, and installs it with the package manager where the system allows |
| `deep32-inventory.sh`, `deep32-inventory.ps1` | Reports which parts of Deep32 this machine can run, and which GPU pack fits its card |
| `matrix.json` | The supported install profiles and sizing rules the inventory and installer both read ([contract C1](../docs/contracts/C1-install-matrix.md)) |
| `prerequisites.json` | The software each profile needs, and the commands the inventory suggests to install it |
| `verify-release.sh`, `verify-release.ps1` | Check a downloaded release against the signing key below |
| `release-key.asc`, `release-key.gpg`, `release-key.fingerprint` | The release signing key and its pinned fingerprint |

---

<p align="center"><sub><a href="../README.md">Overview</a> · <a href="../INSTALL.md">Installation</a> · <a href="../docs/HARDWARE.md">Hardware</a> · <a href="../docs/OPTIONAL-FEATURES.md">Optional features</a> · <a href="../docs/tr/README.md">Design</a> · <a href="../docs/DECISIONS.md">Decision record</a> · <a href="../docs/contracts/README.md">Contracts</a> · <a href="../adapters/README.md">Agent adapters</a> · <a href="../SECURITY.md">Security</a> · <a href="../CHANGELOG.md">Release notes</a></sub></p>
