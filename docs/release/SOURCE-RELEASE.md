# Source release 0.1.0-alpha.3 (alpha)

This is the source of Deep32 0.1.0-alpha.3. This page says what the source holds, which licenses apply, and how to
identify, verify and cite this version. docs/release/RELEASE.json records the same facts for tools.

## Identity

| Field | Value |
|---|---|
| Version | 0.1.0-alpha.3 |
| Git tag | v0.1.0-alpha.3 |
| Release date | 22 November 2026 |
| Release DOI | Added after the release is archived; until then, cite the concept DOI |
| Concept DOI, all versions | 10.5281/zenodo.24272647 |
| Signing key fingerprint | B205 B406 5486 27AA BF52  50D8 A078 C5F2 5D02 5DD6 |

The public signing key is install/release-key.asc, and install/release-key.fingerprint holds its fingerprint. To check
the tag in a clone:

```sh
gpg --import install/release-key.asc
git verify-tag v0.1.0-alpha.3
git rev-parse "v0.1.0-alpha.3^{commit}"
```

The tag points to the release commit; the last command prints that commit's hash.

Cite this version with its release DOI once it has one. The concept DOI always resolves to the newest version.

## Contents

The source folders are src/engine (the search engine), src/gateway (the MCP gateway agents talk to), src/model-bridge
(the shared bridge to the Forge's models), src/workstation (the OpenBSD Workstation and its Library programs),
src/gpu-pack (the GPU pack and the Forge's install forms), src/kb-tools (the knowledge-base build), src/diagnostics
(`deep32 doctor` and `deep32 selftest`), and src/build and src/release-tools (the image builds, release assembly and
verification). install/ holds the setup and inventory scripts, adapters/ the agent skills and their setup scripts,
and tests/ the offline test suites.

The engine API metadata and the gateway version are 1.0.0, from public source pub-9a2d58df01fc, which is also the
engine and gateway the Workstation runs. The original Python package import names are kept.

RELEASE.json lists the file names of the release's eleven images: the Workstation image (disc 1), the content image
(disc 2), and the GPU pack's shared core, seven embedders (one per card class) and ingestion image (disc 3). Their
SHA-256 values are in the release's signed checksum list. RELEASE.json also records the model revisions, the NVIDIA
driver floors, and the knowledge-base generation and fingerprint that the content image carries.

Building the images needs external inputs at pinned versions: operating-system sets, packages, toolchains, model
files and license texts. docs/BUILDING.md describes the build and the test suites. The source keeps the deterministic
point identifiers and settings-format markers that the content and image builds use. The public tree started from an
export of my development tree that removed private collection support, personal defaults, update history and
excluded content.

## Licenses

Deep32's code is under the Apache License 2.0 (LICENSE), with the attribution notices in NOTICE. Keep NOTICE with
every copy and every changed version, and mark the files you change.

Two parts use MuPDF, which is AGPL, and are under AGPL-3.0-or-later (licenses/AGPL-3.0-or-later.txt):

- the Forge's PDF converter, src/gpu-pack/containers/converter-agpl, which imports PyMuPDF;
- the Workstation Library's renderer e26-render, src/workstation/library/src/render, which links libmupdf.

Each runs as its own process. Documentation is under CC BY 4.0 (LICENSE-DOCS).

Third-party software, models, documents and art keep their own licenses; licenses/LICENSING.md and the notices
folder list them. The converter uses MinerU 4.0.7, which is under the MinerU Open Source License: Apache-2.0 with
additional terms (a separate commercial license above 100 million monthly active users or USD 20 million monthly
revenue, and an online service for third parties built on MinerU must say that it uses MinerU). Its license text is
src/gpu-pack/licenses/MinerU-4.0.7-LICENSE.md. The two Jina models are under CC BY-NC 4.0; the source references them
and does not include their weights.

## Known issues

The known issues for 0.1.0-alpha.3 are in CHANGELOG.md.
