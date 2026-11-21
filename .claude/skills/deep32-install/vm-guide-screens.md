# The Workstation VM guide, screen by screen

The guide's screens. The steps and checks for each hypervisor are in `hypervisors/<id>.md`. Messages:
`if-you-see.md`. Codes: `errors.md`.

It starts after the Workstation host role, or with `--vm-guide` (`-VmGuide`). It never creates, changes, starts or
stops a VM. The person does each step; Enter checks it.

## 1. "Workstation VM: the hypervisor"

"Which hypervisor will run the Workstation VM? The wizard looked for each one on this PC (read-only)." Seven rows:
Proxmox VE, VMware ESXi, VMware Workstation, VirtualBox, Hyper-V, KVM / libvirt, "Other hypervisor or bare metal".
Each says "found on this PC" when found, and "boot-tested" (Proxmox VE only) or "not boot-tested yet". Buttons
"Continue", "Later". In plain mode: "Which hypervisor runs the Workstation VM?" with numbers and `Number [N]:`.
Tell the person the hypervisor from their layout, then open `hypervisors/<id>.md`.

## 2. "Workstation VM: no verified disc 1 here"

The guide did not find deep32-get's VM folder next to the verified disc 1 image. This is code `KIT_MISSING`; the
screen shows its fix (`errors.md`). Buttons "Check again", "Back".

## 3. "Where does the VM run?"

"On this PC" or "On another machine". When only one is possible the wizard skips this screen. With "On another
machine" the person copies files there and runs the steps there; the wizard checks what it can and asks the person to
confirm the rest.

## 4. "Workstation VM: the network"

"This PC's network, read only, and how the VM connects." Rows such as "This PC", "Gateway", "DNS", the hypervisor's
networks and "Tailscale here". Then "How the Workstation VM connects":

- "Bridged: the Workstation gets an address on your LAN from the router's DHCP, like any other machine there.
  Tailscale usually connects directly between machines on the same LAN, which is the fastest path. Recommended."
- "NAT: also works; the Workstation only needs outbound internet. Tailscale may relay through its DERP servers when it
  cannot connect directly, which is slower for Ask, the Library and the Forge."
- Always: "Host-only, internal and private networks do not work: the Workstation has no internet there, so its
  Tailscale sign-in cannot finish."

Tell the person: choose Bridged unless they have a reason for NAT.

## 5. "Tailscale, here and across sites"

Four notes; nothing to choose. "Continue".

## 6. "HYPERVISOR: the VM's settings"

The fields for that hypervisor (`hypervisors/<id>.md`, "The VM's settings"). Plain mode: "The VM's settings (Enter
keeps the value in brackets):". The "Workstation name" is the host name the person will type into the Workstation
installer; the wizard waits for it to join the tailnet.

## 7. The step screens

- **Title**: the step's title. Under it "Step I of N · HYPERVISOR, boot-tested (or not boot-tested yet) · WHERE".
- **Body**: numbered items, each command on its own line.
- **Before Enter**: "Enter checks this step." or "Enter shows what to look for; you confirm it."
- **Buttons**: "Check" and "Skip"; after a result "Next" (the last step: "Finish") and "Check again"; while waiting
  "Stop waiting" and "Skip".
- **Results**:

| Result | Means | Do |
|---|---|---|
| `OK` | The wizard read the VM and every check passed | "Next" |
| `ERROR CODE` | A check failed; the screen shows "What it means" and "What to do" | The person does the fix, then "Check again". The fix is in `hypervisors/<id>.md`, else `errors.md` |
| `YOU CONFIRMED` | The person said they saw the "Look for" line | "Next" |
| `SKIPPED` | The person skipped it; it is "listed again at the end" | Come back to it |
| `WAITING` | "Waiting for NAME to join your tailnet" | The person finishes the Workstation's sign-in |
| `WORKING` | "VBoxManage convertfromraw is writing the copy" | Wait |

- **Plain mode** shows `Step 1 of 7: TITLE`, `Where: ...`, the items, then
  `Enter checks this step; s skips it, b goes back, q stops:`, and `Result: ...` with lines such as
  `ok     Firmware: bios: seabios` or `ERROR  Target disk: scsi1, 171.8 GB (171798691840 bytes) (VM_DISK_SMALL)`.

## 8. The boxes a step can open

| Box | Shows | The person |
|---|---|---|
| "Confirm this step" | "Look for: ..." and buttons "Yes, I see it", "Not yet". Plain: `Did you see it? [y/N]` | Answers only what they saw |
| "The node's certificate" (Proxmox, with a token) | "Proxmox uses its own certificate. Its SHA-256 fingerprint at https://ADDRESS:8006 is:" and the fingerprint. Plain: `Does it match? [y/N]` | Compares it with the node (web UI, the node, Certificates; or `pvenode cert info` on the node). "It matches" or "It does not" |
| "API token secret" (stop point) | "Type or paste the secret of TOKEN. It stays in memory for this session only: it is never shown, logged or saved." Plain: `Secret of TOKEN (kept in memory for this session only; not shown, logged or saved):` | Types the secret. You never ask for it |

## 9. "Workstation VM: where it stands"

Every step with OK, ERROR, YOU CONFIRMED or SKIPPED; "Skipped, still to do"; "Every command:" and the path of
`steps-<id>.txt` in the kit folder. Buttons "Done", "Back". Then the Done screen (`wizard-screens.md` 2.15) lists
what is left.
