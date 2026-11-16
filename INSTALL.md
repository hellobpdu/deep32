# Installing Deep32

This page takes you from an empty machine to a working Deep32: the search system, its reference library, a GPU
node for the models, and an agent (Claude Code or Codex) that answers from it. It assumes you know nothing about
the system I run at home, and nothing of mine is needed. You sign in with your own accounts, and every key and
token is made on your machines during the install.

> **Status: 0.1.0-alpha.1 (alpha), source release.** The research skills and a by-hand MCP connection ship
> now; see [the headend](#the-headend-claude-code-or-codex). Disc installation, Deep32 Connect and the relay
> remain planned. Steps marked **(planned)** describe software that is still being built. The inventory
> program (`report` and `bench quick`), the install matrix (first draft, every profile untested), the release
> verifiers and the release key files are also included. Names, options and sizes may change.

Contents: [What you are installing](#what-you-are-installing), [1. Check your machine](#1-check-your-machine),
[2. Choose a profile](#2-choose-a-profile), [3. Get the discs](#3-get-the-discs-planned),
[4. Verify the release yourself](#4-verify-the-release-yourself), [5. Install](#5-install-planned),
[Tailscale](#tailscale), [Pairing](#pairing-planned), [The headend](#the-headend-claude-code-or-codex),
[Afterwards](#afterwards-planned), [What is never stored](#what-is-never-stored).

If an agent is doing the install for you, point it at [AGENTS.md](AGENTS.md).


## What you are installing

Deep32 has four roles. Three come from the discs; the fourth is the computer you already work on.

| Role | What it is | Comes from |
|---|---|---|
| Workstation | OpenBSD desktop with the Ask window, the Deep32 Library and its Reader, the search engine and the MCP gateway | Disc 1 installs it; disc 2 fills it |
| Content | The ready-built knowledge base, the corpus blocks and the Library content | Disc 2, attached after the Workstation is up |
| Forge | The GPU node: embeddings server, reranker, knowledge-base builder and PDF converter | Disc 3, as a VM, a second machine, WSL2 or Docker |
| Headend | Claude Code or Codex with the research skills and a by-hand MCP connection; the Ask relay is planned | `adapters/` in this source release, copied to your own PC |

The three discs (planned; each disc has its own version number):

| Disc | Release tag | Bootable | What it holds | Size |
|---|---|---|---|---|
| 1, SYSTEM | `system-<version>` | Yes | OpenBSD 8.0 and the Workstation: desktop, Ask window, Deep32 Library program (no content), search engine, gateway, Tailscale, signed updates | About 2.3 GB (estimate) |
| 2, CONTENT | `content-<version>` | No | The knowledge base, the corpus blocks `rfc`, `cisco`, `powershell` and `system`, and the Library content | About 9 to 10 GB (estimate) |
| 3, GPU | `gpu-<version>` | Yes, and it can be mounted as a payload | Ubuntu 24.04, the NVIDIA driver, the container runtime, the Forge containers, the two models, the PDF converter, Tailscale | Not measured yet |

Workstation requirements for disc 1:

Disk: 160 GB minimum for the guided install (the guided layout reserves room for two system update slots, the Library content and swap).

Disc 3 has three ways in. Booted, it installs the Forge on a VM with the graphics card passed through
(Proxmox VE or KVM) or on a second machine. Mounted, it feeds the Forge into WSL2 on a Windows PC or into
Docker on an existing Linux host. A normal Hyper-V VM on Windows cannot be given the graphics card, so on
Windows the Forge always runs in WSL2.

The Workstation has no model of its own. Meaning search and reranking happen on the Forge. A Workstation with
no Forge still works, with keyword search only.

The installer also writes a tiny fourth disc for each install, the settings disc, with names and choices only:
host names, user name, time zone, hardware profile and VM sizes. It never holds a secret. Without it, each
system asks on its own console. (planned)

Hardware needs are in [docs/HARDWARE.md](docs/HARDWARE.md).


## 1. Check your machine

`deep32-inventory` reads your machine and says which install profile fits. It changes nothing, needs no
administrator rights, calls no language model and takes a few seconds. It collects no host name, user name,
address or serial number.

On Windows, in the repository folder:

```
powershell -NoProfile -ExecutionPolicy Bypass -File install/deep32-inventory.ps1
```

On Linux or macOS:

```
./install/deep32-inventory.sh
```

| Command | Time | What it does |
|---|---|---|
| `report` (the default) | Seconds | Processor, memory, disks, graphics cards, virtualisation, hypervisors, WSL2, Docker, Tailscale, Claude Code and Codex. Then the verdict: the best profile, two runners-up, how each VM would be sized and what is missing |
| `report --net` | Up to about a minute | The same, and it also checks that the sites an install needs answer: github.com, huggingface.co, archive.ubuntu.com, NVIDIA's CUDA package site and login.tailscale.com (one HEAD request each), and whether the GitHub CLI is signed in |
| `bench quick` | About 2 minutes | Processor, memory and disk speed. It loads the machine fully while it runs, using one temporary file that is removed afterwards |
| `bench system`, `bench stress` | | Not in this version (planned) |

Add `--json` for one compact JSON object instead of the page. `--help` lists the rest.

The verdict uses four words:

| Fit | Meaning |
|---|---|
| `ready` | Nothing missing, nothing unknown |
| `unconfirmed` | Nothing missing, but some things could not be checked |
| `changes` | Possible after the listed fixes or steps only you can do |
| `no` | A hardware requirement is missing |

Lines starting `to confirm:` are about your other machines (a second PC, a Forge elsewhere), which no program
on this machine can check.


## 2. Choose a profile

`install/matrix.json` holds every profile. The inventory picks one; you confirm it before anything changes. It
prefers a layout that runs on this machine alone: `two-machines`, `baremetal` and `headend-only` need another
machine, so they are offered as alternatives and come first only when nothing can run on this machine by itself.

| Profile | Found on the machine | Workstation | Forge | Relay | Target |
|---|---|---|---|---|---|
| `win-nvidia` | Windows 11 Pro, Enterprise or Education, an NVIDIA card, Hyper-V, WSL2 | Hyper-V VM from disc 1 | Disc 3 imported into WSL2 | On the same PC | Reference |
| `proxmox-passthrough` | Proxmox VE, a card that can be passed through | VM from disc 1 | VM booted from disc 3 with the card passed through | On your own PC | Reference |
| `two-machines` | A PC for the Workstation, a second PC with an NVIDIA card on the same network | VM or real install from disc 1 | The second PC booted from disc 3, or disc 3 in its WSL2 or Docker | On either | Supported |
| `linux-nvidia` | Linux, an NVIDIA card, KVM, Docker | KVM VM from disc 1 | Disc 3 payload in Docker on the host | MCP only in version 1.0 | Supported |
| `headless` | Any Linux with an NVIDIA card | None | Disc 3 with the engine and gateway added | None: agents use MCP | Supported |
| `no-gpu` | Windows or Linux with a hypervisor, no usable NVIDIA card | VM from disc 1 | None (keyword search only), a Forge on another machine, or disc 3 in processor mode (untested) | On this PC if it runs Windows | Supported |
| `baremetal` | A laptop or PC of a known hardware profile (Dell Latitude 5430) | Installed from disc 1, replacing the operating system | On another machine | On another PC | Supported |
| `headend-only` | Any PC, macOS included | None | None | Windows only in version 1.0; MCP everywhere | Supported |
| `win-home`, `virtualbox`, `vmware`, `esxi` | As named | VM from disc 1 | Where possible | Where possible | Experimental |

Target words: **Reference** is tested for every release. **Supported** is tested for version 1.0.
**Experimental** is expected to work and untested. A profile's status changes only when a recorded test run
says so. Today every profile is planned and none has been tested.

One machine or several: the names, ports and pairing are the same either way. Moving a part to another machine
changes where it runs and nothing else.


## 3. Get the discs (planned)

Each disc is a GitHub release of its own, cut into numbered parts so that every file stays under GitHub's
2 GiB limit:

```
deep32-system-<version>.iso.part001
deep32-system-<version>.iso.part002
...
```

The code release `v<version>` holds `MANIFEST.json` and its signature `MANIFEST.json.asc`. The manifest names
the three discs that go together, every part with its size and SHA-256, and each whole disc with its size and
SHA-256.

`deep32-install fetch` downloads the parts, checks the signature and every hash, and joins each disc. It also
takes a folder or a USB stick as its source, for example parts you downloaded yourself. No sign-in is needed
to download from a public repository.


## 4. Verify the release yourself

You do not have to trust the installer. Everything it checks, you can check by hand with GnuPG's `gpgv` and a
SHA-256 tool. `install/verify-release.ps1` (Windows) and `install/verify-release.sh` (Linux, macOS, Git Bash)
do exactly the steps below.

**The release key.** Releases are signed with my OpenPGP key:

```
B205 B406 5486 27AA BF52  50D8 A078 C5F2 5D02 5DD6
```

Ed25519, created 14 November 2026, expires 13 November 2028 (I extend it before then, and the files in
`install/` are refreshed). The same fingerprint should appear in three places: the README, line 1 of
`install/release-key.fingerprint`, and the key's entry on keys.openpgp.org. If they differ, stop.

**Where `gpgv` comes from.** Linux distributions ship it (package `gpgv`). On Windows it comes with Git for
Windows: open Git Bash and it is on the path. macOS users run `brew install gnupg` once.

**Steps.** Put the parts of each disc, `MANIFEST.json`, `MANIFEST.json.asc`, and the repository's
`install/release-key.gpg` and `install/release-key.asc` in one folder, open a terminal there (Git Bash on
Windows), and run:

1. Make an empty `gpg-check` folder; it keeps your own GnuPG setup out of every step below. Then, if you want
   to see the fingerprint of the key you were given, show it with `gpg`:

   ```
   mkdir gpg-check
   gpg --homedir ./gpg-check --show-keys ./release-key.asc
   ```

   The second line of the output must be the fingerprint `B205B406548627AABF5250D8A078C5F25D025DD6`. `gpg` is
   needed only for this look at the key. The check itself needs only `gpgv` and the fingerprint above: step 2
   compares the fingerprint of the key that made the signature with it, so you can skip this step if you have
   no `gpg`.

2. Check the manifest's signature against that key only:

   ```
   gpgv --homedir ./gpg-check --keyring ./release-key.gpg --status-fd 1 MANIFEST.json.asc MANIFEST.json
   ```

   It must exit 0 and print exactly one line starting `[GNUPG:] GOODSIG` (today
   `[GNUPG:] GOODSIG A078C5F25D025DD6 Jeffrey A. Cummings ...`) and exactly one line starting
   `[GNUPG:] VALIDSIG`. In the `VALIDSIG` line the last field must be the fingerprint
   `B205B406548627AABF5250D8A078C5F25D025DD6`, and the field just before it, the signature class, must be `00`
   (a signature over the exact bytes). Any `BADSIG`, `ERRSIG`, `EXPSIG`, `EXPKEYSIG`, `REVKEYSIG` or
   `NO_PUBKEY` line means stop. Write the keyring as `./release-key.gpg`: the `gpgv` of Git for Windows cannot
   open a keyring given as `C:/...` and reports `NO_PUBKEY` instead.

3. Check every part. Compare each part's SHA-256 with its `sha256` in the manifest (`discs`, then `parts`):

   ```
   sha256sum deep32-*.part*
   ```

   On macOS use `shasum -a 256`. In Windows PowerShell, `Get-FileHash -Algorithm SHA256 <file>` gives the same
   value in capitals.

4. Join each disc's parts in order and check the whole disc against the disc's own `sha256` in the manifest:

   ```
   cat deep32-system-<version>.iso.part* > deep32-system-<version>.iso
   sha256sum deep32-system-<version>.iso
   ```

   The three-digit part numbers keep the order right.

Signed tags give a second check, with your own GnuPG. Import the key once by its fingerprint, then check the
release's tag in your clone of the repository:

```
gpg --keyserver hkps://keys.openpgp.org --recv-keys B205B406548627AABF5250D8A078C5F25D025DD6   # fingerprint
git verify-tag v<version>
```

**The shipped verifiers.**

```
powershell -NoProfile -ExecutionPolicy Bypass -File install\verify-release.ps1 -PartsDir <folder>
sh install/verify-release.sh <folder>
```

They run `gpgv` with a new empty home folder and the shipped key only, accept the signature only when
`VALIDSIG` names the pinned fingerprint, check every part, then join each disc and check it. A disc that fails
its check is deleted. There is no unsigned mode.

| Exit | Meaning |
|---|---|
| 0 | Every check passed |
| 1 | Usage error |
| 2 | An input file or folder is missing or unreadable |
| 3 | `gpgv` was not found |
| 4 | The signature is not good, or not by the pinned key |
| 5 | The manifest is not usable |
| 6 | A part is missing or the wrong size |
| 7 | A part has the wrong SHA-256 |
| 8 | A joined disc has the wrong size or SHA-256 (it is deleted) |
| 9 | A joined disc could not be written |
| 10 | (shell only) No SHA-256 tool was found |

**A new key, later.** A manifest names the key that signed it. If I ever change keys, the new one is
introduced by a release signed with the old one, and the installer trusts it only from such a release.


## 5. Install (planned)

`deep32-install` (`deep32-install.ps1` on Windows PowerShell 5.1, `deep32-install.sh` on Linux and macOS) reads
the same matrix as the inventory.

| Verb | Does |
|---|---|
| `check` | Runs the inventory report. Changes nothing |
| `plan` | Prints the ordered steps for the profile, what each changes, and the moments that need you. Changes nothing |
| `fetch` | Gets the three discs from GitHub or a folder; verifies the signature and every hash; joins the parts |
| `apply` | Runs the steps. Each has a check before and after and is safe to repeat; a stopped run resumes |
| `verify` | Proof that it works: a cited passage through the gateway, a checked quotation, both models answering, the desktop up, direct network paths |
| `install` | All of the above in order, stopping only where it needs you |
| `status`, `update`, `remove` | Read the state; apply a newer disc 2, a signed system update or a newer Forge; take off everything `apply` made |

Run `check`, then `plan`, and read the plan before `apply`.

**Moments that need you.** The installer stops, says exactly what to do, and waits:

- switching virtualisation on in the firmware, and the IOMMU for GPU passthrough;
- accepting the administrator prompt;
- each Tailscale sign-in, one per node;
- the GitHub sign-in, only if the discs come from a private repository;
- typing a pairing code the installer cannot read itself;
- preparing the host for GPU passthrough and restarting it;
- signing in to Claude Code or Codex;
- agreeing that a real install replaces the machine's operating system (`baremetal`);
- choosing passphrases: the disk passphrase on a real install, and the Ask password at first use.

Everything else runs unattended.

**Notes per profile.**

- `win-nvidia`: the Workstation VM is Hyper-V Generation 1 (OpenBSD does not support Generation 2). The Forge
  is imported into WSL2 and uses the Windows NVIDIA driver; nothing is installed as a Linux driver inside WSL.
- `proxmox-passthrough`: Proxmox's default vCPU type hides AVX, so the installer gives the Forge VM CPU type
  `host` (or `x86-64-v3`). The Workstation VM never uses a virtio-gpu display.
- `two-machines`: install the Workstation first, then boot the second machine from disc 3, then pair.
- `no-gpu`: search works on keywords only until a Forge on another machine is paired.
- `baremetal`: disk encryption is on by default for a real install. In a VM it is off by default.


## Known issues in 0.1.0-alpha.1

- The guided install needs a disk of 160 GB or more; a compact layout comes in the beta.
- During the guided install, the optional question about a root maintenance SSH key prints
  "printf: not found" instead of its prompt. Press Enter to skip it (no maintenance key, the default). Entering
  a key there does not work in this build.
- If a previous install attempt left an OpenBSD partition on the target disk, the installer reuses that
  partition's size; wipe the disk first (from the installer's (S)hell: `bioctl -d` on the old softraid volume if
  one is attached, then `dd if=/dev/zero of=/dev/rsdNc bs=1m count=16`, then reboot).


## Tailscale

Every machine or VM that hosts a part joins **your own** tailnet, signed in with **your own** Tailscale account
during the install. No auth key is on any disc or in the repository, and nothing is pre-authorised. What the
installer does in this section is planned; what Tailscale does is Tailscale's own behaviour.

- **Sign-in.** Each node prints a login address (and a QR code); open it on any device and sign in. The Forge in
  WSL2 rides on its Windows host's node and needs no sign-in of its own.
- **Names and HTTPS.** Deep32 publishes its services with Tailscale Serve over HTTPS. That needs MagicDNS and
  HTTPS certificates switched on in your tailnet; the installer checks and says which switch is off. With HTTPS
  certificates on, every machine name in your tailnet appears in the public Certificate Transparency log. The
  installer says so before it starts; give your nodes names that say nothing about you. If you decline HTTPS,
  a setting uses plain tailnet addresses instead, still encrypted by Tailscale. (planned)
- **Key expiry.** Tailscale expires a device's sign-in after 180 days by default (1 to 180 days, set per
  tailnet). To avoid signing in again, choose "Disable Key Expiry" for these devices on the Machines page of
  your admin console.
- **Direct paths.** Nodes on one machine or one network should talk directly. Through Tailscale's relays, every
  search slows by tens of milliseconds (70 to 157 ms relayed against 1 to 2 ms direct on my system). `verify`
  checks the path and reports it; `tailscale status` shows `direct` or `relay` for each peer.
- **Internet.** Tailscale needs the internet at sign-in and from time to time. A network with no internet at all
  is not supported in version 1.0.
- **Your settings stay yours.** The installer never changes your tailnet's settings. It prints an optional
  access-rule snippet that you may paste in yourself.


## Pairing (planned)

Being on the tailnet is not enough to use a service. Pairing makes the keys and tokens between your nodes:
the gateway token for your agents, the Forge's tokens for the Workstation, the public half of the Forge's
knowledge-base signing key, and the relay's link keys.

One side shows a short one-time code and you type it on the other side. The window lasts five minutes and
allows one attempt; after three failed windows, pairing locks for 15 minutes. Where the installer created the
VM itself, it reads the code from the VM's console and types it for you.

The Ask window has one more lock: the first time you use it, you set an Ask password. It is a second factor
that only you know, stored hashed (PBKDF2-SHA256, 600,000 rounds), and pairing never makes or sees it.


## The headend: Claude Code or Codex

The 0.1.0-alpha.1 (alpha) source release ships the research skills for Claude Code and Codex. You can copy
these skills and connect your agent to an installed Workstation by hand now. The connection uses MCP over
streamable HTTP on your own tailnet. Sign in to the agent and Tailscale with your own accounts.

Deep32 Connect and the relay for the Workstation's Ask window remain planned. The steps below set up direct
MCP access; they do not install the relay. Every question uses your own Claude Code or Codex usage.

### Read your Workstation's gateway token

After the Workstation's first start has completed, sign in as root at its local console and read the
agent token:

```
cat /etc/d32/state/mcp.token
```

The source names this file at
[`src/workstation/distrib/site/usr/local/d32/sbin/d32-firstboot:100`](src/workstation/distrib/site/usr/local/d32/sbin/d32-firstboot#L100),
makes a fresh token at line 125, writes it into `RAG_MCP_TOKENS` at line 134, and stores the same token in
this root-owned file with mode `0600` at lines 142-143. The environment files are root-owned mode `0400`
(lines 138-139). The gateway reads `RAG_MCP_TOKENS` from its environment at
[`src/gateway/rag_edge.py:202`](src/gateway/rag_edge.py#L202). The shipped local MCP client also reads the
separate token file at
[`src/workstation/ship/apps/usr/local/d32/ship/libexec/d32-mcp.py:134`](src/workstation/ship/apps/usr/local/d32/ship/libexec/d32-mcp.py#L134)
and line 139.

Transfer that agent token privately to your headend and set `DEEP32_MCP_TOKEN` in the environment of the
process that starts Claude Code or Codex. For example, Bash can prompt without echoing it or putting it in
shell history:

```bash
read -r -s -p 'Gateway token: ' DEEP32_MCP_TOKEN
printf '\n'
export DEEP32_MCP_TOKEN
```

On Windows, prompt in PowerShell before starting your agent from the same terminal:

```powershell
$gatewayToken = Read-Host 'Gateway token' -AsSecureString
$env:DEEP32_MCP_TOKEN = [System.Net.NetworkCredential]::new('', $gatewayToken).Password
Remove-Variable gatewayToken
```

Keep the token out of committed files and command arguments. The URL below is an example placeholder;
replace it with your Workstation's own tailnet HTTPS URL, ending in `/mcp`.

### Connect your agent by hand: Claude Code

Copy the whole `adapters/claude-code/skills/deep32/` folder to your own `~/.claude/skills/deep32/`
(on Windows, `%USERPROFILE%\.claude\skills\deep32\`). Keep its inner files and folders together.

Add an MCP entry named `deep32` to your project's `.mcp.json`, merging it with any existing entries. This
uses streamable HTTP and an environment-variable reference for the bearer token:

```json
{
  "mcpServers": {
    "deep32": {
      "type": "http",
      "url": "https://workstation.example.ts.net/mcp",
      "headers": {
        "Authorization": "Bearer ${DEEP32_MCP_TOKEN}"
      }
    }
  }
}
```

Start Claude Code with `DEEP32_MCP_TOKEN` set. The configuration holds the variable reference, never the
token literal. Set the variable again whenever you start from a new environment.

### Connect your agent by hand: Codex

`adapters/codex/` contains `skills/deep32/`, the five lens skill folders, `AGENTS.deep32.md`,
`config.deep32.toml`, `setup.ps1` and `setup.sh`. The setup scripts copy all six skill folders into your own
Codex home's `skills/` directory, merge the Deep32 block into its `AGENTS.md`, and add the `deep32` MCP
entry if one does not already exist. They leave an existing entry for you to review. They use `CODEX_HOME`
when set, otherwise your own `~/.codex` (`%USERPROFILE%\.codex` on Windows).

From the repository folder, on Linux, macOS or Git Bash:

```bash
bash adapters/codex/setup.sh --workstation-url https://workstation.example.ts.net/mcp
```

On Windows:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File adapters/codex/setup.ps1 -WorkstationUrl https://workstation.example.ts.net/mcp
```

For a manual configuration, merge the entry from `adapters/codex/config.deep32.toml` into your own Codex
home's `config.toml` and replace `{{WORKSTATION_MCP_URL}}` with the Workstation URL. Its
`[mcp_servers.deep32]` entry uses streamable HTTP and `bearer_token_env_var = "DEEP32_MCP_TOKEN"`; the token
stays in the process environment. Start Codex from that environment.


## Afterwards (planned)

- `deep32-install status` reads the state of every part. `verify` can be run at any time.
- `deep32-install update` imports a newer disc 2 the same way as the first (the previous knowledge base is kept
  for roll-back), applies a signed Workstation update, or loads a newer Forge. Where the hypervisor can, it takes
  a snapshot first.
- `deep32-install remove` takes off everything `apply` made.

Nothing is switched on that you did not ask for. The optional features (a web switch for the desktop's browser,
and later others) are listed in [docs/OPTIONAL-FEATURES.md](docs/OPTIONAL-FEATURES.md), with what each would
contact.


## What is never stored

No disc, release file or file in this repository holds a secret key, token, password or sign-in. The only
keys that ship are public halves, such as the release key in `install/`. Each install makes its own (planned):

| What | Made where and when |
|---|---|
| Tailscale membership | Your sign-in on each node |
| Gateway token for your agents | On the Workstation at first boot; read locally as above for a by-hand connection; pairing is planned |
| Engine token | On the Workstation at first boot; it never leaves it |
| Forge tokens | On the Forge at first boot; handed over during pairing |
| Forge knowledge-base signing key | On the Forge at first boot; its public half is enrolled at pairing |
| Relay link keys | By pairing |
| Ask password | By you, at first use |
| GitHub, Hugging Face, Claude Code and Codex sign-ins | Yours, kept in those tools' own stores |

The settings disc holds names and choices only.
