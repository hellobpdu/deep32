# Security review, November 2026

The record of the security review I did for Deep32 0.1.0-alpha.2 in November 2026: what it covered, how I did it,
what I found, what changed while it was under way and what is still open. It is for anyone who wants to judge
Deep32's security for themselves, and for whoever reviews it next.

<sub>Applies to 0.1.0-alpha.2 · [1 Scope and method](#1-scope-and-method) · [2 Requirements](#2-security-requirements) · [3 Boundary](#3-security-boundary) · [4 Design findings](#4-design-findings) · [5 Static analysis](#5-static-analysis) · [6 Changes](#6-changes-during-the-review) · [7 Open items](#7-open-items)</sub>

## 1. Scope and method

### 1.1 What I reviewed

The 0.1.0-alpha.2 source as prepared for publication on 19 November 2026, and the design it implements:

- the [assurance case](ASSURANCE-CASE.md), whose security requirements and trust boundaries are what sections 2
  and 3 of this record check;
- the design document [D32-TR-1](tr/README.md), in particular section 3.4 (trust boundaries) and section 14
  (security considerations), and the [decision record](DECISIONS.md);
- the [network contract C8](contracts/C8-network.md), which specifies the ports, tokens, keys and pairing;
- [SECURITY.md](../SECURITY.md), [section 10 of the overview](../README.md#10-security-considerations) and
  [INSTALL.md section 11](../INSTALL.md#11-what-is-never-stored);
- the code that implements each security control, named file by file in sections 2 and 3;
- all of the Python, and all of the C and C++ under `src/workstation`, with the tools in section 5.

### 1.2 How

I did this review with the help of an AI coding assistant, Claude, made by Anthropic. Claude read the design
document, the decision record, the contracts and the code, ran the analysis tools and drafted the findings. I read
the findings and approved this record before publishing it.
This is a review by the project itself. Nobody independent of Deep32 has reviewed its security yet, and the
assistant doesn't count as a second reviewer.

The review had three parts:

1. **Requirements and boundary.** For each security requirement (section 2) and each trust boundary (section 3), I
   read the code where it is enforced and compared it with what the release actually does.
2. **Design.** I looked for weaknesses in the design itself: places where a requirement holds in the code but the
   design leaves a risk open (section 4).
3. **Static analysis.** Ruff and Bandit on the Python, and Flawfinder on the C, with every Bandit finding of high or
   medium severity and every Flawfinder hit at levels 4 and 5 read by hand (section 5).

### 1.3 What it did not cover

- **Dynamic analysis.** The review included no fuzzing or sanitizer runs of the C code. OpenBSD has neither
  AddressSanitizer nor libFuzzer, so those runs need Linux. Fuzzing with both sanitizers ran separately on Linux on
  19 November 2026; [tests/fuzz/README.md](../tests/fuzz/README.md#5-findings) records the run and its five
  low-severity findings.
- **Shell and PowerShell.** The review ran no linter over the shell and PowerShell scripts.
- **Third-party packages.** The packages on the images were not checked against a vulnerability database. Each image's
  software bill of materials lists them.
- **CodeQL.** The workflow in [`.github/workflows/codeql.yml`](../.github/workflows/codeql.yml) analyzes the Python
  and C code on each push and pull request. It had not run when I wrote this record, so its results aren't part of
  this review.
- **A running system.** I read the firewall rules, the service configuration and the code. I didn't attack a running
  install.
- **Other people's software.** Tailscale, Docker, NVIDIA's drivers and container toolkit, and the language model
  providers are out of scope, as [SECURITY.md section 3](../SECURITY.md#3-scope) says.

## 2. Security requirements

The requirements are the eight in the [assurance case, section 2](ASSURANCE-CASE.md#2-security-requirements), which
collects them from the design document, the decision record, the installation guide and the network contract. For
each one: where I checked it in the code and what I found.

| Requirement | Where I checked it | Result |
|---|---|---|
| **SR-1. Only paired callers.** A service answers only a caller holding a credential made on that service's machine and handed over in an authenticated pairing; being on the tailnet grants nothing | The gateway (`src/gateway/rag_edge.py`) and the Forge's door (`src/gpu-pack/containers/door/door.py`) compare bearer tokens with `hmac.compare_digest`, and the gateway ignores tokens shorter than 32 characters. Pairing: `deep32net/codes.py` makes a 50-bit one-time code (7 random bytes, 10 base32 characters); `spake2.py` puts both certificate pins and both nonces into the password derivation (PBKDF2-SHA-256, 600,000 rounds); `pairlock.py` locks pairing for 15 minutes after three failed windows in 30 minutes; the tests in `tests/pairing/` | Holds. Tokens never expire (S-4), and the pairing cryptography is the project's own code (S-1) |
| **SR-2. Remote callers can only query.** The eleven tools only read, and no service accepts administrative commands over the network, except the optional maintenance SSH | The gateway declares every tool read-only (`read_only_hint=True` in `src/gateway/rag_edge.py`; DR-73). The maintenance SSH is off unless a public key is supplied at install (`d32_maint_key_ok` in `src/workstation/distrib/site/install.site`); when on, its `sshd` takes that key for `root` only, with passwords, keyboard-interactive login and forwarding off (`maint_open` in `src/workstation/ship/u4/usr/local/d32/sbin/d32-link`) | Holds. The SSH exception is stated in the assurance case and is off by default |
| **SR-3. No secret ships**, and no token appears in a log, a status line, an argument list or an agent's configuration file | Each token is 32 random bytes, made on the machine that checks it: at the Workstation's first boot (`openssl rand -hex 32` in `src/workstation/distrib/site/usr/local/d32/sbin/d32-firstboot`) or at pairing (`deep32net/tokens.py`); C8 gives each credential file an owner and mode that only its own service can read; the headend keeps the agent's MCP token in DPAPI on Windows or a mode 0600 file in a mode 0700 directory elsewhere (`PrivateStore` in `adapters/connect.py`) | Holds as far as I read. I did not trace every log statement |
| **SR-4. An image is trusted only when it matches the signed release metadata** | `src/release-tools/deep32-get.sh` carries the release key's fingerprint; `deep32_get_manifest.py` checks the signature before it parses the metadata, and `deep32_download.py` checks every chunk and the whole image; `verify-release.sh`; the tests in `tests/release-tools/` | Holds. Key custody is S-3, build provenance S-6 |
| **SR-5. Traffic between machines is encrypted and the peer authenticated** | All traffic between machines crosses the tailnet. `deep32net/pairing.py` and `adapters/relay/deep32_relay/tls.py` require TLS 1.3, and the relay requires the peer's pinned certificate. The pairing exchange accepts any certificate on purpose, because SPAKE2 authenticates both certificate hashes. The Workstation reaches the Forge over HTTPS with TLS 1.2 or later, checked against a CA file and the Forge's name (`serve_accel.py`, `src/model-bridge/d32_model_bridge.py`) | Holds. Inside the Forge, the door talks plain HTTP to the model containers on an internal Docker network on the same host |
| **SR-6. Document text is untrusted input** and can't make the system run a command, write a file or reach the network | `adapters/relay/deep32_relay/native.py` starts Claude Code with its built-in tools off and only the Deep32 server (`--tools ""`, `--strict-mcp-config`), starts Codex with a read-only sandbox and web search off, and refuses a session that doesn't see exactly the eleven tools at start. The converter runs with no network | Holds for Ask sessions. A direct session keeps the agent's own tools (S-2), and a model can still follow injected text (SECURITY.md 5) |
| **SR-7. Least privilege** for each component | Workstation: a user per service, credential files readable only by their own service, the Ask link daemon drops privilege and chroots to `/var/empty` (`src/workstation/node/asklink/src/proc.c`), `pf` rules per user (the first filter rule in `src/workstation/ship/u4/etc/pf.conf` is `block log all`), securelevel 1 (`src/workstation/ship/u2/etc/d32/ready.conf`). Forge: in `forge_docker_role.py` every service drops all capabilities and runs with `no-new-privileges`, and the door's file system is read-only | Holds as configured. I didn't test the firewall or the securelevel on a running Workstation. Membership in the Forge host's `docker` group is equivalent to root (TR 14.7) |
| **SR-8. A burst of requests can't exhaust a service** | The door serves at most 16 requests at once and refuses bodies over 4 MiB and any `Transfer-Encoding` (`door.py`); the gateway refuses bodies over 256 KiB and checks Host and Origin (`rag_edge.py`); the engine admits a fixed number of searches and queues the rest for a limited time (`admission_settings` in `src/engine/src/rag_core/api.py`, TR 6.9) | Holds as read. No load test was part of this review |

## 3. Security boundary

The boundaries are the nine in the [assurance case, section 4](ASSURANCE-CASE.md#4-trust-boundaries), drawn from
D32-TR-1 section 3.4 and the network contract. For each one: what crosses it and what I found.

| Boundary | What crosses it | Finding |
|---|---|---|
| **TB-1. The Internet and any part** | Inbound, nothing outside a window that a person opens at the Workstation's console. Outbound: Tailscale's coordination, time servers, Let's Encrypt and the agent's model provider | Holds: the Workstation's `pf` blocks by default, and `publication.py` refuses to publish while Tailscale Funnel is on. HTTPS certificates put machine names in the public Certificate Transparency logs (SECURITY.md 5). Deep32 doesn't restrict outbound traffic on the Forge or the headend, which are the user's own hosts |
| **TB-2. The tailnet and a service** | Search, model and MCP requests from other tailnet machines | Holds: services listen on loopback, the Forge binds its door to `127.0.0.1`, and each service checks its own token (SR-1) |
| **TB-3. Unpaired and paired machines** | A certificate the other side has never seen | Holds: a window opened at the responder's console, one attempt, SPAKE2 or the Ask link's ten-word comparison, and lockout (SR-1). See S-1 |
| **TB-4. The headend's agent and the gateway** | Tool calls | Holds: one MCP token per paired agent and eleven read-only tools; in an Ask session, a per-session proxy on the relay forwards only the eleven tools (`adapters/relay/deep32_relay/proxy.py`) |
| **TB-5. The Workstation and the Forge** | Text to embed and passages to rerank, with the Forge token | Holds: HTTPS checked against a CA file and the Forge's tailnet name (`serve_accel.py`) |
| **TB-6. Mirrors and the installed system** | Image files from the Internet Archive or any other mirror | Holds (SR-4). Key custody (S-3) and build provenance (S-6) are open |
| **TB-7. Document text and the agent** | Passage text that may be written to steer a model | Reduced: ingest flags instruction-like lines, the gateway's instructions say tool output is document text, and an Ask session's agent has only the eleven tools. A model can still follow injected text, and a direct session is outside the Ask box (S-2) |
| **TB-8. Services on one host** | Local requests between the engine, the gateway, the Forge forwarder and the Ask link; on the Forge, requests from the door to the models | Holds as read (SR-7). Flawfinder's race-condition warnings on the Workstation's UNIX sockets are not exploitable (section 5.2) |
| **TB-9. The headend's credentials and other software** | The agent's MCP token | Holds (SR-3). Anything that runs as the same user on the headend can read that user's credentials, as with any per-user store |

The model provider is outside every boundary by design: it sees the question, the agent's queries and the passages
the agent reads (TR 14.1, SECURITY.md 5).

## 4. Design findings

On the positive side, the review found narrow trust boundaries (eleven read-only tools, every service on loopback
behind one ingress, every credential made on the machine that checks it), designed failure handling (the Forge is
treated as unreliable, and index generations are immutable and roll back), and careful pairing code: in `p256.py`
points are checked to be on the curve and in range, the M and N constants are RFC 9382's, the confirmation is compared
in constant time, and the curve and SPAKE2 files are byte-identical in all three copies of the pairing library.

These are the weaknesses it found. "Risk" is my judgment of the risk in 0.1.0-alpha.2 as shipped.

| Finding | What I found | Risk | State |
|---|---|---|---|
| **S-1. The pairing cryptography** | SPAKE2 and P-256 are the project's own pure-Python code, so the Forge host needs no extra packages. The checks are sound as far as I read them, and the tests run the code against RFC 9382's own test vectors (`tests/pairing/reference/vectors/pairing-v2-vectors.json`). The scalar arithmetic is not constant-time, and `p256.py` says so. The Forge's copy of `pairing.py` differs from the Workstation's by its transport layer | Low: one-time codes, one attempt, five minutes, inside TLS 1.3 | Open. The first thing an outside reviewer should read |
| **S-2. Direct sessions are not boxed** | The Ask box protects Ask sessions only. In a direct session, text injected into a document reaches an agent that still has its normal tools on the user's PC | Low with the shipped corpus; higher once users add their own documents | Documented in the overview (section 10) and the agent adapters page (section 3), which recommend a separate profile. Making it the default is not scheduled |
| **S-3. Key custody** | Releases are signed with one OpenPGP key: an Ed25519 primary key that certifies and signs, with a Curve25519 encryption subkey, both set to expire in November 2028. The secret key is passphrase-protected and held on a single machine I control, with a passphrase-protected offline backup, and a revocation certificate was generated with the key. The Workstation image's signify keys stay on a build machine, and each use needs a passphrase only I enter. Signing does not yet go through a separate signing subkey or a hardware token, and no rotation or revocation procedure is published. Whoever holds the key and its passphrase can sign a release (SECURITY.md 5) | Medium over time | Open, not scheduled. The remedy is an offline primary key with signing subkeys on a hardware token, and a published rotation and revocation procedure |
| **S-4. Token life cycle** | Tokens never expire, and rotate only by pairing again. Revoking a Workstation on the Forge leaves the Forge unable to restart | Medium until revocation works | Revocation is planned for 0.1.0-alpha.3 ([roadmap 3](ROADMAP.md#3-010-alpha3)); until then the release notes say to reinstall the Forge into a new data folder. Expiry and rotation are not scheduled |
| **S-5. One network vendor** | Tailscale is the only transport between machines. It sees the coordination, its certificates put names in public logs, and the Workstation's firewall also allows Let's Encrypt. There is no LAN-only mode | A design limit | A LAN-only mode is under consideration ([roadmap 6](ROADMAP.md#6-under-consideration); DR-66) |
| **S-6. Build provenance** | I build the images on my own build machines, which are not public. AI coding assistants ran build steps on them, with root access, under my direction. They never held a signing passphrase: every release signature, the signed release commit and every push are mine. Build inputs are pinned by SHA-256. The GPU pack is rebuilt offline, in a private network namespace with only a loopback interface and its own Docker engine; its producer builds the shared core twice and stops unless both builds are byte-identical (`src/gpu-pack/build/alpha2_disc3.py`), and rebuilt images must pass an independent qualification step before they are merged. The content disc is built twice and the two images are compared. Each image comes with a provenance record and an SBOM, covered by the signed release metadata. The provenance names a revision of my development repository, which is not public, and no image has a published rebuild guide yet, so nobody else can reproduce an image byte for byte | Acceptable for an alpha, as long as it is stated | Repeat builds of every image and a rebuild guide are planned with no release set ([roadmap 5.4](ROADMAP.md#54-builds)) |
| **S-7. The Windows paths** | The Windows pairing helper, the status-file permissions and the Docker Desktop preflight were new in this release and had not been run end to end | Unknown until they are | Forge acceptance on Linux, WSL 2 and Docker Desktop is planned before 0.1.0-alpha.3 ([roadmap 2](ROADMAP.md#2-before-010-alpha3)) |

The review also found design problems that bear on security less directly:

- **Fixes need new images.** The Forge's loader, role scripts and pairing library ship inside the 18.6 GB core image,
  so a security fix there needs a rebuild, a new signature and a large download. The review recommends shipping that
  logic as a small signed kit that a signed revision can replace. Not scheduled.
- **A Workstation is installed fresh in the alpha releases.** The design has signed update sets with hash checks and
  rollback (TR 8.2), but in the alphas an update means a new install and new pairings, and there is no backup of a
  Workstation's state. Not scheduled.
- **The system can't check itself yet.** `deep32 doctor` and `deep32 selftest` ship but don't work, so a broken link
  (tailnet, certificates, a pairing) is hard to diagnose. Planned for 0.1.0-alpha.3.
- **Ask depends on how each agent version behaves.** The relay verifies each session's start-up, which is the right
  check, and it is also why an Ask question can still fail with Claude Code 2.1. The review recommends a list of
  tested agent versions with a test for each. The Ask fix is planned before 0.1.0-alpha.3.
- **The retrieval core has no public tests.** `src/engine` and `src/gateway` have no tests in this repository.
  Publishing them is planned with no release set ([roadmap 5.1](ROADMAP.md#51-tests-and-coverage)).
- **One mirror.** The Internet Archive is the only download mirror. A mirror I control is under consideration.

The review also compared the design document with the release. Where they differ is now listed in D32-TR-1
Appendix C, Implementation Status, and sections 3.4, 8.2 and 14.4 name Let's Encrypt among the permitted outside
destinations.

## 5. Static analysis

### 5.1 Python

On 19 November 2026 I ran Ruff 0.17.5 and Bandit 1.9.4 over the release source, both pinned by version and hash in
[`.github/requirements-lint.txt`](../.github/requirements-lint.txt).
[docs/BUILDING.md section 3](BUILDING.md#3-lint-and-static-analysis) has the commands.

| Tool | Scope | Findings | Review |
|---|---|---|---|
| Ruff, rule set in [`ruff.toml`](../ruff.toml): syntax errors, Pyflakes, Pylint's error checks, flake8-bugbear | 670 files, 184,075 lines | 653 with no rule ignored; 41 outside the tests and the image source, with four bugbear rules ignored | All 41 read: 29 unused imports, 6 unused variables and 6 style findings. None is a defect. The 16 loop-closure warnings outside the tests and the image source were read one by one: each closure runs before its loop moves on |
| Bandit, configuration in [`bandit.yaml`](../bandit.yaml) | 485 `.py` files and the 9 Python programs without a suffix, tests excluded | 187: 4 high, 100 medium, 83 low | Every high and medium finding read; none is an exploitable vulnerability |

What the Bandit review found, by kind:

- **SHA-1 (3, high).** Only where a format requires it: an OpenPGP v4 fingerprint, and a downloaded model file
  compared with its git blob ID. Those files are pinned by SHA-256 as well.
- **SQL built from strings (69, medium).** Table and column names are constants, or names read from the database's own
  catalog; values go through `?` placeholders.
- **XML parsing (14).** Python's `xml.etree` resolves no external entities, and its expat has refused
  billion-laughs expansion since expat 2.4.1. The only untrusted XML is EPUB content in the PDF converter, which runs
  with no network.
- **`urlopen` (12).** The URLs are `https://` constants, the vendor document list the operator gives the fetcher, or
  loopback test addresses. None comes from a network peer.
- **Binding to all interfaces (5).** Four listen inside containers, where the address is the container's own and
  Docker decides what is published; the fifth only compares listener addresses.
- **Other medium findings (5).** Two directory modes of 0711 that let services traverse without listing, an executable
  build output, a `--tmpfs /tmp` option for `docker run`, and a model loaded from a local folder with downloads off.
- **Bidirectional character (1, high).** See section 5.3.

Bandit's configuration skips two checks: importing `subprocess`, and running a program with an argument list and no
shell. A call through a shell would still be reported, and none was found.

`ruff.toml` lists the 41 reviewed findings file by file, and `bandit-baseline.json` holds the 185 findings of the
directory scan, which leaves out the programs without a suffix, so
[`.github/workflows/tests.yml`](../.github/workflows/tests.yml) fails on any new finding. On the release source as
prepared on 19 November, with Python 3.12.14, `ruff check --no-cache .` printed "All checks passed!" and Bandit
reported 185 findings (4 high, 100 medium, 81 low, in 120,379 lines), none of them new against the baseline.

### 5.2 C

On 19 November 2026 I ran [Flawfinder](https://dwheeler.com/flawfinder/) 2.0.20 over all 342 C and C++ files under
`src/workstation` (`.c`, `.h`, `.inc` and `.cpp`, about 91,400 lines). Flawfinder looks for calls with a known
history of vulnerabilities and maps each to a CWE. It reads the source and doesn't follow data flow, so a hit marks a
place to read. It doesn't prove a defect.

```sh
flawfinder --csv --minlevel=0 $(find src/workstation -type f \( -name '*.c' -o -name '*.h' -o -name '*.inc' -o -name '*.cpp' \))
```

It reported 3,221 hits: 6 at level 5, 44 at level 4, 119 at level 3, 1,404 at level 2, 578 at level 1 and 1,070 at
level 0. I read all 50 at levels 4 and 5:

| Hits | Where | Review |
|---|---|---|
| `chown` and `chmod` after `bind` on a new UNIX socket (6, level 5, CWE-362) | The Ask link daemon (`node/asklink/src/chatd.c` and its earlier copy) and the socket launcher `node/shims/src/d32-sockexec.c` | Not exploitable. Each socket is created under `umask 0177`, so nobody else can use it before its owner and mode are set, and it lives in a directory owned by root that nobody else can write (by default `/var/run/d32`, mode 0755; `d32-sockexec` refuses a directory that root doesn't own or that others can write) |
| Format strings (29, level 4, CWE-134) | The Library, the Ask link and several desktop programs | Not exploitable. Each format is a string literal or comes from a constant table. Where the hit is inside a printf-style helper, I checked every call to it: all 567 calls to the 20 helpers involved pass a literal format |
| `access` before use (9, level 4, CWE-362/367) | Help-volume and executable checks in the desktop programs; `node/asklink/src/ident.c` | Not exploitable. The desktop programs run with the user's own rights, so no privilege boundary is crossed. `ident.c` runs only as root (`d32-chatctl keygen`), and it writes the new key to a temporary file opened with `O_CREAT`, `O_EXCL` and `O_NOFOLLOW` before renaming it into place |
| `execv` (6, level 4, CWE-78) | The Ask window, the Library, the Ask link daemon, the socket launcher, the calculator launcher | Not exploitable. Each runs a fixed path, the program's own path or the program named on its own command line, with an argument list and no shell. The socket launcher runs its service only after dropping root, and the Library's helper path can be changed only through the user's own environment |

Below level 4 I read the four `strcpy` and `strcat` calls (their buffers are large enough for the bounded input, in
`library/src/app/layout.c` and `library/src/render/op_pdf.c`) and the 23 `random` hits (variables filled by
`arc4random_buf`). The remaining hits at levels 0 to 3, mostly fixed-size buffers, `memcpy`, `getenv` and `snprintf`,
were counted and not read one by one.

### 5.3 Findings worth a change

No finding in section 5 is an exploitable vulnerability. These four are worth a change. Each is in code the images
are built from, so the fixes wait for 0.1.0-alpha.3, the next release that rebuilds the images
([roadmap 3](ROADMAP.md#3-010-alpha3)); the bundle checker's copies outside the image source change with them:

1. **Invisible characters in source.** The pattern on line 35 of the PDF converter's injection scanner
   (`src/gpu-pack/containers/converter-agpl/production-converter/chain/pilot/vendor-add/scan_injection.py`) writes
   the invisible characters it looks for, among them the bidirectional controls U+202A to U+202E and the zero-width
   space U+200B, as raw characters. They are there on purpose, and invisible characters in source are what a Trojan
   Source review has to rule out. They will be written as `\u` escapes.
2. **SQL identifier quoting in the knowledge-base bundle checker.** `src/kb-tools/kb-bundle/kb_bundle.py` doubles an
   embedded `"` in a table name on line 97, but not in the names it reads from the database's catalog on lines 406
   to 414. A bundle with a `"` in a table or column name would break or change the checker's query. The same code is
   in four other copies of the file under `src/`, three of them in the image source.
3. **A value read and dropped.** The importer (`src/gpu-pack/import/d32import.py`, line 331, and its copies) reads the
   previous run's unit record and never uses it.
4. **A shadowed exception class.** `src/workstation/node/shims/py/serve_accel.py` defines `ForgeAuthRejected` on line
   75 and replaces it with an imported class on line 217. An `except` added between the two would catch the wrong
   class.

## 6. Changes during the review

Changes with a bearing on security merged on 19 November 2026, while the review was under way and before
publication. Sections 2, 3 and 5 describe the source with these changes in it:

| Change | Where | Bearing on security |
|---|---|---|
| CI fails on any new Ruff or Bandit finding, with both tools pinned by hash | `ruff.toml`, `bandit.yaml`, `bandit-baseline.json`, `.github/requirements-lint.txt`, `.github/workflows/tests.yml` | Once the workflow runs on GitHub, a new finding turns the Tests workflow red on the push or pull request that brings it in |
| The Ask relay reports a refused agent sign-in as `agent-sign-in-required`, at start and during a turn. Before, it passed the agent's "Failed to authenticate" line on as answer text. A profile with no usable sign-in now counts as signed out | `adapters/relay/deep32_relay/native.py`, `sessions.py`, `status.py`, `selftest.py`; test `tests/relay/test_claude_trace_2_1_326.py` | An authentication failure is reported as one and never shown as an answer |
| The public path rule accepts `%` only as `%` plus two hex digits, never decoded, and `~` inside a name but never at the start of a segment, so Debian package names pass | `src/release-tools/sbom/common.py` (`public_path`); test `tests/release-tools/test_sbom_public_path.py` | The rule keeps unsafe and private paths out of the published metadata. The tests prove that `..`, absolute paths, drive letters, backslashes and private folder names are still refused, and that `%2e%2e` stays a literal name that nothing decodes into `..` |
| The PDF converter's `pip check` step runs with `-I -B`, so it leaves no bytecode behind, and a recipe test requires `-B` on every build-time Python check. The image's one new Python package, Accelerate 1.15.0, is pinned in `requirements-direct.txt` and installed, like every Python package in the image, with `pip --require-hashes` | `src/gpu-pack/containers/converter-agpl/Dockerfile`, `requirements-direct.txt`; test `src/gpu-pack/tests/test_lean_image_recipes.py` | The build's check of its installed files found the stray file and stopped, which is what that check is for |
| At first start the Workstation shows the Tailscale sign-in address; three application entries are installed executable; the Ask link service starts nothing until it is set up; the message for a `doas` command names the full path | `src/workstation/distrib/site/usr/local/d32/sbin/d32-firstboot`, `src/workstation/ship/tools/mksite-ship.sh`, `src/workstation/ship/u3/etc/rc.d/d32chat`, `strings.tsv` | None on the boundary. The `doas` rules are unchanged; the message now matches them, since `doas` matches the full path |

## 7. Open items

| Item | From | Target |
|---|---|---|
| An outside review of the pairing cryptography | S-1 | Not scheduled |
| An isolated agent profile as the default for direct sessions | S-2 | Not scheduled |
| An offline primary key with signing subkeys on a hardware token, and a published rotation and revocation procedure | S-3 | Not scheduled |
| Revoking a Workstation without breaking the Forge's restart | S-4 | 0.1.0-alpha.3 |
| Token expiry and rotation | S-4 | Not scheduled |
| A LAN-only mode | S-5 | Under consideration |
| Repeat builds of every image and a rebuild guide | S-6 | Planned, no release set |
| Forge acceptance on Linux, WSL 2 and Docker Desktop | S-7 | Before 0.1.0-alpha.3 |
| Ask answers with Claude Code 2.1 | Section 4 | Before 0.1.0-alpha.3 |
| `deep32 doctor` and `deep32 selftest` | Section 4 | 0.1.0-alpha.3 |
| The four static-analysis findings | Section 5.3 | 0.1.0-alpha.3 |
| Triage of CodeQL's first results for the Python and C code | Section 1.3 | When the workflow first runs on the public repository |
| Fixes for the five defects the fuzz targets found | Section 1.3 | 0.1.0-alpha.3 |
| ShellCheck and PSScriptAnalyzer | Section 1.3 | Planned ([roadmap 5.3](ROADMAP.md#53-static-analysis-coding-standards-and-dependencies)) |
| A check of the pinned packages and the bills of materials against vulnerability databases, and of what it finds | Section 1.3 | OSV-Scanner checks the pins in CI; the rest is planned ([roadmap 5.3](ROADMAP.md#53-static-analysis-coding-standards-and-dependencies)) |
| Public tests for the engine and the gateway | Section 4 | Planned, no release set |
| A review by someone independent of the project | Section 1.2 | Not arranged |

To report a security problem, follow [SECURITY.md](../SECURITY.md).

---

<p align="center"><sub><a href="../README.md">Overview</a> · <a href="../INSTALL.md">Installation</a> · <a href="HARDWARE.md">Hardware</a> · <a href="OPTIONAL-FEATURES.md">Optional features</a> · <a href="tr/README.md">Design</a> · <a href="DECISIONS.md">Decision record</a> · <a href="contracts/README.md">Contracts</a> · <a href="../adapters/README.md">Agent adapters</a> · <a href="../SECURITY.md">Security</a> · <a href="../CHANGELOG.md">Release notes</a></sub></p>
