# Optional features

Deep32 ships with every optional feature switched off. None of them has an address, key, account, webhook or
relay target filled in, and nothing of mine is preset: a feature contacts something only after you set it up
and switch it on yourself. The installer lists them in an optional step and sets one up only when you ask.
(planned)

> **Status, 14 November 2026: pre-release.** This page describes version 1.0 as designed. Names may change
> before the release.


## The list

| Feature | In version 1.0 | Default | What it contacts when on |
|---|---|---|---|
| [Web switch](#web-switch) | Yes, as an option | Off | Any web address you open in the desktop's web browser |
| [Update check](#update-check) | Hidden until it is built | Off | The release source you install from (design not final) |
| [Health pings](#health-pings-and-phone-notifications) | Not in version 1.0 | | A health-check service you choose |
| [Phone notifications](#health-pings-and-phone-notifications) | Not in version 1.0 | | A notification service you choose |


## Web switch

The Workstation's packet filter normally gives its web browser no way out to the internet. The web switch opens
it.

- **Modes.** `off` (the default) and `direct`. In `direct` mode the desktop's web browser may reach any web
  address. The switch applies to the web browser only; nothing else on the Workstation gains internet access.
- **Turning it on.** From a terminal on the Workstation itself (it refuses when it is not run from a
  terminal): the Web switch control on the desktop, or `doas deep32ctl web direct`.
- **Turning it off.** The same control, or `doas deep32ctl web off`. Turning it off also ends the connections
  already open.
- **Checking it.** `doas deep32ctl web status` prints `OFF` or `DIRECT`. A lamp on the desktop's front panel is
  lit while the switch is on. (planned)
- **What it contacts.** Whatever sites you visit, and the name servers needed to find them. Nothing is
  contacted while it is off.


## Update check

A menu entry for checking for updates exists in the Deep32 Library, but it does nothing yet. In version 1.0 it
is hidden.

Its design is not settled. The likely place it would ask is the one you install from: the project's GitHub
releases, or nothing at all when you install from a folder. Like every other optional feature it would ship
off. Updates are applied with `deep32-install update` (planned), which checks the release signature first, as
described in [INSTALL.md](../INSTALL.md#4-verify-the-release-yourself).


## Health pings and phone notifications

Not in version 1.0. No code for either exists in the release.

If they are added later, a health ping would send a short "still running" message to a health-check service
whose address you give, and a phone notification would send a message to a notification service you choose.
Each would ship off, with no address or key, like the web switch.


## Other things that run only when you ask

These are not features you switch on, but each one contacts something or uses something of yours, so none
runs by default.

| What | When it runs | What it contacts or uses |
|---|---|---|
| `deep32-inventory report --net` | Only with `--net` | One HEAD request each to github.com, huggingface.co, archive.ubuntu.com, NVIDIA's CUDA package site and login.tailscale.com; asks the GitHub CLI whether it is signed in |
| `deep32-inventory bench quick` | Only when you run it | Nothing outside the machine; about 2 minutes of full processor and disk load |
| `deep32-inventory bench stress` (planned) | Only when you run it | Nothing outside the machine; up to 15 minutes of sustained load |
| The relay's smoke test (planned) | Only when you ask during `deep32-connect` | One question through Claude Code or Codex, which spends your own model usage |


## What a fresh install contacts with everything off

(planned; checked on the built discs before each release)

- Tailscale's own servers: for sign-in, key exchange and, when no direct path exists, its relays.
- Time servers.
- During the install only: the place the discs come from (GitHub, or nothing for a folder), and the package and
  model sources for any part that is not already on the discs.

Nothing of mine is contacted: no address, host name, tailnet, webhook or health-check service.
