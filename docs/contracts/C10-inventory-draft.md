# C10 (inventory part), draft, version 2, 2026-11-17

Contract C10 covers the installer and the inventory. This draft is the inventory half: the modes, what
`deep32-inventory` prints, its exit codes and the verdict format. The installer half (verbs, the state file,
the codes for human-only moments) is added in stage 9. Changing anything here after stage 1 needs a line under
Revisions below saying why and what must be retested. Schema string: `deep32.inventory.v1`.

Changed (2026-11-17): reconcile argument limits, output normalization, legacy probe scope, nullable verdict values and benchmark methods; retest both inventory fixture suites.

## Programs and modes

`install/deep32-inventory.ps1` (Windows PowerShell 5.1) and `install/deep32-inventory.sh` (bash 3.2 or later, Linux and
macOS) take the common modes and options below; PowerShell also accepts native single-dash option names and case variants.
Both read `install/matrix.json` (contract C1).
Their report envelopes have the same members; the shell benchmark fallback prints fewer disk members and null memory scores.

| Command | Does | Time |
|---|---|---|
| `report` (default) | Reads the machine, prints the facts and the verdict. Read-only, no administrator rights | seconds |
| `report --net` | Also HEAD-requests the sites an install needs (any HTTP answer counts as reachable), and asks the GitHub CLI whether it is signed in | 8 s per site and 20 s for the GitHub CLI at most |
| `bench quick [--seconds N]` | Processor, memory and disk scores; refuses while gaming mode is on | 120 s default |
| `bench system`, `bench stress` | Not in this version: exit 2 | |

Options: `--json` (one compact JSON object on standard output, nothing else there), `--matrix FILE`,
`--facts FILE` (report only: judge the facts in FILE instead of probing; the file is an object with a
`facts` member, as `report --json` prints), `--seconds N` (1 to 600, one to nine digits; leading zeros allowed),
`--guard-file FILE` (one more gaming-mode state file the bench obeys, besides the one `DEEP32_GUARD_FILE`
names), `--version`, `--help`. An option given an empty value is a bad argument. The `.ps1` also accepts
modes and options in capitals (PowerShell custom); the shell takes them in lower case only.
Diagnostic error messages and bad-argument usage go to standard error; text reports, requested help and version output go to standard output. A JSON report is one compact object; invalid facts and bad arguments produce no standard output, as specified by the exit codes below.

Input files (`--facts`, `--matrix`, the gaming-mode state) may be UTF-8 with or without a byte-order mark,
or UTF-16 with a byte-order mark (what Windows PowerShell 5.1 writes with `>`); their content is expected to
be ASCII. The `.ps1` prints pure ASCII JSON (any other character as a `\u` escape, so no console code page
can change it); the shell parses and prints the facts again as compact JSON, normalizing strings through its ASCII parser. A relative path is taken from the
folder the program was started in (the `.ps1` corrects Windows PowerShell 5.1's start in `$PSHOME` when
that folder's name holds `[` or `]`).

No mode calls a language model, changes the machine, starts or stops a service or daemon, or writes a file,
except the bench's one temporary file (below). A tool the program asks for its version (15 s at most; 20 s
for the GitHub CLI's sign-in check) that overruns is ended together with every process it started, and no
other process. Windows starts the tool suspended, places it in a private kill-on-close Job Object, and only then lets it run; failure to establish containment leaves the tool unavailable.
The job contains detached descendants as well as the original process, without a process-count cutoff.
The shell uses a native Linux PID namespace mapped to the current user, with an absolute timeout; it does not start the requested tool where that containment is unavailable.
On those systems, including macOS, discovered version tools remain present with an unreadable version, and the GitHub sign-in result is unknown.
A tool whose output remains open past the deadline counts as overrunning even if its original process has exited; its job is ended and the result is unavailable.
Network probes use one eight-second deadline per site and accept every HTTP response, including redirects and error status codes, without following redirects.
On Windows, `Add-Type` makes the .NET compiler keep and
remove its own temporary files. WSL is detected from the registry and files only; `wsl.exe` is never run.
In legacy `report` and `bench quick`, Docker is asked for `docker --version` only, which never contacts a daemon.
The prerequisite check queries version, Compose and daemon information only through the explicit local Docker Desktop pipe or host Unix socket.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | done (a verdict of "no profile fits" is still 0) |
| 1 | other error; with `--json` the object is still printed when the facts are known, and a missing or broken matrix gives `"verdict":{"error":"matrix-unavailable"}`. A facts file that is not one JSON object whose member `facts` (exact case) is an object is also 1, with nothing on standard output |
| 2 | bad arguments (also a missing `--facts` file, an empty option value, and the modes not in this version) |
| 3 | refused: `gaming-mode`, `gaming-state-unreadable`, or `windows-use-ps1` (the shell bench on Windows) |

## The report object

```
{"schema":"deep32.inventory.v1","tool":"0.1.0","impl":"ps1|sh","mode":"report","at":"UTC time",
 "facts":{...},"verdict":{...},"notes":["..."]}
```

`facts`. Conventions: `null` = could not be read; `false` = checked and absent; numbers are plain JSON
numbers; GB capacity facts mean GiB, generally rounded to 0.1, except Windows `installed_gb`, which is rounded to a whole GiB. Tools are `false`, a version string, or `"present"` when the version
could not be read. No host name, user name, address or serial number is ever collected; mount points under
`/home`, `/media`, `/run/media`, `/Users` and `/Volumes` are shortened to the first part (`/home/...`).
`--facts` prints its supplied facts, including unused members; use only public or synthetic fixture values.

| Member | Fields |
|---|---|
| `os` | `family` (windows, linux, macos, esxi), `name`, `edition` (pro, enterprise, education, home, server; distribution id on Linux; macos), `version`, `build` (Windows build number), `arch` (x64, arm64, arm, x86), `wsl_guest`, `kernel` (shell only) |
| `hw` | `vendor`, `model`, `virtual` (this machine is itself a VM) |
| `cpu` | `vendor`, `model`, `family`, `model_id`, `stepping`, `gen` (text, information only), `sockets`, `cores`, `threads`, `p_cores`, `e_cores` (0 when not hybrid), `p_threads`, `avx`, `avx2`, `avx512f` |
| `mem` | `total_gb` (visible), `installed_gb` (Windows only), `free_gb` |
| `disks` | list of `{mount, size_gb, free_gb, ssd}` for fixed local volumes, largest six; `null` if unreadable |
| `gpus` | list of `{name, vendor (nvidia, amd, intel, apple, other), mem_gb, driver, cc}`; `cc` is the compute capability as a string `"M.m"` (nvidia-smi); the shell adds `kernel_driver` to Linux sysfs fallback entries, not to nvidia-smi entries |
| `virt` | `firmware` (virtualisation on), `slat`, `iommu`, `hypervisor` (one is running) |
| `hyp` | `hyperv`, `kvm`, `proxmox`, `virtualbox`, `vmware`, `libvirt` |
| `wsl` | `{present, wsl2_distros}` on Windows (a count; names are never read); `null` elsewhere |
| `docker` | `present`, `client` (version), `nvidia` (container toolkit; `null` on Windows) |
| `tools` | `tailscale`, `claude`, `codex`, `gh` |
| `ports` | `checked`, `busy`: which of 8765, 8766, 8443, 8081, 8082, 7452 have a listener |
| `net` | `null` unless `--net`: `{github, huggingface, ubuntu, nvidia, tailscale: {ok, code, ms}, gh_signed_in}` |

Unknown extra fields are allowed and ignored by the verdict.

## The verdict

```
{"matrix":"0.3.1","best":"win-nvidia","fit":"ready",
 "cpu":{"class":"modern","ws":"yes","forge":"yes"},
 "gpu":{"class":"rtx20","forge":"yes","tei":"turing-1.9","upstream":"experimental","mem_gb":11,"mem_tier":"11"},
 "size":{"threads":12,"mem_gb":64,"tier":2},
 "ranked":[{"p":"win-nvidia","target":"reference","status":"planned","machines":1,"fit":"ready",
            "missing":[],"unknown":[],"auto":[],"confirm":[],"size":{"ws":[6,16],"forge":[4,16]}}, ...],
 "ruled_out":[{"p":"proxmox-passthrough","why":["gpu-cuda"]}],
 "warnings":["gpu-upstream-experimental"]}
```

- `best` is `ranked[0].p`, or `null` with `fit` `none` when no profile is possible. `ranked` holds the best and
  up to two runner-ups. `ruled_out` lists profiles that match the machine but miss a hardware requirement.
- `fit`: `ready` (nothing missing or unknown), `unconfirmed` (only unknowns), `changes` (a `fix`, `human` or
  `confirm` requirement missing), `no` (never in `ranked`). Missing `auto` requirements (the installer does
  them) are listed in `auto` and never lower the fit. Requirements of kind `confirm` are about the person's
  other machines (a second machine, a Forge or a headend elsewhere), so no machine can check them: when no
  fact settles one, it is listed in `confirm` and never lowers the fit either (a facts file that states it
  is judged like a `human` requirement).
- Order of `ranked`: single machine first. Every possible profile whose `machines` is 1 comes
  before every alternative whose `machines` is 2 (`two-machines`, `baremetal`, `headend-only`: the layout
  needs another machine for the Workstation or the Forge), whatever their fits; so an alternative is the
  best only when no one-machine profile can run on that hardware (a Mac, a Celeron, the Latitude 5430
  profile). Within each group: fit first (`ready`, then `unconfirmed`, then `changes`), then the profile's
  `rank`, then fewer missing requirements, then matrix order. Within a group a profile that needs a change
  never outranks one that is ready or only unconfirmed, whatever their ranks; for example a profile missing
  KVM on a cloud VM, where nested virtualisation may be impossible, comes after a ready `headless` (fixture
  `linux-cloud-l4`). On a Windows PC with a usable card but too little free disk space, `win-nvidia` with its
  list of changes comes first and the ready `two-machines` and `headend-only` follow as alternatives (fixture `win-pro-rtx2080ti`).
- `machines` in each ranked entry: 1 or 2, from the matrix profile (C1). The page for a person adds
  "needs another machine" to the line of a profile with 2. Tell the person when the best is an
  alternative: it means no one-machine layout fits this hardware.
- `missing`, `unknown`, `auto`, `confirm`, `why` and `warnings` hold ids from the matrix (`requirements`,
  `warnings`); the matrix gives each a sentence for a person. An agent reads the ids, not the sentences. The
  page for a person prints the best profile's lists as `missing:`, `not checked:`, `installer:` and
  `to confirm:` lines.
- `size` per profile: `[threads, GB]` for the Workstation VM and the Forge on this machine, or `null`. The
  top-level `size` is what the sizing counted: threads (fast threads on hybrid processors; a fraction is
  cut toward zero), memory in GB (installed rounded to the nearest whole number, else visible rounded up to
  an even number) and the tier from the matrix's sizing table (C1).
  Numeric facts are accepted within the parser's range and converted to floating point for sizing.
  A numeric string counts as unknown for numeric calculations and ordered comparisons; equality and membership comparisons are type-sensitive, so a wrong-type value can compare false or true instead.
- `gpu` describes the best NVIDIA card. `forge`: `yes`, `experimental`, `no` or null when the selected card's class is unavailable.
  `gpu.class` is `unknown` for an unclassified NVIDIA card and null when no card is selected.
  `tei` is the embeddings
  server image for its class; `upstream` is what the server's own table says (the Turing image is
  experimental upstream; compute capability under 7.5 is unsupported).
- The rules, the ranking and the sizing are defined by the matrix (C1: `rule_language`, `ranking`,
  `sizing.allocation`). Both programs must give the same verdict for the same facts; the tests check it.

## The bench quick object

```
{"schema":"deep32.inventory.v1","tool":"0.1.0","impl":"ps1","mode":"bench-quick","at":"...","seconds":120,
 "bench":{"method":"csharp|python|shell",
          "cpu":{"st_mops":..,"mt_mops":..,"threads":..,"scaling":..},
          "mem":{"copy_gbs":..,"buffer_mb":..},
          "disk":{"where":"temp","free_gb":..,"file_mb":..,"seq_write_mbs":..,"seq_read_mbs":..,
                  "rand_read_iops":..,"rand_write_iops":..,"skipped":null|"reason"}},
 "gpus":[...],"notes":[...]}
```

`disk.where` is `temp`, `/tmp` or `/var/tmp`; a private `$TMPDIR` prints as `temp`.
Without Python, the shell method measures only the processor, prints null memory scores and emits only `where` and `skipped` in `disk`; it creates no disk-test file.

A refusal prints `{"schema":..,"mode":"bench-quick","refused":"gaming-mode"}` and exits 3.

- For methods `csharp` and `python`, the nominal time split is processor single thread 15%, all threads 15%, memory 10%, disk sequential write 20%, read
  10%, random 4K read 15%, random 4K write 15%. The shell fallback runs only the two processor phases, rounded to whole seconds with a one-second minimum per phase.
- Processor scores for `csharp` and `python` are millions of iterations of a fixed integer and floating-point loop per second; `shell` uses an integer recurrence. They
  compare only between runs of the same `method`. Memory is decimal GB copied per second. Disk figures are MiB/s and
  4K operations per second at queue depth one, unbuffered and write-through where the system allows
  (`direct` in the shell's result says whether it could).
- For `csharp` and `python`, the disk test uses one file in the system temp folder (the shell uses `$TMPDIR`, else `/var/tmp` when it exists, else the platform temp folder): at
  most 1 GiB (256 MiB under 60 s), never more than 5% of the free space, skipped if less than 10 GB would
  stay free. Windows opens it delete-on-close; the shell unlinks it at once. Nothing is left behind.
- The bench reads the gaming-mode state files first. There is no built-in default: it reads the file the
  environment variable `DEEP32_GUARD_FILE` names, when that is set and not empty, and the `--guard-file`
  one as well when given (the option adds a file, it never replaces the other). With neither, no state
  file is read and the bench runs. For each file that exists: a JSON object whose top-level `mode` is the
  string `on` in any case means `gaming-mode`; a top-level `mode` that is any other string lets the bench
  run; anything else (empty, not JSON, not an object, no `mode`, a `mode` that is not a string, a file that
  cannot be read) means `gaming-state-unreadable`. A `mode` key nested deeper never counts. A path that is
  not a regular file is ignored. A machine that keeps such a state file (a switch that pauses heavy jobs
  while the person plays, for example) sets `DEEP32_GUARD_FILE` to its path, and every bench obeys it.

## Test hooks

Shell: `DEEP32_INVENTORY_TEST_ROOT` makes the Linux probes read `/proc`, `/sys`, `/etc` and `/dev` under that
folder; `DEEP32_INVENTORY_TEST_UNAME_S` and `_M` force the system type. They change only where the report probes
read, and are unset in normal use. The bench's measurements and system-type choice ignore them, but its Linux graphics readout still uses `DEEP32_INVENTORY_TEST_ROOT`.

Both programs: `DEEP32_INVENTORY_TEST_GUARD_ONLY=1` makes `bench quick` check the gaming-mode guard and stop
there, measuring nothing: exit 0 with `{"schema":..,"mode":"bench-quick","guard":"clear"}`, or the usual
refusal with exit 3. Only while that hook is set, `DEEP32_INVENTORY_TEST_DEFAULT_GUARD` replaces the
`DEEP32_GUARD_FILE` state file, so the guard can be tested without touching the real one; it can never switch the guard off
for a real run.

## Revisions

- 2026-11-17, T-0228 contract fixes: require contained, bounded tool execution, keep JSON diagnostics off standard output, reject malformed numeric facts, select the six largest local volumes and retain unknown disk discovery, and remove the storage number ceiling. Retest both implementations with the legacy, prerequisite, contract-fix and exact-number suites.

- 2026-11-17, pre-run hardening: add a manual Docker Desktop distribution-isolation gate,
  leave GPU execution unknown when only a runtime advertisement is available, and evaluate
  optional measured Workstation memory plans without choosing a VM size or host reserve.
  Prerequisite resource contract version 4; report schemas and legacy exits are unchanged.

- 2026-11-17, common GPU form: J3 selects a generic full Linux Docker host. Windows GPU
  prerequisites name Docker Desktop, its Linux engine, WSL 2 backend and NVIDIA GPU
  support, without importing a distribution. Unknown evidence still refuses. Retest both
  implementations with the prerequisite and legacy inventory fixture suites.

- 2026-11-16, Phase A: add the opt-in prerequisite check below as `deep32.inventory.v2`. Legacy
  report/bench objects, facts, verdicts and exits retain version 1. Retest both implementations with
  `tests/inventory/run_prereqs.py` and the existing `run_tests.py` fixture suite.

- 2026-11-14, still stage 1 (after the stage 1 tester left two items open): the order of `ranked` is fit
  first, then rank (it was rank first, so a profile needing changes could be best over a ready one); the
  requirement kind `confirm` and the `confirm` list per ranked profile are new (the three layout
  requirements, never checkable, had kept their profiles at `unconfirmed` for ever); `no-gpu` matches only
  where no usable NVIDIA card is known (as its own `found` text says); matrix 0.2.0; a tool
  that overruns is ended with its whole process tree. Retest: `tests/inventory/run_tests.py` (fixtures
  hold the hand-worked reasoning; new fixtures `linux-cloud-l4` and `win-pro-tools-unreadable`; new
  time-limit tests with the fake tools in `tests/inventory/fakebin-win`).
- 2026-11-14, same day, after the adversarial test of that change: a tool that ended in time but left a
  background child holding its output kept the `.ps1` waiting for that child's whole life (30.6 s against
  an 8 s limit in the test; without end for a child that never ends). The output wait now has the same
  limit, and that child tree is ended as above. New tests: `fakebin-win/bg-tool.cmd`, the time limit from
  a folder whose name holds spaces, parentheses and `&`, and the `confirm` kind with the fit-first order
  on a small matrix and facts written with a byte-order mark and CRLF. No change to the verdict.
- 2026-11-14, end of stage 1 ("single machine first"): one-machine profiles are ordered before the
  alternatives that need another machine (matrix 0.3.0, member `machines`, 2 on `two-machines`, `baremetal`
  and `headend-only`); each ranked entry gains `machines`; the page marks alternatives "needs another
  machine"; the `.ps1` reads the matrix's `schema` case-sensitively (member and value), as the shell always
  did. Why: with fit first, `two-machines` ranked best on almost any machine (it needs only a supported
  processor), which blocked the stage 1 check "the right profile on three machines". Retest:
  `tests/inventory/run_tests.py` (ten fixtures' orders worked again by hand, each with its reasoning; new
  cases on a small matrix for the alternatives and the schema case; `machines` checked on every fixture).
- 2026-11-14, end of stage 1, later: the gaming-mode state file comes only from `DEEP32_GUARD_FILE` or
  `--guard-file`; the programs hold no built-in path, and with neither no state file is read. No change
  to the verdict or the exit codes; matrix 0.3.1 changes wording only. Retest:
  `tests/inventory/run_tests.py` (guard tests, with new cases for the variable).

## Prerequisite check, version 2

```
bash install/deep32-inventory.sh --journey J2 --role gpu-host --check-prereqs --format json
powershell -NoProfile -ExecutionPolicy Bypass -File install\deep32-inventory.ps1 -Journey J1 -Role gpu-host -CheckPrereqs -Format json
```

These options belong to `report` only. PowerShell accepts both its single-dash names above and the
double-dash Bash names. `--json` is an alias for `--format json`; conflicting formats are bad arguments.
`--net` cannot be combined with this mode. A check never starts a daemon/distribution, changes a feature,
installs a package, signs in, writes a report file or reads an agent credential store.

Journey names are J1 through J5. Roles are `workstation-host`, `gpu-host`, `agent` and `all`.
Each report concerns this host only. In J2, `all` selects Workstation and agent on Windows, or GPU and
agent on Linux. J3 `all` selects the local Linux Docker full-container plan; it does not check an optional
Workstation VM or an agent on another PC. J5 omits all GPU, NVIDIA runtime and Docker prerequisites.
Windows Home cannot pass the Hyper-V role; its Docker Desktop GPU role is judged independently.

The declarative resource is `install/prerequisites.json`, schema `deep32.prerequisites.v1`. Every record
has a stable ID, capability scopes, minimum version where established, required operator action by OS,
elevation and reboot flags. Candidate package commands are labelled `unverified-offline`; official
catalogue identifiers, exact GPU and Linux-host versions, Docker Desktop version floors and clean-host acceptance remain release gates.
An unknown mandatory requirement cannot pass. The source checker does not manufacture missing signed
runtime minima or certify the intended Tailscale account from a successful daemon query.

Check-mode exits are 0 when every required check is met; 10 for missing/unknown requirements, pending
restart or unsupported journey/host/role; 1 for invalid facts or missing/broken prerequisite resources;
2 for bad arguments, conflicting formats or a missing facts file. Legacy benchmark refusal remains 3.

The existing `--facts FILE` seam also supports this mode. `facts.os` selects the local platform;
`facts.prerequisites` maps stable IDs to booleans, canonical states, or objects such as
`{"state":"met","version":"3.12.0"}`. A versioned prerequisite cannot pass without a numeric version.
`facts.storage_plan` is a list of `{filesystem_id, needed_bytes, free_bytes}`. Fixtures bypass every
host probe. This test seam is not an installer bypass or proof that another machine is ready.

The check JSON has `schema`, `tool`, `impl`, `mode=check-prereqs`, `journey`, `role`, `platform`,
`selected_roles`, `recipe_maturity`, `prerequisites`, `storage_plan`, `requires_reboot` and `next_action`.
Each prerequisite has `id`, `required`, `state` (`met`, `missing`, `unknown`), stable `PR_*` reason code,
`need`, `detected_version`, `minimum_version`, `action_id`, safe `fix` text, `requires_elevation`,
`requires_reboot` and `recipe_maturity`. Unneeded checks use `required=false`, `state=unknown` and
`PR_*_NOT_NEEDED`. No input-supplied command is executed. Version output accepts numeric components only.

Storage records contain anonymous `filesystem-N`, `needed_bytes` and `free_bytes`. Requirements sharing
one backing-filesystem identity are added once against its minimum reported free bytes. Unknown paths,
budgets or free bytes leave `storage.plan=unknown`. The downloader calculates its authenticated selected
media budget; the inventory does not infer a VM target, Docker root or Docker Desktop backing volume from the
current working directory. Reports omit local paths, node names, addresses, tailnet names and raw
Tailscale/Docker output.

Exit 0 means every required check passed. Exit 10 means operator action: missing/unknown prerequisite,
unsupported journey/host/role, or reboot required, with distinct reason IDs. Exit 1 means an invalid
resource/facts document or internal failure; exit 2 means bad arguments or a missing facts file.
Legacy benchmark refusal remains exit 3. No arbitrary child exit becomes the public result.

`--facts FILE` is the read-only fixture seam. Its exact-case `facts` object may contain `os` and a
`prerequisites` object keyed by capability ID. Values are booleans, `met|missing|unknown`, or objects with
`state` and numeric `version`. Versioned mandatory requirements need a readable version. Absent or
malformed facts become unknown. `facts.storage_plan` is an array of
`{filesystem_id, needed_bytes, free_bytes}`; only non-negative integral JSON numbers are accepted.
Decimal and exponent forms are checked exactly, with no fixed numeric magnitude ceiling; large results can use compact exponent notation. If adding different exponents requires more than 65,536 digits of mantissa alignment, storage stays unknown with `PR_STORAGE_PLAN_RESOURCE_UNAVAILABLE` instead of rejecting an individual number or exhausting memory.
These are injected test observations, not signed release evidence. Using a fixture never runs a host
probe. Live external tools share a four-second execution and output deadline and the containment rules above. Bash leaves a probe unavailable when the required timeout or native Linux namespace support is absent. Local Docker probes use only the explicit host Unix socket and cannot follow
the user's remote Docker context. WSL is never invoked by this check.

### Host preflight evidence

`windows.docker-isolation` is required only for a Windows GPU-host role. It remains unknown
in a live inventory: discovery of Desktop, its local Linux engine or the WSL2 kernel cannot
prove that integration is disabled for unrelated distributions. The operator must inspect
the approved Desktop configuration, including automatic default-distribution integration,
before any approved preparation or restart. Stop if that isolation cannot be retained.
Never launch, import into, convert, reset or unregister an unrelated distribution or use a
global WSL shutdown to make the check pass. The checker neither opens Desktop settings nor
changes them. A Windows Workstation-only check and a remote Linux GPU-host check do not
require this Desktop gate; a remote GPU failure does not justify starting local Desktop.

The Windows daemon-info parser treats an advertised NVIDIA runtime as evidence of runtime
availability only. `windows.docker-gpu` remains unknown until separately qualified execution
evidence exists; a wrong engine or backend is missing. An authorised GPU window must verify
the actual selected GPU class and VRAM, authenticated model health and private offline
model binds. Driver qualification uses the maximum of all resolved signed release floors
for every selected GPU. Missing pins remain a release blocker; do not invent a minimum,
pull a sample image or fall back to CPU to claim a GPU pass.

With a split Forge plan, `platform.recipe` is met only when the signed `FORGE-PREREQUISITES.json`
has a row for this host's OS ID, version, architecture and journey. A Windows row states floors:
the lowest Windows 11 build (any update revision), the accepted editions, and the lowest Docker
Desktop and Docker Engine versions, compared number by number, on the WSL 2 backend. The host
must also report a Microsoft WSL 2 kernel, whose version is not pinned. The floors must admit the
one Windows setup the evidence exercised, and two Windows rows may not share a journey and
edition. A row is accepted by exactly one of a passed lifecycle record (`acceptance_sha256`) or a dated owner waiver
(`waiver`: grantor, date, release, recipe, what was not run, and the evidence files by name and
SHA-256). A waiver changes no other check. When the matched row is waived, the preflight evidence
carries one printable `notice` line of at most 300 characters; text output prints it directly
before the final line while `platform.recipe` is met. JSON output does not change.

Workstation memory and DHCP are not inferred from installed RAM or a list of switches.
Measure available RAM before starting the stopped VM, accounting for running host and
production services, and obtain an approved VM allocation and additional retained free
host reserve. A 16 GiB host and a reduced VM allocation do not by themselves prove fit.
The optional exact-case fixture field `facts.workstation_memory_plan` contains
`{total_bytes, available_bytes, vm_bytes, host_reserve_bytes}`. Values must be integral JSON
numbers from zero through 9007199254740991; total, VM and reserve must be positive, and
available cannot exceed total. The sum of VM and reserve must be in that same exact range.
If supplied, this plan determines `workstation.memory` even when the legacy boolean claims
success: insufficient available bytes are missing; malformed or incomplete plans are unknown.
No plan values or private extra fields are printed. An absent plan retains the existing
fixture observation. Live inventory leaves this operator-approved budget unknown; fixtures
are test observations and cannot certify an actual machine or an approved resource change.

For Hyper-V, preserve the reviewed Generation-1 recipe, wired virtual NIC and distinct
verified installer and empty target disks. Confirm VM-name ownership before creation and
install-to-target reboot before an overlay. For KVM, preserve the reviewed firmware, disk
and NIC mapping; do not reuse a Hyper-V disk or infer that its reduced-memory exception
qualifies another recipe. Select an existing approved DHCP-capable network with the VM
off, then verify the guest address and route. Switch presence alone leaves DHCP unproved;
do not invent static addressing or change host production routes to bypass it.

Before download, image load and content import, recheck the measured signed peak space
budget for every backing filesystem: downloads and joined images, retained payload,
Docker's actual store, VM target, imported content/index and retained rollback generations.
The existing `storage_plan` aggregation adds demands sharing one backing filesystem and
uses its lowest reported free bytes. Disc sizes alone cannot establish this peak budget.
Unknown Docker store locations, private bind permissions or persistent media paths remain
unknown until recorded and tested on the owning host, including restart. Preserve previous
generations and private state on failure; do not remove production, shared images or signed
rollback material to force a pass.

Sources: `install/matrix.json`, `install/prerequisites.json`, `install/deep32-inventory.ps1` and `install/deep32-inventory.sh`, as read 2026-11-17.
