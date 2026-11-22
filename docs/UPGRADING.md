# Upgrading Deep32

How to move a Deep32 install to a new release: what each part keeps, what it makes again, and the order of the steps.
For anyone running 0.1.0-alpha.2 or later.

<sub>Applies to 0.1.0-alpha.3 · [1 Before you start](#1-before-you-start) · [2 What each part keeps](#2-what-each-part-keeps) · [3 Moving to a new release](#3-moving-to-a-new-release) · [4 From 0.1.0-alpha.1](#4-from-010-alpha1)</sub>

## 1. Before you start

0.1.0-alpha.2 was the first release whose images install with one command per machine and pair with each other. This
page describes what the tools support. From 0.1.0-alpha.3 on, each release's notes say how to move to it from the
release before; for 0.1.0-alpha.3 that is Procedure 1 below, because both the Workstation and the Forge have new
images, and disc 2 stays the same.

Read the new release's [notes](../CHANGELOG.md) first; they say which parts changed. Then, on each machine, check out
the new release's tag in your clone and check its signature as in [INSTALL.md section 3](../INSTALL.md#3-get-and-verify-the-source):

```sh
git fetch --tags origin
git checkout TAG
git verify-tag TAG
```

`TAG` is the new release's tag, for example `v0.1.0-alpha.3`. The same commands work in PowerShell. Use the scripts
from this checkout for every step below.

## 2. What each part keeps

| Part | How it moves to a new release | What carries over |
|---|---|---|
| Source and scripts | Check out the new tag (section 1) | Nothing to carry over: the setup scripts, the inventory, `deep32-get` and Connect run from the checkout |
| Workstation | A fresh install from the new disc 1, then the content import from disc 2 | Nothing. It makes new tokens and keys, so the Forge, every agent and the Ask relay pair with it again |
| Forge | A fresh install into an empty data folder | Nothing; it makes a new token and certificate and pairs again. Section 2.1 covers the `upgrade` action |
| Agent on a headend | Run Connect again after a source update (section 3.1); after a new Workstation, connect it into a new profile (section 3.3) | Its credential and the files Connect installed, while the Workstation it paired with stays |
| Ask relay | Remove it, then install it from the new checkout: with the same install ID while the Workstation stays, with a new one after a new Workstation (section 3.3) | With the same install ID: its profile's sign-in, its identity and its pin of the Workstation |

### 2.1 The Forge's upgrade action

The Forge's lifecycle has an `upgrade` action that installs a new release's GPU media over an existing Forge. It keeps
the data folder and the Forge's pairing identity, records the release it replaced in `previous.json`, and is the only
way the Forge accepts different media into an existing data folder
([src/gpu-pack/PACK.md](../src/gpu-pack/PACK.md#lifecycle-and-recovery)). The setup scripts and `deep32-get --install`
don't run it: they install into an empty folder or again over the same release. So far only parts of the action have
run, in the test suite against stand-ins for Docker (`src/gpu-pack/tests/test_forge_docker_role.py`), and never
between two real releases. Until a release's notes give steps for it that I have run, move a Forge with a fresh
install.

## 3. Moving to a new release

I haven't run these sequences between two releases yet. If a step fails, please open an
[issue](https://github.com/hellobpdu/deep32/issues) with the bug report form.

### 3.1 When only the source changes

1. Check out the new tag on each machine (section 1).
2. For each agent, run the Connect command you used before
   ([agent adapters section 2](../adapters/README.md#2-connect-an-agent)). It already holds the agent's credential, so
   it doesn't pair again. If the new release changed the files it installs, it lists the ones it will replace, waits
   for you to approve the merge and keeps a backup of each ([section 8](../adapters/README.md#8-what-connect-changes)).
   Then run the same command with `--check` (`-Check` in PowerShell): it changes nothing and checks the agent's
   sign-in, authentication and the eleven tools.
3. If you use the Ask relay and the release notes say it changed, remove it and install it again with the same install
   ID ([agent adapters section 5](../adapters/README.md#5-the-ask-relay)).

### 3.2 When the Forge changes

1. On the Workstation, open **Pairing** and revoke the Forge.
2. On the Forge, uninstall it, delete its data folder and remove its `deep32-local/*` images, as the Removal item in
   [INSTALL.md section 10](../INSTALL.md#10-afterwards) describes.
3. Install the new release as in [INSTALL.md section 5](../INSTALL.md#5-install-the-forge).
4. Pair it with the Workstation ([INSTALL.md section 8](../INSTALL.md#8-pair-the-parts), Procedure 4).

### 3.3 When the Workstation changes

A reinstalled Workstation has new keys, so everything pairs with it again. That includes the Forge: a Forge that is
already paired opens no new pairing window ([INSTALL.md section 8](../INSTALL.md#8-pair-the-parts)), and on an
0.1.0-alpha.2 Forge, revoking a Workstation leaves the Forge unable to restart (0.1.0-alpha.3 fixes that,
[INSTALL.md section 10](../INSTALL.md#10-afterwards)). So the Forge is installed again too.

Agents and the Ask relay also keep what tied them to the old Workstation, and 0.1.0-alpha.2 has no command that drops
it. Connect keeps using the credential it holds for the Workstation's address and doesn't pair again, so an agent
connected at the same address gets `401` from the new Workstation. The relay keeps the old Workstation's certificate
pin, and its pairing window can't replace a stored pin. So each agent is connected into a new, empty profile folder,
and the relay is installed under a new install ID.

**Procedure 1. Move a whole install to a new release**

1. Copy off anything of yours on the Workstation that you want to keep, such as files in your home folder. The guided
   install erases the disk it installs on.
2. Check out the new tag on each machine (section 1).
3. On the Forge, uninstall it, delete its data folder and remove its `deep32-local/*` images
   ([INSTALL.md section 10](../INSTALL.md#10-afterwards)), then install the new release
   ([INSTALL.md section 5](../INSTALL.md#5-install-the-forge)).
4. If the old Workstation is still listed on the Machines page of your Tailscale admin console, remove it, so that the
   new one can take the same name. Then install the new Workstation, sign it in and import disc 2
   ([INSTALL.md section 6](../INSTALL.md#6-install-the-workstation) and [section 7](../INSTALL.md#7-tailscale)).
5. Pair the Forge with the new Workstation and publish the gateway
   ([INSTALL.md section 8](../INSTALL.md#8-pair-the-parts)).
6. Connect each agent into a new, empty profile folder
   ([agent adapters section 3](../adapters/README.md#3-use-a-separate-profile)). If the agent was connected in a
   separate profile before, delete that old folder. If it was connected in its default profile, the old Deep32 entry
   stays there and no longer works.
7. If you use the Ask relay, remove it. Then install it from the new checkout as in
   [agent adapters section 5](../adapters/README.md#5-the-ask-relay), adding `--relay-id` (`-RelayId` in PowerShell) with a new install
   ID of 32 lowercase hexadecimal characters, such as the one `python3 -c "import secrets; print(secrets.token_hex(16))"`
   prints. Use that ID for its sign-in and pairing.

**Verify.** Run the checks in [INSTALL.md section 9](../INSTALL.md#9-verify). On a Linux Forge, `--status`
([INSTALL.md section 10](../INSTALL.md#10-afterwards)) prints whether its three services are healthy and its routes
are published.

## 4. From 0.1.0-alpha.1

The 0.1.0-alpha.1 images were withdrawn on 17 November 2026 and never ran end to end, so there is nothing to carry
over. Install 0.1.0-alpha.2 on every machine as [INSTALL.md](../INSTALL.md) describes, from a clone checked out at
`v0.1.0-alpha.2`.

---

<p align="center"><sub><a href="../README.md">Overview</a> · <a href="../INSTALL.md">Installation</a> · <a href="HARDWARE.md">Hardware</a> · <a href="OPTIONAL-FEATURES.md">Optional features</a> · <a href="tr/README.md">Design</a> · <a href="DECISIONS.md">Decision record</a> · <a href="contracts/README.md">Contracts</a> · <a href="../adapters/README.md">Agent adapters</a> · <a href="../.github/SECURITY.md">Security</a> · <a href="../CHANGELOG.md">Release notes</a></sub></p>
