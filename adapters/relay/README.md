# Ask relay

The alpha.3 source enables a portable Ask relay for Claude Code or Codex through the existing Connect entry point.
Each installation owns an independent agent profile, loopback D32U/1 listener, SAS pins, reverse MCP proxy, startup action and passive doctor status.
Ask spends that profile's model allowance; the relay never signs in automatically or imports an ordinary agent profile.

## Installation and startup check

Use Python 3.11 or newer and absolute native executable paths; DEEP32_CONNECT_PYTHON selects the connector interpreter.
A native agent symlink is accepted; retain its stable installed path so the relay resolves and checks the current regular-file target at every launch and records that target's version in attestation.
Install creates a relay-owned provider venv and installs the approved requirements.lock with pip --require-hashes --only-binary :all:.
The lock pins pyOpenSSL 26.4.0, cryptography 50.0.2, cffi 2.1.1, pycparser 3.12 and typing-extensions 4.16.0 with 165 approved hashes; package markers select the dependencies required by the chosen Python/platform.
A missing approved wheel, hash mismatch or provider failure stops installation rather than selecting another TLS implementation.

~~~sh
adapters/connect.sh --relay install --agent claude --agent-binary /absolute/path/to/claude --relay-transport loopback --relay-port 7452 --no-autostart
~~~

~~~powershell
powershell -NoProfile -ExecutionPolicy Bypass -File adapters\connect.ps1 -Relay install -Agent claude -AgentBinary 'C:\absolute\path\claude.exe' -RelayTransport loopback -RelayPort 7452 -NoAutostart
~~~

Select codex and its native executable for a Codex installation; keep the printed install ID for all later actions.
At install and after an agent upgrade the relay performs a no-model startup check and stops the agent before a model turn.
Claude stage 1 requires successful initialize control evidence with a version, permission mode dontAsk and a signed-in account, followed by mcp_status naming exactly one server called deep32; install requires its presence and a session requires it to be connected.
Claude emits system/init only after the first user message; stage 2 checks that event for the selected model/folder, exactly eleven mcp__deep32__ tools, no built-in tool and the one server.
Other system events, including ui_invalidate, are ignored while either gate is waiting; assistant, user, result and tool events before the gate still refuse the session, and the original deadlines remain in force.
The per-session proxy forwards no tool call until stage 2 passes; a mismatch ends the process tree and fails the turn.
The Claude stage 1 install check also records the short model aliases initialize lists and the --effort levels (low, medium, high, xhigh, max) that every one of those models lists; the relay advertises them with the qualified pair (default, default) first, and each session.open must name one advertised model and one advertised effort.
A session's model goes to Claude Code as --model and its effort as --effort (default passes none); at each turn stage 1 checks that initialize still lists that model with that effort, and stage 2 checks that system/init reports a model of the chosen alias.
Codex must attest its effective configuration, sole enabled MCP server, tool set and disabled features before question release; a Codex relay advertises its one qualified model and effort.
Missing or inconsistent evidence refuses Ask.
Scripted events establish gate ordering; real native permission behaviour, account resolution and platform persistence are qualified live.
Sign in independently outside Ask with the connector's explicit login action, which selects only the relay-owned profile, runs the native sign-in and reruns the no-model check; never copy ordinary account/configuration files.
A repeat install with the same ID and arguments reruns the check and preserves the profile, identity and pins.

~~~sh
adapters/connect.sh --relay login --relay-id INSTALL_ID --agent claude
adapters/connect.sh --relay login --relay-id INSTALL_ID --agent codex
~~~

~~~powershell
powershell -NoProfile -ExecutionPolicy Bypass -File adapters\connect.ps1 -Relay login -RelayId INSTALL_ID -Agent claude
powershell -NoProfile -ExecutionPolicy Bypass -File adapters\connect.ps1 -Relay login -RelayId INSTALL_ID -Agent codex
~~~

A signed-out profile leaves the staged installation unavailable and reports the sign-in action; paid Ask work remains refused until qualification succeeds.
Local console errors include their StateError code for diagnosis; these details are never sent to the paired Workstation.

## Pairing and tailnet publication

~~~sh
adapters/connect.sh --relay pair --relay-id INSTALL_ID --agent claude
adapters/connect.sh --relay status --relay-id INSTALL_ID --agent claude --json
~~~

~~~powershell
powershell -NoProfile -ExecutionPolicy Bypass -File adapters\connect.ps1 -Relay pair -RelayId INSTALL_ID -Agent claude
powershell -NoProfile -ExecutionPolicy Bypass -File adapters\connect.ps1 -Relay status -RelayId INSTALL_ID -Agent claude -Json
~~~

Foreground pairing requires a protected console and stopped service, and refuses JSON output.
On the Workstation console run `doas /usr/local/d32/sbin/d32-pairdesk -relay`, which points Ask at the relay and then pairs; both sides arm a 300-second window allowing one unpinned attempt.
Compare the independently displayed ten words, then type each side's words at the other side's protected prompt without numbering.
The relay prompt shows the words as you type them and checks each one when Space or Enter ends it; a slip that is not a pairing word is asked for again, and a different pairing word ends the attempt with nothing stored. Words never belong in arguments, environment variables, files or shared records.
Mutual TLS 1.3, both confirmations, durable peer ID/certificate pins and both commits precede ACTIVE.
Outside the armed window unknown certificates fail; stored pin/node-ID mismatches fail even while pairing is armed.
Tailnet installation adds --relay-transport tailnet --tailscale-binary ABSOLUTE_BINARY; PowerShell uses -RelayTransport tailnet -TailscaleBinary ABSOLUTE_BINARY.
Connect journals and reads back only its raw TCP Serve forwarder to the same loopback port and preserves unrelated routes.
On Linux, Access denied: serve config denied requires the one-time operator action `sudo tailscale set --operator=$USER`, then a retry of the same installation command.
A failed install retains its owned receipt or attempt journal, root and any recorded Serve route; repeating install for the same agent reuses that root and resumes the exact route. A recorded incomplete provider environment is removed only after its files match their recorded hashes and removal is read back, then rebuilt in the same root.
If several installations match the selected agent, specify --relay-id; changed foreign routes remain a refusal.
It never resets Serve, enables Funnel, terminates link TLS, signs in to Tailscale or changes tailnet settings.
The installed Workstation endpoint, exact PF destination and port must agree with the headend; shipped work-link PF rules use 7452.
An alternate port needs a matching approved Workstation PF rule and endpoint setting rather than only an address-table change.
Gateway d32-pairdesk -headend enrolment is separate and optional for Ask; its ten-symbol code and d32-pairdesk -relay do not replace asklink SAS.
Repeat the same install with --relay-id INSTALL_ID after pairing, omitting --no-autostart, to register and start the same installation.
Windows uses a least-privilege per-user InteractiveToken logon task, Linux a systemd user unit and macOS a LaunchAgent.
DPAPI, permissions, native startup/forwarder persistence and the real Workstation question/cancellation/restart/removal remain live host checks; offline command fakes do not establish these observations.

## Instructions, settings and answer checks

The installed source package carries the agent's instructions: RESEARCH.md for Claude Code and RESEARCH-codex.md for Codex, each generated from that agent's research skill by make_research.py with the skill's rules for a default answer first and its blueprint last.
Claude Code receives the instructions with each question; Codex receives them once, as the thread's base instructions, with a boundary that says the session has no code mode.
A conversation's earlier turns reach the agent labelled as earlier questions and answers; the oldest exchange is dropped when the history no longer fits beside the instructions.
Operator settings come from the service's environment at start and are never fields of the recorded install configuration, so setting one leaves the receipt valid: DEEP32_RELAY_IDLE_SECONDS (60 to 900, default 900), DEEP32_RELAY_CHECKS (footer or off, default off), DEEP32_RELAY_MODEL and DEEP32_RELAY_EFFORT (unset: the agent's own default), DEEP32_RELAY_ANSWER_RECORD (on or off, default off), DEEP32_RELAY_SAVE_DIR (the absolute path of an existing folder, an Obsidian vault; unset: nothing is saved), DEEP32_RELAY_SAVE_MAP (the absolute path of a map file) and DEEP32_RELAY_SAVE_HOOK (the absolute path of a program); the last two need DEEP32_RELAY_SAVE_DIR.
A present value that is not valid stops the service with invalid-relay-setting before any agent starts or port opens; the agent's environment never inherits these names.
With DEEP32_RELAY_SAVE_DIR set, every turn's instructions end with one more sentence asking the agent to close the answer with a line `Blueprint: <exam> <id>` (for example `Blueprint: ENARSI 1.10.d`) or `Blueprint: none`, and every finished answer is saved in the vault as one Markdown note (deep32_relay/vault.py).
The map file is TSV, one line per topic: the exam id (ENARSI or CCIE, a space, an id such as 1.4 or 1.10.d), a tab, and a folder relative to the vault; lines starting with # are comments.
A note goes into the folder the map gives the answer's last Blueprint id when that folder exists in the vault, and otherwise into _inbox at the top of the vault, which is made when missing; nothing is written outside the vault or through a linked folder or junction.
A note is named `<id> Ask - <short question>.md` (`Ask - <short question>.md` in _inbox), where the short question is the question without its modifiers and without the characters Windows or Obsidian refuse in a name, at most 80 characters; a name in use gets ` (2)`, ` (3)` and so on, so no file is ever replaced.
Each note is written under a hidden temporary name in its folder and then renamed, readable by the user the relay runs as; no folder's permissions are changed.
A note holds YAML front matter (title, type and lens ask, exam, ids, date in UTC, model, effort, modifiers, quote_checks, docs, tags), a heading, the question as a question callout and the answer as the Ask window received it, Blueprint line included and Checks footer left out.
The Workstation sends modifiers as the sentences they stand for, so a note's modifiers field holds those sentences; a question sent with the typed `| $...` form keeps the typed letters.
After a save the hook program runs with the vault folder as its only argument, without a shell, for at most 60 seconds; answers saved while it runs are followed by one more run.
Notes are written and the hook runs on a thread of their own, never inside a turn; logs/events.jsonl gets an answer-save line per note and a save-hook line per run, with counts and outcomes only, and a note that cannot be written or a hook that fails never fails the answer.
While another session waits for the only slot, an idle session is closed after 60 seconds whatever the setting; a session still ends one hour after it opened.
deep32_relay/answer_check.py is standard library only, has no side effects and calls no model; with footer it runs once per finished answer and the footer is sent as one more agent.delta before turn.completed, so it never enters the history.
The answer record (logs/answers/, newest 200 kept) holds no question, answer, passage, query or quotation; a record that cannot be written never fails the answer.
[Agent adapters section 5](../README.md#5-the-ask-relay) describes the settings for users and how to set them on each system.

## Status and optional live selftest

Status and the R6 doctor_status projection read only owned state and passive process identity; they do not run agents, read account files, bind listeners, open pairing or repair services.
Missing, stale or unverifiable process evidence cannot report a running relay.

~~~sh
adapters/connect.sh --relay selftest --relay-id INSTALL_ID --agent claude --live --json
~~~

~~~powershell
powershell -NoProfile -ExecutionPolicy Bypass -File adapters\connect.ps1 -Relay selftest -RelayId INSTALL_ID -Agent claude -Live -Json
~~~

The optional live selftest uses the user's model allowance for denied shell, outside-scratch write, web, other MCP and ordinary user-configuration attempts, plus an allowed Deep32 call against its private fake gateway.
This cost opt-in is separate from the no-model install check; synthetic tests or edited receipts cannot substitute for live observations.
Windows uses Job Object kill-on-close; Linux/macOS kill and wait for the new session/process group on cancel/close.
A descendant leaving the POSIX group could outlive cancellation; built-in tools are disabled so these agents start no tool processes.

## Removal and replacement

~~~sh
adapters/connect.sh --relay remove --relay-id INSTALL_ID --agent claude --tailscale-binary /absolute/path/to/tailscale --json
~~~

~~~powershell
powershell -NoProfile -ExecutionPolicy Bypass -File adapters\connect.ps1 -Relay remove -RelayId INSTALL_ID -Agent claude -TailscaleBinary 'C:\absolute\path\tailscale.exe' -Json
~~~

Omit the Tailscale executable for a loopback installation.
Removal stops the owned service, removes only the receipt-recorded task/unit/LaunchAgent, route and files, reads each back and reports anything still present.
Changed foreign objects or uncertain removal retain the journal for review.
The provider venv is removed from its recorded file hashes; if cleanup fails, JSON removal output names the remaining relative paths and a fixed local reason code.
Repeating install with the same ID, or omitting the ID when only one matching installation exists, finishes a recorded partial removal before rebuilding the provider and configuration.
The install receipt keeps the selected transport and port for reinstalls; explicit options override them, including an explicit default port of 7452.
An older receipt is updated from its validated configuration before removal; if an older removal already deleted that configuration, supply the previous transport and port or the loopback/7452 defaults apply.
State, independent profile, identity and pins survive; removal does not revoke Workstation trust.
Explicit Workstation revocation runs `/usr/local/d32/sbin/d32-chatctl unpair work` as root on the Workstation and unpair on the relay:

~~~sh
adapters/connect.sh --relay unpair --relay-id INSTALL_ID --agent claude
~~~

~~~powershell
powershell -NoProfile -ExecutionPolicy Bypass -File adapters\connect.ps1 -Relay unpair -RelayId INSTALL_ID -Agent claude
~~~

Unpair requires a stopped service, deletes only the stored Workstation pin and clears the pairing fields of the last status snapshot; the relay identity, profile sign-in, packages, provider and receipt remain.
Running it again with no stored pin changes nothing.
While a pin is stored, a different Workstation's certificate ends the pairing console with peer-revocation-required and the paired Workstation reconnecting ends it with peer-already-paired; both print the unpair command.
Stop the old Workstation's Ask link or turn it off before pairing a new one, because the first unpinned attempt consumes the window.

Alpha.3 has no in-place upgrade; remove then install the new verified source with the same ID to retain identity/pins and rerun checks. The instructions an installed relay gives its agent change only this way.
The source kit is covered by the release's signed metadata; local correspondence hashes do not replace release verification.
Shipped asklink takes precedence over C9's draft: thirteen types, research-v1, channels 1/2/4, ten-word SAS and empty confirmations/commits, with no PAIR_SHARE, channel 7, Ask-password or engine-selection operation.
Source-derived vectors supply offline evidence; the real Workstation R2 run supplies live interoperability evidence.
