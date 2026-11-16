# C6. Console status channel

Contract C6, version 1, draft of 2026-11-14. Not frozen. How a node that is installing, or booting for the first
time, tells the installer and the person where it stands: the stage, the Tailscale sign-in address, a pairing
code, an error. Nobody watches a new VM's console, so without this channel the sign-in address and
pairing codes would never reach the person. Written by the install and first-boot scripts of disc 1 and disc 3.
Read by `deep32-install`, which shows the sign-in address and types pairing codes itself where it can.

## Where the lines go

| Form | What the installer reads |
|---|---|
| A VM the installer created (Hyper-V Generation 1, Proxmox, KVM with libvirt) | the VM's first serial port: the Hyper-V COM1 named pipe, Proxmox `serial0`, the libvirt console |
| Forge in WSL2 or in Docker | the node's status file, through the form's own channel (a command inside the WSL distribution; the container log) |
| A real machine (disc 1 on a laptop, disc 3 on a second PC) | nothing: the person reads the screen |

Every node also writes the lines to its own screen and to a root-only status file. The kernel console stays on the
display: on Hyper-V Generation 1, moving it to `com0` leaves the VGA without a display and the desktop could not
start. So the lines go to the serial device as a plain port.

## Line format

`D32S/1 <seq> <node> key=value ... [msg=<text to the end of the line>]`

- ASCII, at most 512 bytes, ended by LF (CR LF on a serial port). A reader ignores every line that does not start
  with `D32S/1 ` (kernel messages, login prompts).
- `seq`: decimal, 1 at each boot, one higher for each new state. `node`: `workstation`, `forge` or `headless`.
- Values hold no spaces, except `msg`, which comes last.
- Keys: `boot` (8 hex digits, random per boot), `stage`, `state` (`running`, `waiting`, `done`, `failed`),
  `version` (the disc's version), `name` (the node name), `signin` (the Tailscale login address exactly as
  Tailscale prints it), `pair` (the one-time pairing code), `pair_with` (`forge`, `workstation`, `relay`,
  `headend`), `expires` (seconds left), `error` (a code from C10), `msg`.
- Each line carries the whole current state. While a stage waits for a person, its line is written again every 10
  seconds with the same `seq`, so a reader that attaches late misses nothing.

Stages, in order: `install`, `firstboot`, `keys` (per-install keys and tokens), `signin`, `tailnet` (signed in;
name and address known), `content` (the Workstation waits for disc 2), `import`, `pairing`, `ready`. A node skips
the stages it has no part in.

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

- Reads only prefixed lines, keeps the highest `seq` for each `boot`, and treats a new `boot` as a restart.
- `signin`: shows the address, with a QR code, and waits for that stage's `done`.
- `pair`: types the code into the other side when it controls that side; otherwise shows it to the person.
- `failed`: stops and gives the one response C10 allows for that error code.

## Open points

- OPEN: the status file's path and lifetime. Decides: the builder in stages 4 and 5.
- OPEN: the error codes, which belong to C10's installer half. Decides: the builder in stage 9.
- OPEN: a time limit for each stage. Decides: the builder in stage 9.
- OPEN: proof that a serial port written as a plain device, with the console on the display, works on Hyper-V
  Generation 1, and that the installer can read the COM1 pipe without elevation. Decides:
  the lab run in stage 5.
- OPEN: whether sign-in runs `tailscale login --qr`, so the node's own screen also shows a QR code. Decides: the
  builder in stage 7.
