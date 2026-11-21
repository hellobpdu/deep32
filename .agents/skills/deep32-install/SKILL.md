---
name: deep32-install
description: Install Deep32 with the person, end to end, on the machines they name, from this clone. Guides the person through the setup wizard (install/deep32-wizard.sh or .ps1) screen by screen, then the Workstation, disc 2, Forge pairing, publication, Connect and verification, and stops at every step only the person can do. Can run install/deep32-setup with --events when the person asks. Use when the person types $deep32-install or asks to install, set up, continue or check a Deep32 install. Do not use it for research questions; that is the deep32 skill.
---

# Install Deep32 with the person

You guide one person through installing Deep32. The person runs the setup wizard in their own terminal and does every
step that needs them. You explain each screen, keep the progress checklist, check results and look up errors.
AGENTS.md at the top of this clone is the full guide; this skill follows it. Every file named below is in
`.agents/skills/deep32-install/` in this clone.

## Rules

Follow all of these, every time.

1. Work only on machines the person names in this conversation.
2. Before every command that changes a machine, show the machine and the exact command, then wait for the person's
   yes. Read-only checks named in these files need no yes.
3. Never type, ask for, read or repeat a password, passphrase, token, API token secret or Tailscale key. The person
   types it into the program that asks.
4. Never pass a plan digest (`--plan-digest`, `-PlanDigest`, `--accept-plan`) unless the person approved that exact
   plan in this conversation by sending its digest.
5. Never change, re-sign or convert in place any image, and never skip one of its checks.
6. Never turn off a firewall, a TLS or certificate check, or a signature check.
7. Stop and report when a `[fix]`, `[fail]`, error code or failed check needs the person. Give it word for word, its
   meaning and its fix, and wait.

## The flow

Do one step at a time. Finish a step before the next.

1. Read `AGENTS.md` sections "Fast path", 2 and 3. Read nothing else yet.
2. Open [choose-setup.md](choose-setup.md). Ask its questions in section 1, one at a time.
3. Pick the layout with its table in section 2. Tell the person the row and why. Wait for their yes.
4. Post the progress checklist from section 5 of that file, filled in. Post it again after every step.
5. Take the first machine from section 3 of that file. Ask the person to open a terminal there, in the Deep32 folder.
6. Give them the wizard command for that machine's system ([wizard-screens.md](wizard-screens.md) section 0).
7. Ask them to tell you each screen's title, or paste the screen. Find it in `wizard-screens.md`. Say what it asks,
   what to choose and what a good result looks like.
8. At each stop point (section "Stop points" below), say exactly what to do and where. Wait until they say it is done.
9. When the VM guide starts, open [vm-guide-screens.md](vm-guide-screens.md) and `hypervisors/<id>.md` for the chosen
   hypervisor (`proxmox`, `esxi`, `vmware`, `virtualbox`, `hyper-v`, `kvm` or `other`). Go through its steps with the
   person, one at a time.
10. On a Windows Forge, the wizard shows two commands to run by hand: use [windows-forge.md](windows-forge.md).
11. When the person sees a message you do not know, open [if-you-see.md](if-you-see.md). When a screen shows an error
    code or "STEP stopped", open [errors.md](errors.md) and look the code up.
12. For the steps on the Workstation (installer, first boot, disc 2, import, pairing, publication, Connect, verify),
    use [workstation-steps.md](workstation-steps.md), one step at a time.
13. Repeat steps 5 to 12 for the next machine, in the order of `choose-setup.md` section 3.
14. Finish with the checks in `workstation-steps.md` step 9. Post the final checklist.

If the person asks you to run the setup yourself, or the wizard cannot run, use
[setup-events.md](setup-events.md) instead of steps 6 and 7.

## Stop points

Stop and wait for the person at each of these. Never do them yourself.

- A password: sudo, administrator, the Workstation's user and root, a disk passphrase, the Proxmox API token secret.
- A Tailscale sign-in, a machine approval, or a setting in the Tailscale admin console.
- Typing yes for "Name in public logs", or running `d32-publish publish --accept-ct`: both publish a name in the public
  Certificate Transparency logs.
- Reading the Forge plan and typing its digest.
- Typing a pairing code (Forge, Headend), the Send disc 2 code, or the relay's ten words.
- Connect's hidden code prompt and its `Type merge to approve this file plan:` question.
- Anything at a VM console, in firmware, or choosing and writing a disk or USB stick.
- Signing in to Claude Code or Codex.

## Files

- [choose-setup.md](choose-setup.md): the questions, the layout table, which role runs where, disc 2's three ways,
  the progress checklist and the stop points.
- [wizard-screens.md](wizard-screens.md): starting the wizard, then every main screen, what to choose and the expected
  result.
- [vm-guide-screens.md](vm-guide-screens.md): the Workstation VM guide's screens and results.
- `hypervisors/<id>.md`: the VM guide for one hypervisor: its settings, steps, checks and fixes.
- [windows-forge.md](windows-forge.md): the Forge on Windows, the two commands by hand.
- [if-you-see.md](if-you-see.md): messages the wizard shows, what they mean and what to do.
- [workstation-steps.md](workstation-steps.md): the Workstation from the installer to verification.
- [setup-events.md](setup-events.md): running `install/deep32-setup` yourself with `--events`.
- [errors.md](errors.md): how to look up any error code, the exit statuses and the VM guide codes.
