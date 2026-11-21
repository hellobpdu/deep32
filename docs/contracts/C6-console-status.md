# C6. Console status channel

Contract C6, version 2, draft of 2026-11-17. Not frozen. How a node that is installing, or booting for the first
time, reports its stage, sign-in address, pairing code or error. Written by Workstation install
and first-boot scripts and Forge runtime sources, including retained legacy ISO first boot; the host installer's
automatic reading and code entry remain acceptance work.

Changed: status producers and protected delivery channels; sequence and stage behavior; paths, error codes and implemented time limits.

## Where the lines go

| Form | What the installer reads |
|---|---|
| A VM the installer created (Hyper-V Generation 1, Proxmox, KVM with libvirt) | the VM's first serial port: the Hyper-V COM1 named pipe, Proxmox `serial0`, the libvirt console |
| Forge in Docker or through the legacy WSL2 interface | the protected current-status file; stdout and the Docker status log omit `signin` and `pair` |
| A real machine running Workstation or an existing-host Forge | nothing: the person reads the screen |

The producers write a root-only current-status file and try the available console and plain serial device.
The Workstation keeps the kernel console on the display and opens `/dev/cua00` once when it is available and is
not the active console; Forge tries `/dev/console` and `/dev/ttyS0`. The Hyper-V display and COM1
reader checks below are still unverified.

## Line format

`D32S/1 <seq> <node> key=value ... [msg=<text to the end of the line>]`

- ASCII, at most 512 bytes including the line ending. Forge writes CR LF to its console and serial devices;
  the Workstation writes LF through its plain serial descriptor. A reader ignores every line
  that does not start with `D32S/1 ` (kernel messages, login prompts).
- `seq`: decimal, one higher for each new state; Forge starts at 1 for a new producer, while the Workstation keeps
  its boot and sequence in runtime state and can adopt the installer's carried state. `node`:
  `workstation`, `forge` or `headless`.
- Values hold no spaces, except `msg`, which comes last. Long `msg` details are shortened with `[truncated]`, or `~` when fewer than 11 message bytes remain; an oversized structured record is refused.
- Keys: `boot` (8 hex digits, random per boot), `stage`, `state` (`running`, `waiting`, `done`, `failed`),
  `version` (the disc's version), `name` (the node name), `signin` (a validated `https://login.tailscale.com/`
  address), `pair` (the one-time pairing code), `pair_with` (`forge`, `workstation`, `relay`,
  `headend`), `expires` (seconds left), `error` (a code from C10), `msg`.
- Each line carries the whole current state. While a stage waits for a person, its line is written again every 10
  seconds with the same `seq`, so a reader that attaches late misses nothing.

Stages: `install`, `firstboot`, `keys` (per-install keys and tokens), `signin`, `tailnet` (signed in;
name and address known), `content` (the Workstation waits for disc 2), `import`, `pairing`, `ready`.
A node skips work it has no part in and may revisit a stage: Forge checks model health under `firstboot` after
`keys` and `tailnet`, before `pairing`.

```
D32S/1 7 forge boot=<8 hex> stage=signin state=waiting signin=https://login.tailscale.com/a/<code>
D32S/1 12 workstation boot=<8 hex> stage=pairing state=waiting pair=<code> pair_with=forge expires=240
D32S/1 13 workstation boot=<8 hex> stage=import state=failed error=<code> msg=disc 2 signature does not verify
```

## Secrets

- The sign-in address and the pairing code are the only sensitive values ever written, and both are short-lived:
  the address is needed only until the node is signed in, and the code works once inside its window (five
  minutes, one attempt, C8). A `signin` value is written only while the stage `signin` waits.
- Never written: a token, key, password, password hash, recovery code or any other lasting secret.
- The status file is readable by root only. A `pair` value is removed from it when its window closes.

## The installer

These are requirements for the host installer; a general host C6 consumer is not established by the merged source.

- Reads only prefixed lines, keeps the highest `seq` for each `boot`, and treats a new `boot` as a restart.
- `signin`: shows the address, with a QR code, and waits for that stage's `done`.
- `pair`: types the code into the other side when it controls that side; otherwise shows it to the person.
- `failed`: stops and gives the one response C10 allows for that error code.

## Open points

- Paths: `/tmp/d32/c6/status` in the Workstation install ramdisk, `/var/run/d32/c6/status` after installation,
  `/run/deep32-forge/status` for the legacy Forge first boot, and `<data-root>/status/status` for Docker Forge.
  Writers replace the current state atomically; the full cleanup and retention lifetime remains OPEN.
- Error identifiers come from the emitting component: Workstation uses `WS_*` codes and Forge uses `forge-*`
  reasons; C10 records the component-specific behavior.
- Implemented Forge limits: 300 seconds for the legacy ISO console password and pairing window, 60 seconds for model
  container startup, and 600 seconds for each authenticated model health wait. Workstation
  console, sign-in and content waits have no overall stage deadline in this source.
- OPEN: proof that a serial port written as a plain device, with the console on the display, works on Hyper-V
  Generation 1, and that the installer can read the COM1 pipe without elevation. Decides:
  the lab run in stage 5.
- Workstation `d32-link login --qr` optionally runs `tailscale up` with `--qr`; Forge first boot only checks the
  owning host's existing sign-in and never invokes login or up.

Sources: `src/workstation/distrib/site/usr/local/d32/libexec/d32-c6.sh` (`c6_setup`, `c6_set`, `c6_repeat`);
`src/workstation/ship/install/d32-ramdisk.ksh` (`_d32c6init`);
`src/workstation/distrib/site/usr/local/d32/sbin/d32-firstboot`;
`src/workstation/ship/u4/usr/local/d32/sbin/d32-link` (`login_fg`);
`src/gpu-pack/firstboot/status.py` (`Status`); `src/gpu-pack/firstboot/firstboot.py` (`signin`, `pair`, `start_models`);
`src/gpu-pack/forms/forge_docker_role.py` (`pair_local`); `src/gpu-pack/containers/door/status-log.py`.
