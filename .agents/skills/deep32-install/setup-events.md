# Path B: you run deep32-setup yourself

The wizard in the person's terminal is the main path. Use this file only when the person asks you to run the setup,
or when the wizard cannot run there. The rules in `SKILL.md` hold for every command here.

## 1. Before each command

1. The machine is one the person named.
2. You run it on this machine, or over SSH: `ssh -n -o BatchMode=yes USER@HOST '...'` (AGENTS.md section 4).
3. Show the person the machine and the exact command. Wait for their yes. The read-only commands in section 2 marked
   "No" need no yes.

## 2. The commands

Run them from the clone's top folder. `< /dev/null` gives the setup no input, so every question it asks is answered
no.

| What | Linux | Windows PowerShell | Changes the machine |
|---|---|---|---|
| Which role this machine looks like | `bash install/deep32-setup.sh --guess-role --events < /dev/null` | `powershell -NoProfile -ExecutionPolicy Bypass -File install\deep32-setup.ps1 -GuessRole -Events` | No |
| The checks for a role | `bash install/deep32-setup.sh ROLE --checks-only --events < /dev/null` | `powershell -NoProfile -ExecutionPolicy Bypass -File install\deep32-setup.ps1 ROLE -ChecksOnly -Events` | No |
| The role itself | `bash install/deep32-setup.sh ROLE --events < /dev/null` plus the role's options | `powershell -NoProfile -ExecutionPolicy Bypass -File install\deep32-setup.ps1 ROLE -Events` plus options | Yes |

ROLE is `forge`, `workstation` or `headend`. The role options: `--data-root DIR` (Forge data, default `/opt/deep32`;
Windows `-DataRoot`, default `C:\Deep32`), `--name NAME` (the Forge's Tailscale name), `--settings FILE`,
`--plan-digest D`, `--hypervisor kvm|hyper-v` (workstation), `--workstation URL` and `--agent claude|codex|both`
(headend). `--yes` accepts every safe fix without asking: use it only when the person has approved all of them.

On Linux the Forge role runs its root steps with sudo. Run it yourself only as root or where `sudo -n true` succeeds.
Otherwise the person runs the wizard. Downloads are large (discs 1 and 2 about 21.5 GB, disc 3 about 37.1 GB); run
the role so that a long run does not cut it off, with its output in a log file you can read.

## 3. Read the lines

Read only these kinds of lines.

**Tags**, from the setup itself:

| Line starts with | Means | Do |
|---|---|---|
| `[ok]` | A check passed | Nothing |
| `[info]` | Progress | Nothing |
| `[you]` | A step only the person can do | Give it to the person word for word. Stop point |
| `[fix]` | Something needs fixing | Section 4 |
| `[fail]` | The run stopped | Give it word for word; look up the exit status (`errors.md`); stop |

**Events**, one JSON object per line, starting with `{"deep32":`:

| Event | Fields | Do |
|---|---|---|
| `role` | `role`, `guessed`, `os`, `gpu` | Tell the person which role the machine looks like; they decide |
| `step` | `step`: `check`, `download`, `install` or `pair` | Update the progress checklist |
| `ask` | `id`, `text` | A question the setup asks before a change. With no input it is answered no. See section 4 |
| `signin` | `role` | A Tailscale sign-in starts: stop point |
| `run` | `tool` (`get` or `connect`) and its choices | A child tool starts; its own lines follow, untagged |
| `exit` | `tool`, `status` | Look up `TOOL:STATUS` in `errors.md` when the status is not 0 |
| `pair` | `code`, `peer`, `minutes` | A pairing window is open: stop point; give the `[you]` lines |
| `paired` | `peer` | Pairing finished |
| `checked` | `role`, `problems` | `0` means ready for that role |

The setup exits 0 when the step finished, 1 when it stopped at a `[fail]` line or found `[fix]` lines it could not fix,
and 64 for an argument it does not know.

## 4. The checks loop

1. Run the checks for the role (section 2).
2. Give the person every `[you]` line.
3. For each `[fix]` line, find it below. Tell the person what it says and who does the fix. Run a fix yourself only
   when the table says you may, after the person's yes, then run the checks again.
4. Repeat until the last event is `{"deep32":"checked","role":"ROLE","problems":0}`.

| `[fix]` line begins | Who fixes it | How |
|---|---|---|
| `Tailscale is not installed` | The person, or you after their yes | The command or page the line names |
| `Tailscale is not signed in` | The person (stop point) | In their own terminal: `sudo tailscale up` (a Forge: `sudo tailscale up --hostname deep32-forge`; Windows: `tailscale up`), then they open the address it prints and sign in |
| `turn on MagicDNS` | The person | Tailscale admin console, DNS page |
| `turn on HTTPS certificates` | The person | Tailscale admin console, DNS page, HTTPS Certificates |
| `Tailscale name '...' must start with deep32-` | You after their yes, or the person | `sudo tailscale set --hostname deep32-forge` (or the name they chose), or the admin console's Machines page |
| `python3 3.10 or newer with ssl is needed on PATH` | The person, or you after their yes | Ubuntu: `sudo apt install git gnupg gpgv python3 curl ca-certificates` |
| `install gpgv` | The person, or you after their yes | The package the line names |
| `install curl` | The person, or you after their yes | The system's package manager |
| `the Forge installer needs python3 3.12 or newer` | The person | Ubuntu 24.04 has it |
| `NVIDIA driver ... is older than 580.65.06` | The person | Update the driver; restart if asked |
| `the card has ... MiB of GPU memory` | Nobody | This machine cannot be a Forge in this release |
| `no supported NVIDIA card found` | Nobody | This machine cannot be a Forge |
| `install the Docker Compose v2 plugin` | The person, or you after their yes | The plugin the line names |
| `Docker keeps images in its containerd store` | The person decides | Switching hides what is in the other store; the line says how ([INSTALL.md section 5](../../../INSTALL.md#5-install-the-forge)) |
| `install the NVIDIA Container Toolkit` | The person, or you after their yes | The commands in the line |
| `Docker is not installed or not running` | The person | https://docs.docker.com/engine/install/ |
| `the pairing helper needs /usr/bin/python3 3.10+ with ssl` | You after their yes, or the person | `sudo sh install/forge-host-python.sh --install` |
| `turn on systemd` (WSL) | The person | The line's steps, from Windows |
| `run this from an elevated PowerShell` | The person | PowerShell as administrator |
| `install Python 3.12 or newer from python.org` | The person | The line's steps |
| `Docker Desktop is installed but not running` | The person | Start Docker Desktop and wait for "Engine running" |
| Anything else | | Look it up (`errors.md`) |

Instead of a separate command, a question can be answered on standard input: one line `y` or `n` per `ask` event, in
the order the questions appeared, for example `printf 'y\n' | bash install/deep32-setup.sh forge --checks-only --events`.
Use that only for the questions the person approved.

## 5. The Forge on Linux, with its plan

1. **Settings file.** Ask the person for each value. Write this file only after they approve its exact text, for
   example as `~/deep32-forge-settings.json`. It holds no secret. The installer refuses secret-shaped values and values
   longer than 128 characters.

   ```json
   {
     "format": "deep32-settings",
     "format_version": 1,
     "role": "forge",
     "node_name": "deep32-forge",
     "user_name": "deep32",
     "time_zone": "Etc/UTC",
     "keyboard": "us",
     "profile": "two-machines",
     "transport": "tailnet",
     "publish": "serve-https",
     "forge_accept_ct": true,
     "written_by": "deep32-install"
   }
   ```

   `node_name` starts with `deep32-`. `forge_accept_ct` is `true` only when the person has agreed in this conversation
   that the Forge's name goes into the public Certificate Transparency logs; without it the Forge does not install.
   `profile` is an ID from `install/matrix.json`; the wizard writes `two-machines`.
2. **First run**, without a digest:
   `bash install/deep32-setup.sh forge --events --settings ~/deep32-forge-settings.json --data-root /opt/deep32 < /dev/null`.
   It checks, downloads and verifies the three images, prints the plan from a line `{` to a line `}`, then stops with
   `Plan consent required; rerun with --accept-plan and the displayed digest`,
   `{"deep32":"exit","tool":"get","status":1}` and
   `[fail] the Forge install stopped; its message above names the cause`. That stop is expected.
3. **Stop point: the plan.** Show the person every line of the plan. Ask them to reply with its digest if they
   approve. Go on only when the reply equals the plan's `"digest"` exactly (AGENTS.md section 7).
4. **Second run**: the same command with `--plan-digest DIGEST` added. The verified downloads are kept and checked
   again. Good lines: `[ok] Forge installed and running`, `[ok] door answers on https://NAME:443 and asks for a token`
   and the same for 8443.
5. **Stop point: the code.** A `pair` event and
   `[you] on the Workstation: open Pairing, choose Forge, pick NAME and type the code CODE (valid five minutes)`.
   Give the `[you]` lines word for word. The person types the code on the Workstation, once its Import Report shows
   the knowledge base in use. `[fail] no pairing window opened; if the Forge is already paired, it's done` means just
   that.

`deep32-setup.sh forge` installs with `--journey J2`. For J4, [INSTALL.md section 5](../../../INSTALL.md#5-install-the-forge)
gives the by-hand command, which takes `--journey J4`; let the person choose. The Windows Forge is by hand in
0.1.0-alpha.2 (`windows-forge.md`): you may run its first command, the download as a standard user, after
the person's yes; the second runs in the person's administrator window.

## 6. The Workstation host

`bash install/deep32-setup.sh workstation --events --hypervisor kvm < /dev/null` downloads and verifies disc 1 and
disc 2 into `deep32-downloads` next to the clone and writes the VM files. A line `VM artefacts written: PATH; ...`
names the kit folder. On Windows, `install\deep32-setup.ps1 workstation -Events` writes Hyper-V files when Hyper-V is
on, else the raw image for VirtualBox or VMware. It ends with two `[you]` lines about creating the VM, the installer,
the first boot and disc 2. Continue with `hypervisors/<id>.md`: the person does each step; you run the read-only check
it names where you have access, and compare the result with its checks.

## 7. The agent PC

Connect reads a hidden code from the keyboard, so the person runs this in their own terminal:

```sh
bash install/deep32-setup.sh headend --workstation 'https://<workstation>.<tailnet>.ts.net/mcp' --agent claude
```

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File install\deep32-setup.ps1 headend -Workstation 'https://<workstation>.<tailnet>.ts.net/mcp' -Agent claude
```

You may run `headend --checks-only --events` first, and Connect's `--check` afterwards (`workstation-steps.md` step 8).
