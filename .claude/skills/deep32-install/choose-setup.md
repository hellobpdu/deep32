# Choose the setup with the person

Use this file at the start, before any machine changes. It holds the questions, the decision table, the order of the
machines, the progress checklist and the stop points.

## 1. Ask these questions

Ask one question, wait for the answer, then ask the next. Repeat each answer back in one line.

1. "Which machines will you use for Deep32? For each one: Windows (which edition), Linux (which distribution) or
   macOS, and does it have an NVIDIA graphics card? If so, which model and how much memory?"
2. "Which of these machines can run virtual machines, and with what: Proxmox VE, Hyper-V, KVM (libvirt or
   virt-manager), VirtualBox, VMware Workstation, VMware ESXi, or none?"
3. "On which PC do you use Claude Code or Codex?"
4. "Does the machine with the NVIDIA card already run other GPU work or services? Is anything on it already using
   ports 8081, 8082, 7453, 443 or 8443?"
5. "How should the Workstation get disc 2 (19.6 GB): a CD drive or virtual CD, a USB stick of 32 GB or more, or over
   the network?"
6. "May I connect to any of these machines over SSH? If yes, which ones and as which user? If not, you run every
   command and I explain each one."

If the person does not know the card, they can run this read-only command on that machine:

```sh
nvidia-smi --query-gpu=name,memory.total,compute_cap,driver_version --format=csv,noheader
```

## 2. Decide the layout

Go down the table and take the first row that matches. Tell the person the row and why.

| Row | The person has | Layout | Workstation VM | Forge |
|---|---|---|---|---|
| 1 | One Linux PC with KVM and an NVIDIA card that passes the card check below | J4 | KVM on that PC | Docker on the same PC |
| 2 | A Windows PC with Hyper-V, and a Linux PC with an NVIDIA card | J2 | Hyper-V on the Windows PC | Docker on the Linux PC |
| 3 | Two Windows PCs: one with Hyper-V, one with an NVIDIA card and Docker Desktop | J1 | Hyper-V on the first PC | Docker Desktop on the second PC, by hand |
| 4 | A Proxmox VE node, and a Linux PC with an NVIDIA card | Proxmox Workstation, Linux Forge | Proxmox VE (boot-tested) | Docker on the Linux PC |
| 5 | A hypervisor and no NVIDIA card, or a card that fails the card check | Keyword only | As in rows 1 to 4 | None; a Forge can be added later |
| 6 | VirtualBox, VMware Workstation, VMware ESXi or another hypervisor for the Workstation | As above | That hypervisor, "not boot-tested yet" | As above |
| 7 | Only a Mac | Headend only | On another machine | On another machine |

Row 6: before the person chooses it, say this: "The wizard marks this hypervisor 'not boot-tested yet', and
docs/HARDWARE.md section 6 lists VirtualBox and VMware as not supported in this release. The boot-tested one is
Proxmox VE." Go on only if the person still chooses it.

The checks behind the rows:

| Check | Passes when | Source |
|---|---|---|
| Card | NVIDIA, compute capability 7.5 or newer, at least 10,752 MiB of memory, driver 580.65.06 or newer | docs/HARDWARE.md section 5 |
| Hyper-V | Windows 10 or 11 Pro, Enterprise or Education | the VM guide's Hyper-V text |
| Workstation VM | Virtualization with SLAT in the firmware; 4 vCPUs and 8 GB at least, 6 vCPUs and 12 GB recommended; a 200 GB empty disk | docs/HARDWARE.md, the VM guide's checks |
| Forge disk | About 281 to 284 GB free at the start when the data folder and Docker's storage share one filesystem | INSTALL.md section 5 |
| Forge host | Ubuntu 24.04 with Docker Engine (J2, J4), or Windows 11 build 26100 or later with Docker Desktop 4.99.0 or later on the WSL 2 backend (J1) | INSTALL.md section 5 |

If question 4 found GPU work or a used port on the NVIDIA machine, stop. Tell the person the Forge needs the GPU and
those ports, and let them decide. Do not plan a Forge there until they do.

## 3. Which wizard role runs where

| Row | First machine | Then | Then |
|---|---|---|---|
| 1 | The Linux PC: role Workstation host, then the VM guide (KVM) | Same PC: on Done press N, role Forge | Agent PC: role Agent PC |
| 2 | Windows PC: role Workstation host, then the VM guide (Hyper-V) | Linux PC: role Forge | Agent PC: role Agent PC |
| 3 | First Windows PC: role Workstation host, then the VM guide (Hyper-V) | Second Windows PC: PowerShell wizard, role Forge: the by-hand screen | Agent PC: role Agent PC |
| 4 | A Linux or Windows PC that can reach the node (or the node itself): role Workstation host, then the VM guide (Proxmox VE) | Linux PC: role Forge | Agent PC: role Agent PC |
| 5 | As the row it came from, role Workstation host and the VM guide | No Forge: on Your machines set Forge to "Not using it" | Agent PC: role Agent PC |
| 6 | Role Workstation host, then the VM guide for that hypervisor | As the row it came from | Agent PC: role Agent PC |
| 7 | The Workstation and Forge machines as in another row | | The Mac: role Agent PC |

Between the first and the second column the Workstation itself is installed, signed in and fed disc 2
(`workstation-steps.md`). The Forge is paired only after Import Report shows the knowledge base in use. The Agent PC
needs the address that publication prints on the Workstation.

## 4. The way for disc 2

| The person chose | What they need | Read |
|---|---|---|
| CD drive or virtual CD | In a VM, the hypervisor attaches the ISO as a read-only CD/DVD (VM guide step "Boot the installed disk; attach disc 2"). On a real machine, a 25 GB BD-R in an optical drive. | `hypervisors/<id>.md`, `workstation-steps.md` |
| USB stick | A stick of 32 GB or more, written raw by the person with balenaEtcher or `dd`; writing erases it | `workstation-steps.md` |
| Network | A PC on the same tailnet that has the verified disc 2 and runs the wizard's Send disc 2, or the Internet Archive mirror | `workstation-steps.md` |

## 5. The progress checklist

Post this in the chat once the layout is chosen. Fill in the machines. After every step, post it again with the
marks changed. Marks: `[x]` done, `[ ]` to do, `[-]` not needed, `[s]` stopped, with the reason after it.

```text
Deep32 install
Layout: (row and name)   Workstation VM on: (machine, hypervisor)   Forge on: (machine or none)   Agent PC: (machine)
Disc 2 by: (CD, USB stick or network)
[ ] 1. Layout chosen, machines named
[ ] 2. Workstation host: wizard role Workstation host: discs 1 and 2 verified, VM files written
[ ] 3. Workstation VM: VM guide steps for (hypervisor) OK or confirmed
[ ] 4. Workstation installed from disc 1 (the person, at the VM console)
[ ] 5. First boot: passwords set and Tailscale signed in (the person)
[ ] 6. Disc 2 given to the Workstation
[ ] 7. Import Report: the knowledge base in use
[ ] 8. Forge: checks passed, images verified, plan approved by the person, installed, door answers 401
[ ] 9. Forge paired on the Workstation (the person typed the code)
[ ] 10. Forge models verified: d32-gpu-ext status shows verified for embed and rerank
[ ] 11. Gateway published on the Workstation (the person agreed to Certificate Transparency)
[ ] 12. Agent PC: Connect paired (the person typed the code), --check passed
[ ] 13. First-session check passed; a known question answered with a checked quotation
[ ] 14. Optional: Ask relay paired
```

For a keyword-only layout mark 8, 9 and 10 `[-]`.

## 6. Stop points

At each of these, stop. Say what to do, where, and what they should see. Wait until the person says it is done or
shows you the result. Never do these yourself.

| Stop | Where | What the person does | They should see |
|---|---|---|---|
| sudo or administrator password | Their terminal | Types the password when asked | The step goes on |
| Tailscale sign-in | Any device's browser | Opens the address the machine shows and signs in with their own account | The machine joins their tailnet |
| Tailnet admin switches | Tailscale admin console | Turns on MagicDNS and HTTPS certificates, renames a machine, approves it | The setup check passes |
| Workstation installer | The VM console | Answers the guided installer; types the target disk's name to confirm it is erased | "The operating system has halted." |
| First boot passwords | The VM console | Sets the user and root passwords | The sign-in address appears |
| USB stick | Their PC | Chooses the stick and writes disc 2 with balenaEtcher or `dd` | The tool finishes without an error |
| Forge plan | The wizard's Install screen, or the chat | Reads the plan and types its digest | The install starts |
| Certificate Transparency | The Forge settings, or the Workstation console | Types yes for "Name in public logs"; runs `d32-publish publish --accept-ct` | The Forge installs; publication prints the address |
| Forge pairing code | The Workstation | Pairing, Forge, picks the Forge, types the code | The Workstation says the pairing is saved |
| Headend pairing code | The Workstation and the agent PC | Pairing, Headend; types the code at Connect's hidden prompt | Connect goes on |
| Connect merge | The agent PC terminal | Types `merge` if they approve the file plan | Connect prints `Launch:` |
| Send disc 2 code | The Workstation | Types the code the sending PC shows | The download starts |
| Relay words | The Workstation text console and the agent PC | Compares the ten words and types each side's words | The relay is paired |
| Proxmox API token secret | The wizard | Types the secret into the wizard's box, if they made a token | The check runs |
| Agent sign-in | Their agent | Signs in to Claude Code or Codex | The agent starts |
