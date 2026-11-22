# Assurance case

Why I believe Deep32 0.1.0-alpha.3 meets its security requirements, with the evidence for each claim. It is for
security reviewers and for anyone deciding whether to deploy Deep32 or build on it.

<sub>Applies to 0.1.0-alpha.3 · [1 Claim](#1-claim-and-scope) · [2 Requirements](#2-security-requirements) · [3 Threat model](#3-threat-model) · [4 Trust boundaries](#4-trust-boundaries) · [5 Design principles](#5-secure-design-principles) · [6 Common weaknesses](#6-common-implementation-weaknesses) · [7 Input validation](#7-input-validation) · [8 Hardening](#8-hardening) · [9 Cryptography](#9-cryptography) · [10 Known limitations](#10-known-limitations-and-residual-risk)</sub>

## 1. Claim and scope

The claim this page argues:

> In a deployment installed from the signed 0.1.0-alpha.3 images and set up as [INSTALL.md](../INSTALL.md) describes,
> only parts that a person has paired can use a Deep32 service; a paired agent can only search and read the corpus;
> no Deep32 service accepts administrative commands over the network, apart from the optional maintenance SSH of
> section 3.3 (E8); and an image or content disc is used only when it matches the signed release metadata.

The argument has four parts, as the OpenSSF Best Practices criteria ask [[OPENSSF]](#references): a threat model
(section 3), the trust boundaries (section 4), how secure design principles are applied (section 5), and how common
implementation weaknesses are countered, using the CWE Top 25 of 2025 (section 6). Sections 7 to 9 cover input
validation, hardening and cryptography in more detail, and section 10 lists what is not countered.

How to read the evidence:

- Paths are relative to the repository root. A path names the file I relied on for the statement next to it. A file
  name on its own, such as `rag_edge.py`, is the file given in full earlier on this page.
- `deep32net/` is the pairing library, `src/workstation/distrib/site/usr/local/d32/deep32net/`. The Forge carries the
  same library in `src/gpu-pack/pairing/deep32net/`; its SPAKE2 and P-256 files are byte-identical.
- The design is specified in the design document [D32-TR-1](tr/README.md); its section numbers are given as "TR 10.3".
  The reasons for each choice are in the [decision record](DECISIONS.md), cited as DR-nn.
- This is the project's own case for itself. Nobody independent of Deep32 has reviewed it yet; my own review against
  it is in the [security review](SECURITY-REVIEW-2026-11.md).

## 2. Security requirements

| ID | Requirement | Where it is specified |
|---|---|---|
| SR-1 | A service answers only a caller that holds a credential made on that service's machine: handed over in an authenticated pairing, or, for the engine, given only to the gateway on the same machine. Being on the tailnet grants nothing | TR 3.4, TR 10.2, DR-68, DR-69 |
| SR-2 | Remote callers can only query. The gateway's eleven tools only read, and no Deep32 service accepts administrative commands over the network. The one exception is the Workstation's maintenance SSH, which is off unless the person turns it on at install (E8 in section 3.3) | TR 7.2, TR 8.2, DR-54, DR-73 |
| SR-3 | No secret ships on an image or in the repository, and no token appears in a log, a status line, an argument list or an agent's configuration file | TR 10.2, TR 14.2, [INSTALL 11](../INSTALL.md#11-what-is-never-stored), DR-78 |
| SR-4 | An image or content disc is trusted only when it matches the release metadata signed with the release key | TR 12, DR-86, DR-90 |
| SR-5 | Traffic between machines is encrypted and the peer is authenticated | TR 10.1, TR 11.1, [C8](contracts/C8-network.md) |
| SR-6 | Document text is untrusted input. It can't make the system run a command, write a file or reach the network | TR 3.4, TR 11.7, TR 14.5, DR-11, DR-74, DR-76 |
| SR-7 | Each component runs with only the privileges it needs, so a defect in one is contained | TR 8.1, TR 14.7, DR-54 |
| SR-8 | A burst of requests can't exhaust a service | TR 6.9, TR 11.4 |

Outside these requirements, and accepted as described in [SECURITY.md](../.github/SECURITY.md#5-known-limitations): the
language model provider sees each question, the passages the agent reads and its reasoning; whoever holds the release
signing key can sign a release; and an administrator of a machine that runs a part controls that part.

## 3. Threat model

### 3.1 Assets

| Asset | Why it matters |
|---|---|
| The integrity of the images and the content | A substituted image would run attacker code as root on the Workstation or the Forge |
| Per-install credentials: engine token, MCP tokens, Forge token, Forge bundle key, link keys and pins | Each grants use of one service |
| The Workstation system and its index | It holds the corpus and answers every search |
| The headend: the user's agent and its account | An agent that obeys planted text could act with the user's own permissions |
| Questions and retrieved passages | They show what the user is working on |

### 3.2 Adversaries

| Adversary | Can | Can't (by design) |
|---|---|---|
| A1. Network attacker on the path between machines | Observe and alter packets on the Internet or the LAN | Read or change tailnet traffic, which is WireGuard between nodes |
| A2. Another device on the same tailnet, including one that is compromised | Connect to any published service or pairing port | Use a service without its token, or pair without a code shown on the responder's console |
| A3. A mirror, or anyone in the download path | Serve any bytes for an image | Make `deep32-get`, the importer or the Forge loader accept bytes that differ from the signed digests |
| A4. The author of a document in the corpus | Put instruction-like text or malformed content into a document | Run commands, write files or reach the network through the gateway's tools |
| A5. An agent that follows planted text | Call the eleven tools with any arguments | Change the index, the configuration or another session through those tools |
| A6. An unprivileged program on a part, such as a desktop program on the Workstation | Run as its own user | Read another service's credential, reach the network outside its firewall rule, or change system files at securelevel 1 |

Out of scope, as listed in [SECURITY.md 3](../.github/SECURITY.md#3-scope): Tailscale, Docker, NVIDIA's drivers and
container toolkit, the language model providers and the documents themselves. Tailscale's coordination service is
trusted to admit only your machines to your tailnet; the services don't rely on tailnet membership for authorization.

### 3.3 Entry points and threats

| Entry point | Threats | Mitigation, with evidence |
|---|---|---|
| E1. MCP gateway, HTTPS on the tailnet (`/mcp` through Tailscale Serve to loopback port 8766) | Unauthenticated use (A2); token guessing; DNS rebinding from a browser; oversized requests | Bearer token checked on every request, comparing SHA-256 digests in constant time (`Guard.authorized` in `src/gateway/rag_edge.py`); tokens shorter than 32 characters are ignored, and with none left every request other than `/health` gets 401 (`parse_tokens`); Host and Origin allow-lists and a 256 KiB body limit (`create_app`); the eleven tools only read, and each is declared read-only to the agent (`read_only_hint=True`) |
| E2. Forge door, HTTPS on the tailnet (Serve to loopback ports 8081 and 8082) | Unauthenticated use of the GPU (A2); request smuggling; oversized bodies | Token checked before anything else, then a fixed path allow-list per port, `Transfer-Encoding` refused, a 4 MiB body limit and at most 16 requests at once (`src/gpu-pack/containers/door/door.py`); ports published on 127.0.0.1 only (`src/gpu-pack/containers/compose.yaml`) |
| E3. Pairing listeners (7453 Forge, 7454 gateway), open only during a window | Offline or online guessing of the code (A2); a peer in the middle | SPAKE2 over P-256, with both certificate pins in the password derivation (`password_scalar` in `deep32net/spake2.py`); one attempt per five-minute window opened at the responder's console; three failed windows in 30 minutes lock pairing for 15 minutes (`deep32net/pairlock.py`); the credential is handed over only after the peer's key confirmation verifies (`deep32net/pairing.py`) |
| E4. Ask link (D32U/1), dialed by the Workstation to the relay | Impersonation of either end (A2); malformed frames | Mutual TLS 1.3 with the peer's whole-certificate SHA-256 pin compared in constant time (`handshake_done` in `src/workstation/node/asklink/src/link.c`; `adapters/relay/deep32_relay/tls.py`); an unknown certificate only while a window armed on that side's console is open, then the ten-word comparison of TR 11.5; frame limits of TR 11.2 and 11.4 (`adapters/relay/deep32_relay/wire.py`) |
| E5. Downloads and imports: `deep32-get`, the Workstation's content import, the Forge loader | Substituted or corrupted images (A3) | Detached OpenPGP signature checked with a pinned fingerprint before any metadata is parsed, then every 64 MiB chunk and the whole image against signed SHA-256 digests (`src/release-tools/deep32_get_manifest.py`, `src/release-tools/deep32_download.py`); content import refuses every member path until the signed manifest verifies (`run_signature` in `src/workstation/distrib/site/usr/local/d32/libexec/d32-import/imp_manifest.py`); the Forge loader checks every selected image archive against its signed identity before it loads any (`images` in `src/gpu-pack/forms/docker_role.py`), and containers start only from immutable image IDs with `pull_policy: never` (`compose.yaml`) |
| E6. Document text, at ingest and in tool output | Prompt injection (A4, A5) | Conversion without network access and a scan for instruction-like lines, invisible characters and control bytes (DR-11; `network_mode: none` for the converter in `compose.yaml`); the gateway's server instructions say tool output is document text (`INSTRUCTIONS` in `src/gateway/rag_edge.py`, DR-74); an Ask session runs the agent in the box of TR 11.7 (`adapters/relay/deep32_relay/native.py`). Residual risk in section 10 |
| E7. Local console and desktop on the Workstation | A desktop program gaining root or network access (A6) | `doas` permits fixed commands only (`src/workstation/ship/u4/etc/doas.conf`); `pf` blocks network access per user (`src/workstation/ship/u4/etc/pf.conf`); securelevel 1 and immutable system files (section 8.1) |
| E8. Optional maintenance SSH on the Workstation | Remote root login | Off unless an Ed25519 public key is supplied at install. When on, a separate `sshd` accepts that one key, for `root` only from the paired peer's tailnet address, with passwords, keyboard-interactive login and forwarding off, and `pf` admits only that address through its inbound tables (`maint_open` in `src/workstation/ship/u4/usr/local/d32/sbin/d32-link`; `d32_maint_key_ok` in `src/workstation/distrib/site/install.site`) |

## 4. Trust boundaries

<details>
<summary><b>The deployment and its boundaries, as text</b></summary>

```text
 Internet ............ TB-1 ............ tailnet (WireGuard, your account)
                                             |
   Headend            TB-4                   |  TB-2: Serve route -> loopback, token checked
   agent, relay  ---- MCP token ------> Workstation gateway (loopback 8766)
   (TB-9 creds)  <=== D32U/1 mTLS ===== Workstation link process (dials out)
                                             |  TB-8: separate users, pledge/unveil, pf per user
                                        Workstation engine (UNIX socket, engine token)
                                             |  TB-5: HTTPS, CA-verified, Forge token
                                        Forge door (loopback 8081/8082)
                                             |  TB-8: internal Docker network
                                        embedder, reranker containers
 Mirrors ..... TB-6 (signed metadata) ..... every installed image and content disc
 Documents ... TB-7 (untrusted text) ...... the agent and the model
```

</details>

| Boundary | Between | What is enforced there | Evidence |
|---|---|---|---|
| TB-1 | The Internet and any part | Machines talk only over the tailnet. On the Workstation, `pf` blocks everything, then permits outbound traffic per user and per destination table, and inbound traffic only inside a window armed at the console or for the maintenance SSH of E8; no Deep32 service listens on a network address. Tailscale Funnel is refused, so nothing is published to the Internet | `src/workstation/ship/u4/etc/pf.conf`; Funnel check in `src/workstation/distrib/site/usr/local/d32/publication.py` and `src/gpu-pack/forms/forge_publication.py`; TR 14.4 |
| TB-2 | The tailnet and a service | Services listen on loopback (the engine on a UNIX socket) and are reached through Tailscale Serve over HTTPS. Each service checks its own token | [C8 Nodes and ports](contracts/C8-network.md#nodes-and-ports); `Guard` in `src/gateway/rag_edge.py`; `door.py` |
| TB-3 | Unpaired and paired machines | A pairing window is opened by a person at the responder's console, allows one attempt, and lasts five minutes; SPAKE2 authenticates both certificates against the code | TR 10.3; `src/workstation/distrib/site/usr/local/d32/deep32net/` (`pairing.py`, `spake2.py`, `window.py`, `pairlock.py`) |
| TB-4 | The headend's agent and the gateway | One MCP token per paired agent; eleven read-only tools; in an Ask session, a per-session proxy on the relay with its own random bearer, forwarding only during a verified turn | TR 7.2, TR 11.6; `adapters/relay/deep32_relay/proxy.py` |
| TB-5 | The Workstation and the Forge | The Forge token travels only over HTTPS whose certificate is checked against the CA file and the Forge's tailnet name; the Forge's models are identified by probe before any request is forwarded, and again on a timer | `src/workstation/node/shims/py/serve_accel.py` |
| TB-6 | Mirrors and the installed system | Signed metadata, chunk and whole-image digests; signed content manifest; signed Workstation update sets | TR 12; `src/release-tools/deep32_get_manifest.py`; `imp_manifest.py`; `src/workstation/ship/u2/usr/local/d32/sbin/d32upd` |
| TB-7 | Document text and the agent | Text is data. The agent is told so, and an Ask session's agent has no tool other than the eleven | TR 14.5; `native.py` |
| TB-8 | Services on one host | Workstation: one system user per service, each with no login shell, its own `pledge` promises and `unveil` paths, its own `pf` rules and, where it has one, its own credential file. Forge: containers with no capabilities and no privilege gain; the model containers have no published port and sit on an internal Docker network | `src/workstation/distrib/ids/d32-ids.tsv`; `src/workstation/node/shims/py/d32svc.py`; `compose.yaml` |
| TB-9 | The headend's credentials and other software | The agent's MCP token is kept in DPAPI for the current user on Windows, or a mode 0600 file in a mode 0700 directory elsewhere, and is never written to an MCP configuration file | `PrivateStore` and `atomic` in `adapters/connect.py`; DR-78 |

## 5. Secure design principles

Saltzer and Schroeder's principles [[SALTZER]](#references), plus the two that the OpenSSF criteria add.

| Principle | How Deep32 applies it | Evidence |
|---|---|---|
| Economy of mechanism | One authenticated entrance per machine (the gateway on the Workstation, the door on the Forge). One pairing protocol for both Forge and agent enrollment. One OpenPGP signature check for every release's metadata. The gateway is stateless and holds no documents | TR 7.1, TR 9.1, TR 10.3, TR 12.2; `src/gateway/rag_edge.py`; `door.py` |
| Fail-safe defaults | `pf` starts from `block log all`. The engine refuses to start without a token of at least 32 characters, and the gateway answers 401 to every request other than `/health` when it has none. A malformed address allow-list stops the gateway at start. Features that contact anything ship off. Nothing in release metadata is read before the signature passes. The image downloader refuses an HTTPS-to-HTTP redirect | `pf.conf`; `_expected_token` in `src/engine/src/rag_core/api.py`; `parse_tokens` and `parse_networks` in `rag_edge.py`; [Optional features 1](OPTIONAL-FEATURES.md#1-defaults); `deep32_get_manifest.py`; `_open_response` in `deep32_download.py` |
| Complete mediation | Every request is checked: the gateway's `Guard` wraps the whole application (only `/health` is public, and Serve publishes only `/mcp`); the door checks the token before the path; every engine route depends on `require_auth`; the relay's proxy checks host and bearer on every call; the Ask link checks the pin on every connection; the Forge forwarder repeats its model identity check on a timer | `rag_edge.py`; `door.py`; `api.py`; `proxy.py`; `link.c`; `serve_accel.py` |
| Open design | The design, the protocols and the source are public. Security rests only on per-install keys and tokens, and on published algorithms: SPAKE2 [[RFC9382]](#references), TLS 1.3 [[RFC8446]](#references), OpenPGP [[RFC9580]](#references) | [D32-TR-1](tr/README.md); [contracts](contracts/README.md) |
| Separation of privilege | Pairing needs a person at the responder's console to open the window and the code typed on the other machine. Ask pairing needs the words typed on both consoles. A release image is accepted only with a valid signature and a matching pinned fingerprint. Changing a system file on the Workstation needs root and a boot into the maintenance window | TR 10.3, TR 11.5, TR 12.2, TR 8.2 |
| Least privilege | Each Workstation service runs as its own user with `/sbin/nologin`, and drops to its `pledge` promises once started (the gateway: `stdio rpath unix prot_exec`, plus `inet` only when it serves TCP). The Library's renderer runs with `stdio recvfd` and no visible path. `doas` grants fixed commands. Containers drop every capability. The agent in an Ask session has only the eleven tools | `src/workstation/node/shims/py/serve_edge.py`, `src/workstation/node/shims/py/serve_core.py`, `serve_accel.py`; `src/workstation/library/src/render/main.c`; `doas.conf`; `compose.yaml`; `native.py` |
| Least common mechanism | One MCP token per agent, each revocable on its own. One random bearer per Ask session. One credential file per service, readable only by that service's group. The two relay links run as two different users. The Library is kept apart from the corpus and its indexes | [C8 Tokens and keys](contracts/C8-network.md#tokens-and-keys); `proxy.py`; `pf.conf`; DR-19 |
| Psychological acceptability | Pairing is a ten-symbol code typed once. Ask pairing has the person type the other side's words, so a mismatch can't be approved by a glance. `deep32-get` verifies without extra steps. The setup script tags each line `[ok]`, `[fix]`, `[you]` or `[fail]` | TR 10.3, TR 14.3; `install/deep32-setup.sh` |
| Limited attack surface | Only Serve routes and pairing windows reach a service from the tailnet. The Workstation's web browser has no route to the Internet unless the web switch is turned on at the console | [C8](contracts/C8-network.md); `pf.conf`; [Optional features 2.1](OPTIONAL-FEATURES.md#21-web-switch) |
| Input validation with allowlists | Every untrusted input is checked against what is allowed (section 7) | Section 7 |

## 6. Common implementation weaknesses

The 2025 CWE Top 25 [[CWE-TOP25]](#references), each with how it is countered in Deep32 or why it doesn't apply.
"Partly" means a countermeasure is in place and a gap remains, named in the row.

### 6.1 Memory safety

The gateway, the engine, pairing, the relay, the importer and the getter are written in Python. The C code is under
`src/workstation` (disc 1): the Library, the Ask client, the Ask link (the one C program that talks to another part),
the vector kernel and the desktop tools. Parts of that C code read documents, media and network input.

| CWE | Status and how | Evidence |
|---|---|---|
| CWE-787 Out-of-bounds write · CWE-125 Out-of-bounds read · CWE-120 Classic buffer overflow · CWE-121 Stack-based buffer overflow · CWE-122 Heap-based buffer overflow | **Partly.** C is compiled as C99 or GNU C99 with `-Wall -Wextra -Werror`; most programs add `-fstack-protector-strong`, and the Ask link also `-Wformat=2 -Werror=format-nonliteral -Wshadow`. The Ask link checks each frame's length against fixed limits before it reads the payload (`src/workstation/node/asklink/src/frame.c`). OpenBSD enforces W^X and address space randomization. `pledge` and `unveil` limit what a compromised parser can do: the Library's renderer runs with `stdio recvfd` and sees no path, and the link process runs chrooted in `/var/empty` with `stdio inet`. A Flawfinder pass over all the C, with every hit at levels 4 and 5 read, found nothing exploitable ([security review 5.2](SECURITY-REVIEW-2026-11.md#52-c)). Fuzz targets built with AddressSanitizer and UndefinedBehaviorSanitizer cover the C parsers that build without the window system. **Gap:** on 19 November 2026 they found a read of up to four bytes past a buffer in the Library's JSON reader and a write past the sample buffer in its WAVE reader, which no program in this release calls; both are rated low and fixed in 0.1.0-alpha.3 (section 10). The rest of the C has no fuzz target yet, and `src/workstation/pyrag/d32log/build-d32log.sh` compiles without warning flags | `tests/fuzz/README.md`; `src/workstation/node/asklink/Makefile`; `src/workstation/ship/u10/src/*/Makefile`; `src/workstation/library/Makefile`; `link.c`; `src/workstation/library/src/render/main.c` |
| CWE-416 Use after free | **Partly.** As above. Python code has no manual memory management | as above |
| CWE-476 NULL pointer dereference | **Partly.** As above; on OpenBSD page zero is unmapped, so a null dereference ends the one process | as above |

### 6.2 Injection and unsafe processing

| CWE | Status and how | Evidence |
|---|---|---|
| CWE-79 Cross-site scripting | **Not applicable in the main path.** No Deep32 service serves web pages to a browser: the gateway returns JSON to an agent, and the Library and Ask windows are native programs. The one generated HTML page, the Library's start page, escapes every value with `html.escape`. The encyclopedia reaches the Workstation's browser through the packaged `kiwix-serve`, on 127.0.0.1 only, from the signed content disc, confined by `pledge` and `unveil` | `src/workstation/ship/tools/mkindex.py`; `src/workstation/ship/u2/src/kiwixjail/d32-kiwix-jail.c` |
| CWE-89 SQL injection | **Countered.** Values reach SQLite through `?` placeholders, including full-text queries (`MATCH ?`); table names are constants or are read from the database's own `sqlite_master`; Python's `sqlite3` runs one statement per call; bundles are checked through read-only, immutable connections. One inconsistency in identifier quoting is listed in section 10 | `src/engine/src/rag_core/retrievers/lexical.py`, `src/engine/src/rag_core/reader.py`; `sqlite_ro` in `src/kb-tools/kb-bundle/kb_bundle.py` |
| CWE-78 OS command injection · CWE-77 Command injection | **Countered.** No shipped Python passes `shell=True` or calls `os.system`; programs start with argument lists, and the GnuPG path is resolved to a file and checked to be GnuPG. Shell scripts check their inputs against fixed patterns before use, for example the maintenance key and tailnet addresses | `gpg_executable` in `src/release-tools/deep32_get_manifest.py`; `d32_maint_key_ok` in `src/workstation/distrib/site/install.site`; `d32-link` |
| CWE-94 Code injection | **Countered.** No `eval` or `exec` of input. The Workstation's Python services run with `-I -B` and a rebuilt environment. The reranker loads its model code from the pinned revision in the GPU pack, offline (section 10) | `d32svc.py`; `src/gpu-pack/containers/reranker/reranker_api.py` |
| CWE-502 Deserialization of untrusted data | **Countered.** No `pickle`, `marshal`, `yaml.load` or `torch.load` in Deep32's own code. Protocol and metadata JSON is parsed strictly (UTF-8 only, no byte order mark, no duplicate keys, finite numbers only, and in pairing messages nesting at most 32 deep). The embedding and reranking models are `safetensors` files checked by digest; every file of the GPU pack, models included, is covered by the signed digests, and the converter's models are loaded by MinerU, a third-party program, in a container with no network access | `loads_strict` in `deep32net/wire.py`; `strict_loads` in `src/release-tools/contract_validate.py`; `src/gpu-pack/build/validate.py` |
| CWE-22 Path traversal | **Countered.** Paths from metadata and manifests are checked against allowlists (no absolute paths, no `.` or `..` parts, no reserved Windows names, fixed character sets) and opened with `O_NOFOLLOW`; private state refuses symbolic links and junctions anywhere in its path | `safe_path` in `contract_validate.py`; `public_path` in `src/release-tools/sbom/common.py`; `safe_member` in `imp_manifest.py`; `open_regular` in `src/workstation/distrib/site/usr/local/d32/libexec/d32-import/imp_fs.py`; `safe` in `deep32net/files.py` |
| CWE-434 Unrestricted upload of dangerous file type | **Not applicable.** No service accepts uploaded files. Content enters a Workstation only from a signed content disc | `imp_manifest.py` |
| CWE-918 Server-side request forgery | **Countered.** The gateway calls only its configured engine, and no tool argument is an address: they are queries and document keys. The door forwards only to two fixed containers and fixed paths. Download addresses come only from signed metadata, and `safe_url` refuses credentials, queries, fragments and non-public hosts and addresses; each redirect is checked the same way, at most five are followed, and a redirect from HTTPS to HTTP is refused. The Forge forwarder connects only to the Forge it was paired with: model addresses handed over at pairing must be HTTPS `*.ts.net` URLs on that Forge's name, and the test-only loopback transport accepts only 127.0.0.1 | `rag_edge.py`; `door.py`; `safe_url` in `contract_validate.py`; `_open_response` in `deep32_download.py`; `serve_accel.py` |

### 6.3 Access control and authentication

| CWE | Status and how | Evidence |
|---|---|---|
| CWE-862 Missing authorization · CWE-863 Incorrect authorization · CWE-284 Improper access control | **Countered.** Authorization is the token check itself: a holder of a gateway token may call the eleven read-only tools, and nothing else is exposed. Administration is local only, through `doas` rules for fixed commands. File access is separated by user and group | `rag_edge.py`; `doas.conf`; [C8 shared credential parent](contracts/C8-network.md#shared-workstation-credential-parent-d03-amendment-2026-11-17) |
| CWE-306 Missing authentication for critical function | **Countered.** The engine, the gateway, the door, the relay's per-session proxy and pairing each authenticate the caller. The model containers on the Forge check no token of their own; they have no published port and are reachable only on the Forge host (section 10) | `api.py`; `rag_edge.py`; `door.py`; `proxy.py`; `compose.yaml` |
| CWE-639 Authorization bypass through user-controlled key | **Not applicable.** There are no per-user objects: every paired agent reads the same corpus. A token can't be used to reach another agent's session, because each Ask session has its own bearer | `proxy.py` |
| CWE-352 Cross-site request forgery | **Countered.** Credentials are bearer tokens in the `Authorization` header, which a browser does not attach on its own; no service uses cookies. The gateway also refuses requests whose `Host` is not its tailnet name or a loopback name, or whose `Origin` is not its own HTTPS address | `create_app` in `rag_edge.py` |

### 6.4 Input, information and resources

| CWE | Status and how | Evidence |
|---|---|---|
| CWE-20 Improper input validation | **Countered**, with the gap in 6.1 for the C readers. See section 7 | Section 7 |
| CWE-200 Exposure of sensitive information | **Countered for credentials.** Tokens never appear in logs, status lines, argument lists or agent configuration files; the door logs no requests; the gateway escapes and truncates the request path it logs; its error messages carry no internal addresses or tokens. Questions and passages go to the model provider, and machine names to the Certificate Transparency logs, by design (section 10) | `rag_edge.py`; `door.py`; `adapters/connect.py`; TR 10.2 |
| CWE-770 Allocation of resources without limits | **Countered.** Search admission with a bounded queue; a 3-second budget for `grep_corpus`; typed and bounded tool arguments; body limits on the gateway (256 KiB) and the door (4 MiB, 16 requests at once); D32U/1 limits on frame size, credit and frames per second; pairing lockout; per-service memory limits on the Workstation (login classes) and the Forge (`mem_limit`); size limits on downloaded metadata | `api.py`, `src/engine/src/rag_core/api_v2.py`; `rag_edge.py`; `door.py`; TR 11.4; `src/workstation/distrib/site/etc/login.conf.d/`; `compose.yaml`; `deep32_get_manifest.py` |

## 7. Input validation

Every input from a source I don't control is checked against what is allowed, and anything else is refused.

| Input | Source | Allowlist check | Evidence |
|---|---|---|---|
| MCP tool arguments | A paired agent | Typed fields with length and range limits (query text up to 2,000 characters, document keys up to 200, at most 20 keys, 1 to 30 passages, 200 to 4,000 characters each) checked before the engine is called | Type definitions near `build_server` in `src/gateway/rag_edge.py` |
| HTTP requests | Any tailnet peer | Host and Origin allow-lists, 256 KiB body; on the Forge, a fixed path list per port, `Content-Length` from 0 to 4 MiB, no `Transfer-Encoding` | `create_app` in `rag_edge.py`; `door.py` |
| Pairing messages and codes | The peer in a pairing | Frame header check; strict JSON with exact key sets; certificates checked as a single DER structure of bounded size; the Forge's bundle key must be an Ed25519 public key; codes normalized and then matched to `[A-Z2-7]{10}` | `deep32net/wire.py`; `deep32net/codes.py` |
| D32U/1 frames | The Ask link peer | Payload length at most 1,048,576 bytes and control frames at most 16,384; frame types 1 to 13; flags only on DATA; strict JSON | TR 11.2, TR 11.3; `adapters/relay/deep32_relay/wire.py`; `src/workstation/node/asklink/src/frame.c` |
| Release metadata | GitHub and mirrors | Signature first; then JSON schemas with closed key sets, strict JSON, and the path and URL checks of section 6.2. Invalid examples are kept as test fixtures | `contract_validate.py`; `src/release-tools/contracts/fixtures/invalid/` |
| Content disc manifest | The content disc | Signature first; exact key sets; safe member paths and versions; every file copied with its size and SHA-256 checked | `imp_manifest.py`; `run_files` in `imp_files.py` |
| Settings and install inputs | The person installing | Fixed patterns, for example the maintenance key and tailnet addresses | `install.site`; `d32-link` |
| Agent endpoint | The person, in Connect | Must be an `https://` URL ending in `/mcp`, with no user name, query or fragment; redirects refused | `endpoint` and `NoRedirect` in `adapters/connect.py` |

The Library's C readers parse content from the signed content disc. Their checks are in the code, and the readers
that build without the window system have fuzz targets ([tests/fuzz](../tests/fuzz/README.md)); what the fuzzing
found is in section 10.

## 8. Hardening

### 8.1 Workstation (OpenBSD)

- **Kernel and system.** Securelevel 1, with the files in `/etc/d32/schg.list` set immutable at install, so they can be
  changed only in a maintenance boot (`src/workstation/distrib/site/etc/d32/site.d/95-schg.sh`,
  `src/workstation/ship/u2/etc/d32/ready.conf`). `sysctl.conf` turns off the kernel debugger, `/dev/kmem`, `dt(4)`
  tracing, ptrace of any process other than one's own children, core files from set-id programs, the aperture driver
  and IP forwarding
  (`src/workstation/distrib/site/etc/sysctl.conf`). System libraries are relinked in random order at boot
  (`library_aslr=YES` in `src/workstation/ship/site/etc/d32/site.d/75-ship-desktop.sh`).
- **Firewall.** `pf` blocks everything by default, drops traffic that fails reverse-path checks, and permits outbound
  traffic per user and per destination table; the engine's and gateway's users have no network access off the machine
  (`src/workstation/ship/u4/etc/pf.conf`).
- **Privilege separation.** Each service has its own user with no login shell
  (`src/workstation/distrib/ids/d32-ids.tsv`), a login class with a memory limit and no core dumps (the engine, the
  gateway and the Forge forwarder each have one of their own; `src/workstation/distrib/site/etc/login.conf.d/`), and
  its own credential file (C8). The engine opens its database immutable (`RAG_DB_IMMUTABLE=1` in
  `src/workstation/distrib/site/etc/login.conf.d/d32core`).
- **pledge and unveil.** 26 source files under `src/workstation` use `pledge` and 13 use `unveil`: the Library, its
  renderer, the Ask link, the desktop tools, and the engine, gateway and Forge forwarder through
  `src/workstation/node/shims/py/pledge.py` and `d32svc.py`. The Python services rebuild their environment, refuse to
  run as root, and lock `unveil` and `pledge` once started.
- **Compiler.** `-Werror` with `-Wall -Wextra` in every Makefile under `src/workstation`;
  `-fstack-protector-strong` in the `src/workstation/ship/u10` programs and the Ask link. W^X and
  position-independent executables come from OpenBSD itself.

### 8.2 Forge (Docker)

From `src/gpu-pack/containers/compose.yaml`: every container has `cap_drop: [ALL]` and `no-new-privileges`; the door
and the status container have read-only root file systems; the models are mounted read-only; the converter and the
status container run with `network_mode: none`; the embedder and reranker sit on an `internal` network; the door
publishes only on 127.0.0.1; the Forge token is a Docker secret; images are referenced by immutable ID with
`pull_policy: never`; the embedder and the reranker have memory limits. No container gets the Docker socket
(`src/gpu-pack/forms/docker_role.py`).

### 8.3 Headend

The relay starts Claude Code with its built-in tools off, only the eleven tools allowed, one MCP server under strict
configuration and no settings layers, and Codex with a read-only sandbox, approvals set to never and web search off;
it checks the session's reported configuration before the first turn (`adapters/relay/deep32_relay/native.py`).
On Windows a job object ends every process an agent starts (SECURITY.md 5 describes Linux and macOS).

### 8.4 What is not hardened yet

- The `d32log` build has no warning flags, and the Library builds
  `src/workstation/library/src/import/kwaj.c` with `-Wno-pedantic`. Warnings as errors for every C program are
  listed for 0.1.0-alpha.4 ([roadmap 3.3](ROADMAP.md#3-010-alpha3)).
- ShellCheck and PSScriptAnalyzer have not been run on the scripts. Fixing their findings in the image source is
  listed for 0.1.0-alpha.4 too.
- Fuzzing with the sanitizers covers the C parsers that build without the window system. The Library's interface
  and renderer, the Ask link's TLS and process code and the desktop tools have no fuzz targets yet, and the C unit
  tests have not run under the sanitizers ([roadmap](ROADMAP.md#52-dynamic-analysis)).

HTTP security headers such as a Content Security Policy don't apply: no Deep32 service serves pages to a browser.

## 9. Cryptography

### 9.1 Where it is used

| Use | Algorithms | Implementation | Evidence |
|---|---|---|---|
| Transport between machines | WireGuard | Tailscale's client | TR 10.1 |
| Gateway and Forge over Serve | HTTPS with a Let's Encrypt certificate for the machine's tailnet name | Tailscale Serve; Python `ssl` on the Workstation's side of the Forge link | TR 10.1; `serve_accel.py` |
| Pairing | TLS 1.3; SPAKE2 on P-256; PBKDF2-HMAC-SHA256 with 600,000 iterations over the code, both pins and both nonces; HMAC-SHA256 key confirmation; self-signed ECDSA P-256 certificates; SHA-256 pins | Python `ssl`, `hashlib`, `hmac`, `secrets`, OpenSSL for key generation; SPAKE2 and P-256 arithmetic in Deep32's own code | `deep32net/spake2.py`, `p256.py`, `identity.py`, `pairing.py`; `tests/pairing/reference/vectors/pairing-v2-vectors.json` |
| Ask link | Mutual TLS 1.3; ECDSA P-256 certificates; SHA-256 pins; the ten words from SHA-256 over both pins and both nonces (110 bits) | LibreSSL `libtls` on the Workstation; pyOpenSSL on the relay | `link.c`; `adapters/relay/deep32_relay/tls.py`; TR 11.5 |
| Tokens, codes and nonces | 32 random bytes as 64 hex digits; codes of 50 bits | Python `secrets`; `openssl rand` (LibreSSL) for the tokens the Workstation makes at first boot; `arc4random_buf` for the Ask link's nonces | `deep32net/tokens.py`, `codes.py`; `d32-firstboot`; `link.c` |
| Release and content signatures | OpenPGP Ed25519 detached, armored, binary-mode signatures over SHA-256 checksum lists and the content manifest; signify (Ed25519) for disc 1 and Workstation update sets; the Forge's bundle key is Ed25519 | GnuPG; OpenBSD `signify` | `deep32_get_manifest.py`; `imp_manifest.py`; `src/workstation/release/mkimage.sh`; `d32upd`; `deep32net/wire.py` (`kb_public`) |

### 9.2 Criteria

| Criterion | Status | Argument and evidence |
|---|---|---|
| crypto_weaknesses | Met | No default security mechanism depends on SHA-1, MD5 or a CBC-mode suite. SHA-1 appears only where an outside format defines it (OpenPGP v4 fingerprints in `src/release-tools/openpgp_pub.py`, git blob IDs when model files are resolved in `src/gpu-pack/build/fetch-inputs.py`), and those files are also pinned by SHA-256. The build tools refuse a package index that offers only MD5 or SHA-1 (`fetch-inputs.py`). MD5 appears only in the GPU pack's media checksum file, with `usedforsecurity=False` (`src/gpu-pack/build/media.py`) |
| crypto_algorithm_agility | Unmet (SHOULD) | TLS negotiates its cipher suite, and the release verifier accepts any key the user names with `--key` and `--fingerprint`. Pairing (SPAKE2 on P-256), the Ask link's words and the release signatures (one Ed25519 key) each have one algorithm. If one is broken, the replacement is a new protocol version, which the HELLO's `versions` and `features` fields allow for, or a new key, in a new release |
| crypto_credential_agility | Met | Every credential that crosses between machines is its own protected file, apart from configuration, databases and logs: on the Workstation `/etc/d32/private/forge.token` and `/etc/d32/private/mcp-tokens`, which the gateway insists on (`serve_edge.py` refuses to start with a token in its environment file), on the Forge `/var/lib/deep32-forge/private/forge-token`, the link keys and pins in each part's private state directory, and on the headend the protected store of TB-9. Pairing again replaces them; no rebuild is involved. The release key the verifier trusts is a file (`install/release-key.gpg`), and `deep32-get` takes `--key` and `--fingerprint`. The engine token, which never leaves the Workstation, is made at first boot and then moved, as the MCP tokens are, into a protected file for each service that reads it: `/etc/d32/private/engine-api.token` for the engine and `/etc/d32/private/engine-client.token` for the gateway (root, the service's group, mode 0440), leaving no token in their environment files (`d32-firstboot`, `d32-gateway-enrol`). Until 0.1.0-alpha.3 it sat in those environment files beside their other settings. |
| crypto_used_network | Met, with a justification | Traffic between machines is WireGuard, and every Deep32 protocol on top of it is HTTPS or TLS 1.3. Plain HTTP is used only on loopback, on UNIX sockets, on the Forge's internal Docker network inside one host, and with the test-only `transport=loopback` switch (TR 10.4). `deep32-get` would accept an `http` mirror if the signed mirror list named one; the first mirror must be the Internet Archive over HTTPS (`_mirrors` in `contract_validate.py`), every byte is checked against signed digests, and the image downloader refuses a redirect from HTTPS to HTTP (`_open_response` in `deep32_download.py`) |
| crypto_tls12 | Met | Every TLS connection Deep32 configures uses TLS 1.2 or later. Pairing and the relay allow TLS 1.3 only (`tls_context` in `deep32net/pairing.py`; `pinned_context` and the pyOpenSSL provider in `tls.py`). The Workstation's link client offers TLS 1.3 unless the operator sets its `tls12` option, and the relay accepts only 1.3. The Forge forwarder sets 1.2 as its minimum (`serve_accel.py`). Downloads of this release, metadata and images, use Python's default context (`deep32_download.py`), whose minimum has been TLS 1.2 since Python 3.10. Only the getter scripts' path for 0.1.0-alpha.1 still uses curl and `Invoke-WebRequest` with their own defaults, against GitHub, which accepts only TLS 1.2 or later |
| crypto_certificate_verification | Met, with a justification | Certificates are verified by default everywhere. The getter (`create_default_context` in `deep32_download.py`, curl and `Invoke-WebRequest` with their defaults in `src/release-tools/deep32-get.sh` and `src/release-tools/deep32-get.ps1`), Connect (`urllib` defaults in `adapters/connect.py`) and the Forge forwarder (CA file and host name in `serve_accel.py`) use CA verification. The Ask link and the relay verify the peer against its pinned certificate, the SHA-256 of the whole certificate, and use no CA: the relay with `CERT_REQUIRED`, the Workstation by comparing the pin after the handshake and before any message, with `libtls`'s chain, name and expiry checks off on purpose (`link.c`, TR 11.1). The pairing helper's TLS uses `CERT_NONE` on purpose: no CA could know these certificates yet, so both certificate pins go into the SPAKE2 password scalar, and a certificate that doesn't match fails the key confirmation; the pins are then stored for every later connection |
| crypto_verification_private | Met | A token is sent only after the peer is verified. The Forge token goes in the `Authorization` header only on a connection whose certificate and host name were checked (`serve_accel.py`); Connect sends the agent's token only over verified HTTPS and refuses redirects (`adapters/connect.py`); Claude Code and Codex reach the gateway with their own HTTPS clients, and Connect writes only the URL and a reference to the token's environment variable into their configuration, with no TLS option (`claude_merge` and `codex_merge` in `adapters/connect.py`). In pairing, the responder sends the token only after the joiner's key confirmation verifies (`pairing.py`). The engine token never leaves the Workstation, and the relay's per-session bearer never leaves 127.0.0.1 |

## 10. Known limitations and residual risk

The first five are also in [SECURITY.md 5](../.github/SECURITY.md#5-known-limitations).

| Limitation | What limits it |
|---|---|
| The language model provider sees each question, the passages the agent reads and the agent's reasoning | The corpus and the index stay on your machines; a passage leaves only when a search returns it |
| HTTPS certificates for Tailscale Serve put machine names in the public Certificate Transparency logs [[RFC9162]](#references) | Default machine names carry nothing personal, and publication asks for agreement once (TR 10.1) |
| Documents can carry text written to steer a model, and a model can follow it | The ingest scan and the gateway's instructions reduce it. An Ask session can only search and read. An agent you connect directly keeps its own tools; I recommend a [separate agent profile](../adapters/README.md#3-use-a-separate-profile) for it |
| On Linux and macOS the relay ends an agent by ending its process group, so a process that left the group could outlive cancellation | The agents run with their built-in tools off and start no tool processes |
| Whoever holds the release signing key can sign a release; I hold it alone, and there is no published procedure for rotating or revoking it | The fingerprint is pinned in the repository, the README and keys.openpgp.org |
| SPAKE2 and P-256 are Deep32's own Python code, which is not constant-time, as the header of `deep32net/p256.py` says | Checked against the RFC 9382 test vectors (`tests/pairing/reference/vectors/pairing-v2-vectors.json`); one online attempt per five-minute window, inside TLS 1.3, with the lockout of section 3.3 |
| Tokens don't expire; they rotate only by pairing again or revoking | From 0.1.0-alpha.3, revoking a paired Workstation on the Forge with `deep32-pair unpair` rotates the Forge's token and the Forge restarts with it ([INSTALL 10](../INSTALL.md#10-afterwards)); before, the Forge then refused to restart |
| The model containers on the Forge check no token, and membership in the Forge's `docker` group is equivalent to root | They have no published port; people with access to the Forge host are inside its trust boundary (TR 14.7) |
| The reranker loads model code shipped with the model (`trust_remote_code`) | It loads only the pinned revision from the GPU pack, offline, in a container with no capabilities (`reranker_api.py`, `compose.yaml`) |
| Fuzzing found five low-severity defects in the Library's readers: a read of up to four bytes past the text in the JSON reader, a loop that never returns on one page-break layout in the RFC text reader, and in the WAVE reader a write past the sample buffer and two kinds of undefined behavior ([fuzzing findings](../tests/fuzz/README.md#5-findings)) | Fixed in 0.1.0-alpha.3 ([roadmap](ROADMAP.md#3-010-alpha3)). Before that, no program called the WAVE reader, the JSON over-read needed a damaged taxonomy file, a local file the Library reads at start, and the RFC text came from the signed content disc |
| Parts of the C that read input have no fuzz target yet: the Library's interface and renderer, the Ask link's TLS and process code, and the desktop tools | The compiler and platform measures of section 8.1. The plan is in the [roadmap](ROADMAP.md#52-dynamic-analysis) |
| The pattern in the converter's injection scanner writes the invisible characters it looks for (zero-width characters and bidirectional controls) raw, which a review for hidden source text has to rule out (`src/gpu-pack/containers/converter-agpl/production-converter/chain/pilot/vendor-add/scan_injection.py`, line 35) | Listed for 0.1.0-alpha.4, when the converter image is rebuilt ([roadmap](ROADMAP.md#3-010-alpha3)) |
| Tailscale is the only transport between machines | A LAN-only mode is [under consideration](ROADMAP.md#6-under-consideration) |
| I haven't shown yet that every image builds to the same bytes twice | Each image's provenance record names its source revision ([Overview 10](../README.md#10-security-considerations)) |
| A Docker Desktop Forge, with its pairing helper on Windows, and a Hyper-V Workstation have not been run end to end | TR, Implementation Status |

This page describes 0.1.0-alpha.3. I'll revise it in any release that changes a trust boundary, a credential or a
protocol.

## References

- **[CWE-TOP25]** MITRE, "2025 CWE Top 25 Most Dangerous Software Weaknesses",
  <https://cwe.mitre.org/top25/archive/2025/2025_cwe_top25.html>.
- **[OPENSSF]** OpenSSF Best Practices Badge, "Silver and Gold criteria",
  <https://www.bestpractices.dev/en/criteria/1>, <https://www.bestpractices.dev/en/criteria/2>.
- **[RFC8446]** Rescorla, E., "The Transport Layer Security (TLS) Protocol Version 1.3", RFC 8446,
  DOI 10.17487/RFC8446, August 2018, <https://www.rfc-editor.org/info/rfc8446>.
- **[RFC9162]** Laurie, B., Messeri, E., and R. Stradling, "Certificate Transparency Version 2.0", RFC 9162,
  DOI 10.17487/RFC9162, December 2021, <https://www.rfc-editor.org/info/rfc9162>.
- **[RFC9382]** Ladd, W., "SPAKE2, a Password-Authenticated Key Exchange", RFC 9382, DOI 10.17487/RFC9382,
  September 2023, <https://www.rfc-editor.org/info/rfc9382>.
- **[RFC9580]** Wouters, P., Ed., Huigens, D., Winter, J., and Y. Niibe, "OpenPGP", RFC 9580,
  DOI 10.17487/RFC9580, July 2024, <https://www.rfc-editor.org/info/rfc9580>.
- **[SALTZER]** Saltzer, J. H. and M. D. Schroeder, "The Protection of Information in Computer Systems",
  Proceedings of the IEEE 63(9), pp. 1278-1308, DOI 10.1109/PROC.1975.9939, September 1975.

---

<p align="center"><sub><a href="../README.md">Overview</a> · <a href="../INSTALL.md">Installation</a> · <a href="HARDWARE.md">Hardware</a> · <a href="OPTIONAL-FEATURES.md">Optional features</a> · <a href="tr/README.md">Design</a> · <a href="DECISIONS.md">Decision record</a> · <a href="contracts/README.md">Contracts</a> · <a href="../adapters/README.md">Agent adapters</a> · <a href="../.github/SECURITY.md">Security</a> · <a href="../CHANGELOG.md">Release notes</a></sub></p>
