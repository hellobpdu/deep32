# Known vulnerabilities in third-party packages

Each Deep32 release gets an OpenVEX document here: for every known vulnerability that OSV-Scanner reports in a
package Deep32 pins, it says whether the vulnerability affects Deep32 and why. It is for anyone who runs a scanner over
the source or the images and wants to know what a finding means for Deep32.

<sub>Applies to 0.1.0-alpha.2 · [1 What is here](#1-what-is-here) · [2 How I made it](#2-how-i-made-it) · [3 Result](#3-result-for-010-alpha2) · [4 What it does not cover](#4-what-it-does-not-cover) · [5 Keeping it current](#5-keeping-it-current)</sub>

## 1. What is here

[`deep32-0.1.0-alpha.2.openvex.json`](deep32-0.1.0-alpha.2.openvex.json) is an
[OpenVEX 0.2.0](https://github.com/openvex/spec) document with one statement per advisory and package version: 76
statements for 74 advisories, because setuptools is pinned at two versions. Its products are:

| Product | What it is |
|---|---|
| `pkg:oci/deep32-builder?tag=0.1.0-alpha.2` | The index builder image in the GPU pack's ingestion add-on |
| `pkg:oci/deep32-converter?tag=0.1.0-alpha.2` | The PDF converter image in the same add-on |
| `pkg:generic/deep32-workstation@0.1.0-alpha.2` | The Workstation's two Python environments, deep32-engine and deep32-gateway |
| `pkg:github/hellobpdu/deep32@v0.1.0-alpha.2` | Pin files in the source that no 0.1.0-alpha.2 image ships: the full Docker role, the knowledge-base build tools, the Workstation's wheel build environment and the test pins |

Each statement names the package as a `pkg:pypi` subcomponent of the product. The door and reranker images have no
statements because OSV-Scanner reports nothing for their Python packages.

[`osv-scanner.toml`](../../osv-scanner.toml) at the top of the repository lists the same 74 advisories as OSV-Scanner
exceptions, each with its justification and the name of its statement here. The scan of every pin file in
[`.github/workflows/osv-scanner.yml`](../../.github/workflows/osv-scanner.yml) reads it. The exceptions expire on 19
February 2027, so any finding that is still there by then shows again.

## 2. How I made it

1. **Scan.** On 19 November 2026 I ran OSV-Scanner 2.7.1 with the workflow's arguments over every pin file: 129
   findings in 10 pin files, which are 74 distinct advisories once aliases (GHSA, PYSEC and CVE identifiers for the
   same problem) are merged. The pin files under `src/gpu-pack/containers` list only the top-level packages of the
   converter and reranker images, so I also scanned the complete hash-locked lists those images are built from. They
   added no findings: 39 for the builder (the same as its pin file), 35 for the converter and none for the reranker.
2. **Reachability.** For each advisory I worked out which image or environment carries the package, whether Deep32
   imports it and runs the vulnerable function, and whether someone outside could reach that function in Deep32 as
   built. I read Deep32's source and the source of the installed packages along each call path, among them MinerU
   4.0.7, pylate 1.6.0, sentence-transformers 5.3.0, datasets 5.0.1 and mcp 2.2.0. The design facts the statements
   rely on:
   - The GPU pack's containers run offline. The builder reaches the embedding, reranking and Qdrant services over
     plain HTTP on an internal Docker network with no route out, and runs with `HF_HUB_OFFLINE=1`.
   - The PDF converter runs one job at a time with no network at all. MinerU runs vLLM inside its own process on page
     images that PDFium renders from the PDF. vLLM's HTTP server, its request fields, its KV-transfer connectors and
     `trust_remote_code` are never used, and the image has no Ghostscript.
   - Model files are pinned by revision, come from the release payload and are checked against its recorded hashes
     when the GPU pack is installed.
   - The gateway checks the client's tailnet address and bearer token before a request reaches the MCP library.
   - The Workstation reaches the Forge through its GPU forwarder, which checks the Forge's HTTPS certificate with
     OpenBSD's own certificate bundle. Pairing and the Ask link pin the certificate of the other end.
3. **Status.** Each statement uses OpenVEX's status and justification terms. Where I could not show that the
   vulnerable code is out of reach, the rule was to mark the advisory affected.

## 3. Result for 0.1.0-alpha.2

| Status | Statements | By severity |
|---|---:|---|
| not_affected | 76 | 2 critical, 30 high, 38 medium, 6 low |
| affected | 0 | |
| fixed | 0 | |
| under_investigation | 0 | |

56 statements use `vulnerable_code_not_in_execute_path` and 20 use
`vulnerable_code_cannot_be_controlled_by_adversary`. The main reasons, by package:

- **vllm 0.28.0** (converter, 33 advisories): every one is in vLLM's HTTP server, its request parameters,
  disaggregated serving, KV-transfer connectors or model families Deep32 does not run. The converter calls vLLM in
  process with fixed prompts and sampling settings, and a PDF can change only the pixels of the page images.
- **sentence-transformers 5.3.0** (builder): the trust check is skipped only when the model is loaded from a local
  folder an attacker can write to. Deep32 names the ColBERT model by its Hugging Face identifier from configuration
  inside the image, the builder is offline, and 0.1.0-alpha.2 doesn't ship that model.
- **certifi 2026.7.22** (builder, Workstation): no connection in Deep32 checks a certificate against certifi's bundle
  while it runs (section 2).
- **aiohttp 3.14.3** (builder): imported by the datasets package for one exception class. Nothing creates an aiohttp
  server or client.
- **filelock 3.32.3** (builder): the attacks need Windows or a second local user who can write to the lock's folder;
  the builder runs on Linux as one unprivileged user in its own container.
- **opentelemetry-api 1.45.0** (Workstation gateway): the MCP library does parse trace headers from every request, so
  this code runs, but only for clients that already passed the gateway's address and token checks. A client with a
  valid token could slow the gateway; it can already read everything the gateway serves. I'm moving the Workstation to
  1.45.2 in its next build.

The test pins in `tests/engine` and `tests/gateway` already use the fixed certifi 2026.10.23 and opentelemetry-api
1.45.2. Several other packages can't move yet: pylate 1.6.0, the newest release, pins sentence-transformers 5.3.0,
transformers 5.3.0 or older and ujson 5.12.0, and MinerU 4.0.7 requires vLLM older than 0.29.0.

## 4. What it does not cover

- The Debian packages inside the images, the base images' own `pip`, and the Qdrant and embedding images, which hold
  no Python packages. OSV-Scanner's pin-file scan doesn't see them; each image's SBOM lists them.
- Native libraries inside Python wheels, such as PDFium in pypdfium2, MuPDF in PyMuPDF and OpenCV. OSV-Scanner
  matched their Python package versions and found nothing, but a vulnerability in the C library isn't always recorded
  against the Python package. These sit on the converter's PDF path, the one place where Deep32 reads untrusted input.
- OpenBSD and its packages on the Workstation.

## 5. Keeping it current

The scan of every pin file in the workflow runs on each push and pull request and once a week, and reports new
findings to the repository's Security tab. The pins outside the images (the CI tools, the Ask relay and the test pins)
are scanned without the exceptions, so a new advisory there fails the workflow. When I rebuild the images or the
Workstation for a release, I redo the triage, publish a new document here named for that release and update
`osv-scanner.toml` to match. Scanners that read OpenVEX can apply the document directly; OSV-Scanner 2.7.1 reads only
its own configuration file, which is why both exist.

---

<p align="center"><sub><a href="../../README.md">Overview</a> · <a href="../../INSTALL.md">Installation</a> · <a href="../HARDWARE.md">Hardware</a> · <a href="../OPTIONAL-FEATURES.md">Optional features</a> · <a href="../tr/README.md">Design</a> · <a href="../DECISIONS.md">Decision record</a> · <a href="../contracts/README.md">Contracts</a> · <a href="../../adapters/README.md">Agent adapters</a> · <a href="../../.github/SECURITY.md">Security</a> · <a href="../../CHANGELOG.md">Release notes</a></sub></p>
