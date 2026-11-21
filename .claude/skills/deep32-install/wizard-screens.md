# The setup wizard, screen by screen

The person runs the wizard in their own terminal. You cannot see their screen. Ask them to tell you the title at the
top of the panel, or to paste what it says. Find that title below (titles are in quotes, exactly as on screen), then:

1. say in one or two sentences what the screen asks;
2. say what to choose for their layout (`choose-setup.md`);
3. say what a good result looks like;
4. if they see something else, use `if-you-see.md`.

Other screens: the VM guide in `vm-guide-screens.md` (its steps per hypervisor in `hypervisors/<id>.md`), the
Windows Forge in `windows-forge.md`, Send disc 2 in `workstation-steps.md` step 4. Messages and what to do:
`if-you-see.md`. Error codes: `errors.md`.

## 0. Start the wizard

1. Ask the person to open a terminal on the machine. On a Windows machine that will host the Workstation VM with
   Hyper-V, PowerShell as administrator: the VM guide reads Hyper-V from there.
2. If the machine has no clone yet, the person gets and checks it ([INSTALL.md section 3](../../../INSTALL.md#3-get-and-verify-the-source)):

   ```sh
   git clone --branch v0.1.0-alpha.2 https://github.com/hellobpdu/deep32.git
   cd deep32
   gpg --import install/release-key.asc
   git verify-tag v0.1.0-alpha.2
   ```

   The signing key's fingerprint must be `B205 B406 5486 27AA BF52  50D8 A078 C5F2 5D02 5DD6`. If `git verify-tag`
   does not report a good signature from that key, stop.
3. Give the command for the machine's system, to run from the clone's top folder:

   | System | Command |
   |---|---|
   | Linux or macOS | `bash install/deep32-wizard.sh` |
   | Windows | `powershell -NoProfile -ExecutionPolicy Bypass -File install\deep32-wizard.ps1` |

4. Other forms of the same command:

   | Linux or macOS | Windows | What it does |
   |---|---|---|
   | `--help` | `-Help` | Prints the options |
   | `--demo` | `-Demo` | The whole wizard against a recorded setup: no network, no changes. Good for a first look |
   | `--plain` | `-Plain` | The setup script's plain tagged lines instead of the full screen |
   | `--vm-guide` | `-VmGuide` | Only the Workstation VM guide (`--kit DIR` or `-Kit FOLDER` names the download folder) |
   | `--send-disc2` | `-SendDisc2` | Send this machine's verified disc 2 to a Workstation over the tailnet (`workstation-steps.md`, step 4) |

The full screen needs bash 4.2 or newer (on a Mac, from Homebrew) or Windows PowerShell 5.1 or PowerShell 7. The
wizard keeps a log of every run in `~/.local/state/deep32-setup/` (Windows: `%LOCALAPPDATA%\Deep32-setup`).

## 1. What every screen shows

- **Top**: `D E E P 3 2 setup`, the role and the release at the right, a strip of 32 cells, "Step N of 6" and a
  percentage.
- **Left rail**: the six steps "Check this machine", "Choose role", "Download and verify", "Install", "Pair", "Done".
  A step can show as done, current, failed, skipped ("not needed here") or later ("on the Workstation").
- **Bottom**: the keys for this screen and the log file's path.
- **Keys**: arrows choose, Enter continues or presses the chosen button, Esc goes back one screen, Left, Right and Tab
  choose a button, L shows the log, ? shows help, Q quits (the next run picks up where this one stopped).
- In `--demo` the rail says "Demo: nothing changes".

## 2. The main screens, in order

### 2.1 "Check this machine" (the start)

- **Shows**: "The setup reads what this machine has before anything changes. It stops and tells you whenever something
  needs you." and "What happens": check this machine, choose its role, download and verify, install (you read the plan
  first and agree to it), pair (a short code typed on the Workstation).
- **Buttons**: "Start the check", "Quit". After a run that stopped: "Last time the ROLE setup stopped (DATE). Resume
  keeps its settings; finished downloads are kept." with "Resume", "Start over", "Quit".
- **Tell the person**: choose "Start the check", or "Resume" to carry on.
- **Next**: "Reading what this machine has for the ROLE role. Nothing changes in this step."
- **Stop point**: on Linux, before the Forge checks or a fix, the screen may switch to plain text with "The next step
  reads Docker or changes this machine as root. Type your password for sudo; it is not shown." The person types it.

### 2.2 "Check this machine" (the results)

- **Shows** one of:
  - "Ready for the ROLE role. Nothing was changed." Good.
  - "For the ROLE role. N things to fix; each one says what to do." Then a list of lines, each with its tag: ok, fix,
    you, info. The selected fix line shows "What to do" under the list.
- **Buttons**: "Fix here" (only when the setup can fix something itself), "Continue", "Check again".
- **Tell the person**: read every fix line to you. For each one, say what it means and what to do (`if-you-see.md`, or
  `errors.md`). "Fix here" asks before each change (2.3). After a fix, "Check again".
- The check is for the role this machine looks like. If the person picks another role later, the wizard checks again
  for that role after the settings screen.

### 2.3 "Before this step" (a question)

A box over the screen. "Not now" is chosen at first. The person decides.

| Question on screen | Yes button | What Yes does |
|---|---|---|
| "Start Tailscale sign-in now?" | "Yes, start the sign-in" | "This runs `tailscale up` on this machine as root. Tailscale shows a sign-in address; the machine joins your tailnet once you sign in. Nothing else changes." |
| "Rename this machine to NAME on your tailnet?" (Windows: "this PC"); NAME is the Forge name, `deep32-forge` unless the person chose another | "Yes, rename it" | "This renames the machine on your tailnet with `tailscale set --hostname`. The Workstation only offers Forges whose name starts with deep32-." |
| "Install it with your package manager?" | "Yes, install it" | "This installs Python 3 for the pairing helper with your package manager, as root." |

### 2.4 "Sign in to Tailscale" (stop point)

- **Shows**: "This machine joins your tailnet. Tailscale is waiting for the sign-in; the setup goes on by itself
  afterwards." Then: 1. "On any device (a phone works), open this address:" and the address in a box. 2. "Sign in with
  your own Tailscale account: the same tailnet the Workstation and your other Deep32 machines use." 3. "Approve this
  machine if your tailnet asks for approval. Then come back here." At the bottom: "Waiting for the sign-in to finish".
- **The person** opens the address and signs in. You never open it.
- **Next**: the wizard goes on by itself.

### 2.5 "Your machines"

- **Shows**: "Which parts do you have, and where? The answers decide what this run sets up here and what the wizard
  tells you to do on the other machines." Three rows: "Workstation", "Forge", "Agent PC". Left and Right change the
  answer of the selected row.
- **Answers**: "Set it up on this machine", "Set it up on another machine", "Already set up", "Not using it" (Forge and
  Agent PC only), "I don't know yet".
- **Hints**: Workstation: "The Workstation runs as a VM on a Windows PC with Hyper-V or a Linux PC with KVM." Forge:
  "The Forge needs an NVIDIA card. Without one, search works on keywords only." Agent PC: "The computer where you use
  Claude Code or Codex."
- **Tell the person** the three answers from `choose-setup.md` section 3 for this machine. A part finished on another
  machine is "Already set up". No Forge: "Not using it".
- **Good**: "Continue" opens "Choose role" with the right role selected.

### 2.6 "Choose role"

- **Shows**: "What should this machine be? You can run the setup again later on each of your other machines."
  - "Forge": "The GPU node: embeddings, reranker and PDF converter in Docker. Needs an NVIDIA card; pairs with the
    Workstation."
  - "Workstation host": "Downloads and checks discs 1 and 2 and writes the VM settings for the Workstation. Creates no
    VM by itself."
  - "Agent PC": "Connects Claude Code or Codex on this PC to your Workstation, with a pairing code."
  - One row says "suggested", with a hint such as "The check suggests Forge: an NVIDIA card was found (class
    ampere86)."
- **Tell the person** the role for this machine from `choose-setup.md` section 3. Keys 1, 2 and 3 pick a row.

### 2.7 The settings screen

Title "Forge settings", "Workstation host settings" or "Agent PC settings", with "Check these, then continue. Up and
down choose a field; type to change it." Buttons "Continue", "Back".

**Forge settings**

| Field | Starts as | What to type |
|---|---|---|
| Forge name | `deep32-forge` | Its Tailscale name. It must start with `deep32-`. Keep a name that says nothing about the person. |
| Data folder | `/opt/deep32` (Windows `C:\Deep32`) | Where the Forge keeps its data. The same folder for every later action. |
| Time zone | This machine's | A name such as `Europe/London` or `Etc/UTC`. |
| Local user | `deep32` | The account the Forge services run as. Never `root`. |
| Name in public logs | (empty) | The person types `yes` only if they agree that the Forge's name goes into the public Certificate Transparency logs. The Forge needs HTTPS, so it cannot install without it. This is the person's decision (stop point). |

**Workstation host settings**: "Hypervisor". Keep `kvm` on Linux and `hyper-v` on Windows: the VM guide works from
these files for every hypervisor, including Proxmox VE, VirtualBox and VMware.

**Agent PC settings**

| Field | What to type |
|---|---|
| Workstation address | The `https://...ts.net/mcp` address the Workstation printed when it was published (`workstation-steps.md` step 6). |
| Agent | `claude`, `codex` or `both`; `auto` picks what is installed. |

The messages this screen can show are in `if-you-see.md`.

### 2.8 "Download and verify" or "Connect your agent" (before it starts)

- **Forge**: "What happens next on this machine, in order:" download the GPU pack for the card's class (the shared
  core, the embedder and the ingestion image, about 37.1 GB) into `deep32-downloads`; check each image against the
  signed release; "Show the installer's plan: folders, ports and images. Nothing is installed until you type its
  digest."; install into the data folder, start the Forge and show the pairing code. Then: "The Forge steps run as
  root, so sudo asks for your password once before they start." (stop point).
- **Workstation host**: download disc 1 (about 1.9 GB) and disc 2 (about 19.6 GB) into `deep32-downloads` next to the
  clone; check each; "Write the VM files and the commands that create it for kvm. No VM is created or started."
- **Agent PC**, titled "Connect your agent": Connect checks the agent and the gateway address; "On the Workstation, open
  Pairing and choose Headend. It shows a ten-character code that works once, for five minutes."; "The screen switches
  to plain text while Connect runs. When it asks, type the code; it is not shown as you type."; "Connect asks before it
  changes any existing agent files. Then this screen comes back."
- **Buttons**: "Start" (Agent PC: "Start Connect"), "Back". Ask the person to approve the downloads before they press
  Start: they are large.

### 2.9 "Setting up the ROLE"

"Checking this machine again before anything changes." The tagged lines scroll. Nothing to do.

### 2.10 "Download and verify" (progress)

- **Shows** "Disc 3: the GPU pack images for this card, from the signed release." or "Disc 1 (Workstation) and disc 2
  (content), from the signed release." One row per file: "waiting", "downloading", "verifying", "verified", or
  "stopped".
- **Tell the person**: this is the long part. A stopped download resumes where it stopped when they run the wizard
  again. Never use a file that did not pass verification.

### 2.11 "Install": the Forge plan (stop point)

- **Shows**: "The Forge installer's plan. Read it, then type its digest to install." A box named "plan" with the plan's
  rows (PgDn shows more). Then "Installing writes to DATA FOLDER, loads the Docker images and starts the Forge services.
  To agree, type this digest:" and 64 characters in two lines of eight groups.
- **Help the person read the plan.** Ask them to read you the rows. Check with them:
  - `journey`: J2 or J4 on Linux, as chosen;
  - `data_root`: the data folder they typed;
  - `docker_role`: `forge`;
  - `media_components`: `core`, `embedder-CLASS` for their card's class, and `ingestion`;
  - `node_name`: their Forge name, starting with `deep32-`;
  - `forge_accept_ct`: `true` only because they typed `yes`.
- **The person** types the digest into the wizard. Spaces are fine and pasting works. You never type it.
- **Messages**: "N characters to go. Spaces are fine; pasting works." "A character does not match; Backspace fixes it."
  When it matches, the buttons are "Install" and "Back".

### 2.12 "Install" (progress)

- **Forge**: "Installing the Forge from the checked images. This takes a few minutes." Phases: "Plan accepted", "This
  host checked again", "Images mounted read-only", "Forge loader running", "Settings handed over", "Forge started; its
  doors answer with a token request". "The installer reports no progress inside these steps, so the wizard shows the
  time so far and no percentage or time left." Under "What the installer says", its last lines.
- **Workstation host**: "Writing the VM files for the Workstation." Phases "Disc 1 checked again", "VM files and
  commands written". Then the VM guide starts (`vm-guide-screens.md`).
- On a Forge the installer prints one line about the release's owner waiver for this host type, for example
  "Note: this host type is accepted for 0.1.0-alpha.2 under an owner waiver dated 2026-11-18 (recipe
  ubuntu-24.04-docker-engine-j2). ...". It does not stop the install.

### 2.13 "Pair": the Forge code (stop point)

- **Shows**: first "The Forge is installed. Its pairing window opens in a moment.", then "The Forge waits for the
  Workstation. The code works once, for five minutes." The code in a box as two groups of five characters (letters
  A to Z and digits 2 to 7), and "Valid for M:SS more".
- **Steps on screen**: 1. "On the Workstation, open Pairing and choose Forge. From its terminal:
  `doas /usr/local/d32/sbin/d32-pairdesk -forge`" 2. "Pick FORGE NAME." 3. "Type the code above. Spaces and the dot
  between the groups are fine." 4. "Wait until the Workstation says the pairing is saved, then choose Paired here."
- **Buttons**: "Paired", "New code (runs the setup again)".
- **Tell the person**: pair only once the Workstation's Import Report shows the knowledge base in use. If the
  Workstation is not ready, let the code expire; later choose "New code (runs the setup again)" or run the wizard
  again. The person types the code on the Workstation. You never type it.

### 2.14 Agent PC: Connect in plain text (stop point)

- The screen switches to plain text: "Connect runs here in plain text. When it asks for the code, type the code the
  Workstation shows (it is not shown as you type)."
- Connect asks `Workstation one-time code for claude (hidden, 10 characters):` (codex for Codex). The person gets the
  code on the Workstation (Pairing, Headend) and types it. One dot shows per character; Backspace removes one; Ctrl+U
  clears the line.
- If the agent already has a configuration, Connect lists the files it will back up and merge and asks
  `Type merge to approve this file plan:`. The person decides.
- At the end: "Press Enter to go back to the setup screen."
- Before the person starts Connect: if this conversation runs in the same agent home that Connect will change, ask
  them to close this session first, or to connect a separate profile by hand
  ([adapters/README.md section 3](../../../adapters/README.md#3-use-a-separate-profile)).

### 2.15 "Done"

- **Title**: "This machine is now the Forge", "The Workstation files are ready", "Your agent is connected", or "Done"
  with a sentence (for example "No pairing window opened. If this Forge is already paired with your Workstation, it is
  done.").
- **A row of ticks**: Forge "checked", "N images verified", "installed", "paired" or "code shown"; Workstation host
  "checked", "discs 1 and 2 verified", "VM files written", "VM guide done"; Agent PC "checked", "agent paired",
  "eleven tools checked".
- **"What's next"**, "in this order, on the machine named at the left". A step marked "press N to do this now, here"
  sets up the next part on this same machine.
- At the bottom: "Saved with every command:" and the path of `next-steps.txt`.
- **Buttons**: "Set up the ROLE here" (when there is one), "Close".
- **Tell the person**: read the list with them and update the progress checklist. The steps on the Workstation are in
  `workstation-steps.md`.

### 2.16 "STEP stopped" (a failure)

- **Shows**: "What the tools said" with up to three lines and the exit statuses, for example "deep32-get exit 10";
  "What it means"; "What to do" (numbered); sometimes "Also reported: ..."; "Full log" and its path.
- **Buttons**: "Try again", "Back", "Show the log".
- **Tell the person**: read "What it means" and "What to do" to you word for word. Explain them. If you need more, look
  the code or the exit status up (`errors.md`). After the person has done the steps, "Try again".

### 2.17 "Quit the setup?"

"Nothing more changes. Run the wizard again to pick up where it stopped; finished downloads are kept." While a step
runs: "A step is running. Quitting stops it and everything it started; finished downloads and verified parts are
kept, and the next run resumes." Buttons "Stay", "Quit".
