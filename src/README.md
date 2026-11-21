# Source

The code for every part of Deep32. The [architecture](../README.md#4-architecture) shows how the parts fit together;
component pages next to the code describe each one in depth.

| Folder | What it holds |
|---|---|
| [`engine/`](engine/) | The search engine: hybrid keyword and vector search, fusion, reranking calls, the section reader, identifier lookup and the quotation check |
| [`gateway/`](gateway/) | The MCP gateway the agent talks to: eleven tools, token check, untrusted-output instructions |
| [`workstation/`](workstation/) | The OpenBSD Workstation: node services, the desktop, the Ask window and the Library programs |
| [`gpu-pack/`](gpu-pack/) | The Forge: its Docker containers, the GPU pack builder, first boot, pairing and publication ([PACK.md](gpu-pack/PACK.md)) |
| [`model-bridge/`](model-bridge/) | The authenticated request and model-attestation core shared by the Workstation's forwarder and the Forge |
| [`kb-tools/`](kb-tools/) | Builders for the knowledge base: the RFC index, errata, IANA registries, the CLI index and feature history |
| [`release-tools/`](release-tools/) | `deep32-get` and the tools that assemble, sign and verify a release |
| [`build/`](build/) | The image build stages for the Workstation and the input resolver for the GPU pack |

---

<p align="center"><sub><a href="../README.md">Overview</a> · <a href="../INSTALL.md">Installation</a> · <a href="../docs/HARDWARE.md">Hardware</a> · <a href="../docs/OPTIONAL-FEATURES.md">Optional features</a> · <a href="../docs/tr/README.md">Design</a> · <a href="../docs/DECISIONS.md">Decision record</a> · <a href="../docs/contracts/README.md">Contracts</a> · <a href="../adapters/README.md">Agent adapters</a> · <a href="../.github/SECURITY.md">Security</a> · <a href="../CHANGELOG.md">Release notes</a></sub></p>
