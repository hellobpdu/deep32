# If you see

Find the line the person sees in the first column. Say what it means. Do what the last column says. Screens:
`wizard-screens.md`, `vm-guide-screens.md`. Codes: `errors.md`.

| The person sees | It means | Do this |
|---|---|---|
| "deep32-wizard: the full-screen wizard needs bash 4.2 or newer (on a Mac, from Homebrew); running deep32-setup.sh" | Old bash: the plain setup script runs instead | Read its tagged lines with the person (`setup-events.md`, "Read the lines"), or install a newer bash and start again |
| "The next step reads Docker or changes this machine as root. Type your password for sudo; it is not shown." | sudo needs the person's password | Stop point: the person types it |
| "sudo did not accept a password; the Forge steps need it." | Wrong or no sudo password | The person tries "Start" again |
| "Ready for the ROLE role. Nothing was changed." | All checks passed | "Continue" |
| "For the ROLE role. N things to fix; each one says what to do." | Some checks failed | Go through each fix line (`wizard-screens.md` 2.2) |
| "Tailscale is not signed in" | This machine is not on a tailnet | "Fix here", "Yes, start the sign-in", then `wizard-screens.md` 2.4 |
| "turn on MagicDNS: admin console, DNS page" | MagicDNS is off in the tailnet | Stop point: the person turns it on in the admin console, then "Check again" |
| "turn on HTTPS certificates: admin console, DNS page, HTTPS Certificates" | HTTPS certificates are off | Stop point: the person turns them on, then "Check again" |
| "Tailscale name 'NAME' must start with deep32- for the Workstation to offer it" | The Forge's Tailscale name is wrong | "Fix here", "Yes, rename it", or rename it on the admin console's Machines page |
| "no supported NVIDIA card found (compute capability 7.5 or newer); this machine can't be a Forge" | No usable card here | Forge on another machine, or no Forge (keyword only) |
| "the card has N MiB of GPU memory; the Forge needs at least 10752 MiB (docs/HARDWARE.md)" | The card is too small for alpha.2 | No Forge here; keyword only, or another machine |
| "NVIDIA driver V is older than 580.65.06; update it" | Old driver | The person updates the driver, restarts if asked, "Check again" |
| "Docker keeps images in its containerd store; ..." (Windows: "Docker Desktop keeps images in its containerd store; ...") | Docker uses the containerd image store | Read the whole line. Switching hides the images and containers kept in the other store; the person decides and switches ([INSTALL.md section 5](../../../INSTALL.md#5-install-the-forge)) |
| "install the NVIDIA Container Toolkit, then: sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker" | No NVIDIA runtime in Docker | The person installs it, or you run the commands after their yes |
| "the pairing helper needs /usr/bin/python3 3.10+ with ssl" | The host Python is missing or too old | "Fix here", "Yes, install it" |
| "run this from an elevated PowerShell (right-click PowerShell, Run as administrator)" | Windows Forge checks need administrator | The person starts PowerShell as administrator and runs the wizard again |
| "Docker Desktop is installed but not running: start it and wait until it says Engine running" | Docker Desktop is stopped | The person starts it, then "Check again" |
| "Note: this machine cannot run the Forge: ..." on Your machines | This machine has no NVIDIA card, or is not Linux (or Windows with the PowerShell wizard) | Forge: "Set it up on another machine" or "Not using it" |
| "Note: this machine has no hypervisor for the Workstation VM (KVM on Linux, Hyper-V on Windows)." | No KVM or Hyper-V here | If the VM runs on Proxmox VE, ESXi or another machine, the Workstation host role can still download and verify here; else "Set it up on another machine" |
| "Note: Claude Code or Codex is not installed here yet." | No agent on this PC | The person installs and signs in to their agent, then runs the wizard again |
| "The Forge name must start with deep32-, use only a-z, 0-9 and -, and be at most 39 characters." | Bad Forge name | Type a name such as `deep32-forge` |
| "The installer refuses names that look like secrets (token, secret, password, key, cookie); choose another name." | The name looks like a secret | Choose another name |
| "The data folder must be a full path, such as /opt/deep32." (Windows: "such as C:\Deep32.") | Not a full path | Type a full path |
| "The time zone looks wrong; use a name such as Europe/London or Etc/UTC." | Bad time zone | Type a zone name |
| "The local user must be a plain account name, not root." | Bad user | Type a plain name such as `deep32` |
| "The Forge publishes over HTTPS; type yes to agree that its name appears in public logs." | Name in public logs is not yes | The person's decision. Without yes there is no Forge in this release |
| "Choose kvm or hyper-v." / "On Windows the VM files are for Hyper-V; the hypervisor is hyper-v." | Wrong hypervisor value | `kvm` on Linux, `hyper-v` on Windows |
| "A Mac cannot host the Workstation VM (it needs KVM on Linux or Hyper-V on Windows); use another PC." | Workstation host on a Mac | Use another PC |
| "The address must start with https:// and end with /mcp." | Bad Workstation address | The address publication printed |
| "The plan changed since you typed its digest; read it again." | The plan is new | The person reads the new plan and types its digest |
| "This code has expired. Choose New code; it runs the Forge setup again." | Five minutes passed | "New code (runs the setup again)" when the Workstation is ready |
| "No pairing window opened. If this Forge is already paired with your Workstation, it is done." | The Forge did not open a window | Check Pairing on the Workstation; if the Forge is listed as paired, it is done |
| "The VM's settings could not be read here, so confirm the step yourself." | No read-only access to the VM from here | The person checks the "Look for" line and confirms |
| "this PC is not on your tailnet, so the wizard cannot see the Workstation join. Confirm it yourself." | `TS_NOT_HERE` | The person checks the Tailscale admin console and confirms |
| "Waiting for NAME to join your tailnet" for long | The Workstation has not signed in | The person finishes the sign-in on the VM console; if it stalls, another DNS server for the VM's network (`TS_PEER_MISSING`) |
| "Result: ERROR CODE" | A VM check failed | `hypervisors/<id>.md`, then `errors.md` |
| "The download folder belongs to root and sudo cannot read it now, so the sizes and speed are not visible. ..." | The wizard cannot show progress | Wait; each image shows when verified |
| "STEP stopped" | A step failed | `wizard-screens.md` 2.16 |
| Anything else | | Ask the person for the exact line and look it up (`errors.md`) |
