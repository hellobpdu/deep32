# C10 (inventory part), draft, version 1

Contract C10 covers the installer and the inventory. This draft is the inventory half: the modes, what
`deep32-inventory` prints, its exit codes and the verdict format. The installer half (verbs, the state file,
the codes for human-only moments) is added in stage 9. Changing anything here after stage 1 needs a line under
Revisions below saying why and what must be retested. Schema string: `deep32.inventory.v1`.

## Programs and modes

`install/deep32-inventory.ps1` (Windows PowerShell 5.1) and `install/deep32-inventory.sh` (bash 3.2 or later, Linux and
macOS) take the same arguments and print the same shapes. Both read `install/matrix.json` (contract C1).

| Command | Does | Time |
|---|---|---|
| `report` (default) | Reads the machine, prints the facts and the verdict. Read-only, no administrator rights | seconds |
| `report --net` | Also HEAD-requests the sites an install needs (any HTTP answer counts as reachable), and asks the GitHub CLI whether it is signed in | 8 s per site and 20 s for the GitHub CLI at most |
| `bench quick [--seconds N]` | Processor, memory and disk scores; refuses while gaming mode is on | 120 s default |
| `bench system`, `bench stress` | Not in this version: exit 2 | |

Options: `--json` (one compact JSON object on standard output, nothing else there), `--matrix FILE`,
`--facts FILE` (report only: judge the facts in FILE instead of probing; the file is an object with a
`facts` member, as `report --json` prints), `--seconds N` (1 to 600, digits only; leading zeros allowed),
`--guard-file FILE` (one more gaming-mode state file the bench obeys, besides the one `DEEP32_GUARD_FILE`
names), `--version`, `--help`. An option given an empty value is a bad argument. The `.ps1` also accepts
modes and options in capitals (PowerShell custom); the shell takes them in lower case only. Messages for a
person go to standard error.

Input files (`--facts`, `--matrix`, the gaming-mode state) may be UTF-8 with or without a byte-order mark,
or UTF-16 with a byte-order mark (what Windows PowerShell 5.1 writes with `>`); their content is expected to
be ASCII. The `.ps1` prints pure ASCII JSON (any other character as a `\u` escape, so no console code page
can change it); the shell passes a facts file's own bytes through. A relative path is taken from the
folder the program was started in (the `.ps1` corrects Windows PowerShell 5.1's start in `$PSHOME` when
that folder's name holds `[` or `]`).

No mode calls a language model, changes the machine, starts or stops a service or daemon, or writes a file,
except the bench's one temporary file (below). A tool the program asks for its version (15 s at most; 20 s
for the GitHub CLI's sign-in check) that overruns is ended together with every process it started, and no
other process: on Windows the `.ps1` ends the process it started itself and then its descendants, each
one taken only if its parent is already in that tree, it was created no earlier than that parent, and it
is still the process the system listed (same id and start time; a handle to each is held until the end,
so no id can be handed on meanwhile). If the process list cannot be read, it uses `taskkill /T /F` on the
process it started, while that process is still running. (A `.cmd` tool runs in `cmd.exe`, which starts
`node`; ending `cmd.exe` alone left `node` running.) A tool that ends in time but leaves a process of its
own holding its output open past the limit (for example a `.cmd` that starts a background child with
`start /b`) counts as overrunning too: that tree is ended the same way and the output read so far is
used; without a readable process list nothing more can be found once the started process has ended, so
the program then stops waiting and moves on (it no longer waits for that process however long it runs).
On Windows, `Add-Type` makes the .NET compiler keep and
remove its own temporary files. WSL is detected from the registry and files only; `wsl.exe` is never run.
Docker is asked for `docker --version` only, which never contacts a daemon (a Docker context can point at a
remote host over ssh, which `docker version` would follow).

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
numbers; GB means GiB rounded to 0.1. Tools are `false`, a version string, or `"present"` when the version
could not be read. No host name, user name, address or serial number is ever collected; mount points under
`/home`, `/media`, `/run/media`, `/Users` and `/Volumes` are shortened to the first part (`/home/...`).

| Member | Fields |
|---|---|
| `os` | `family` (windows, linux, macos, esxi), `name`, `edition` (pro, enterprise, education, home, server; distribution id on Linux; macos), `version`, `build` (Windows build number), `arch` (x64, arm64, arm, x86), `wsl_guest`, `kernel` (shell only) |
| `hw` | `vendor`, `model`, `virtual` (this machine is itself a VM) |
| `cpu` | `vendor`, `model`, `family`, `model_id`, `stepping`, `gen` (text, information only), `sockets`, `cores`, `threads`, `p_cores`, `e_cores` (0 when not hybrid), `p_threads`, `avx`, `avx2`, `avx512f` |
| `mem` | `total_gb` (visible), `installed_gb` (Windows only), `free_gb` |
| `disks` | list of `{mount, size_gb, free_gb, ssd}` for fixed local volumes, largest six; `null` if unreadable |
| `gpus` | list of `{name, vendor (nvidia, amd, intel, apple, other), mem_gb, driver, cc}`; `cc` is the compute capability as a string `"M.m"` (nvidia-smi); the shell adds `kernel_driver` on Linux |
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
  an even number) and the tier from the matrix's sizing table (C1). Numbers of any size are accepted; a fact of the wrong
  type (a number written as a string, say) counts as unknown, never as an error.
- `gpu` describes the best NVIDIA card. `forge`: `yes`, `experimental` or `no`; `tei` is the embeddings
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

A refusal prints `{"schema":..,"mode":"bench-quick","refused":"gaming-mode"}` and exits 3.

- Time split: processor single thread 15%, all threads 15%, memory 10%, disk sequential write 20%, read
  10%, random 4K read 15%, random 4K write 15%.
- Processor scores are millions of iterations of a fixed integer and floating-point loop per second. They
  compare only between runs of the same `method`. Memory is GB copied per second. Disk figures are MB/s and
  4K operations per second at queue depth one, unbuffered and write-through where the system allows
  (`direct` in the shell's result says whether it could).
- The disk test uses one file in the system temp folder (the shell uses `$TMPDIR`, else `/var/tmp`): at
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
folder; `DEEP32_INVENTORY_TEST_UNAME_S` and `_M` force the system type. They change only where the probes
read, and are unset in normal use. The bench ignores them.

Both programs: `DEEP32_INVENTORY_TEST_GUARD_ONLY=1` makes `bench quick` check the gaming-mode guard and stop
there, measuring nothing: exit 0 with `{"schema":..,"mode":"bench-quick","guard":"clear"}`, or the usual
refusal with exit 3. Only while that hook is set, `DEEP32_INVENTORY_TEST_DEFAULT_GUARD` replaces the
`DEEP32_GUARD_FILE` state file, so the guard can be tested without touching the real one; it can never switch the guard off
for a real run.

## Revisions

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
