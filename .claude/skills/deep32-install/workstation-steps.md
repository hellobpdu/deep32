# On the Workstation, and the steps after the wizard

These steps happen at the Workstation's console, in the order below. The wizard's Done screen lists them under "What's
next". [INSTALL.md section 6](../../../INSTALL.md#6-install-the-workstation) and
[section 8](../../../INSTALL.md#8-pair-the-parts) are the source. Every step here is the person's; you say what to do
and what they should see.

## 1. Create the VM

Use the VM guide and `hypervisors/<id>.md`. The values every recipe uses: BIOS firmware (Hyper-V Generation 1), 6
vCPUs and 12 GB of memory (at least 4 and 8 GB), a standard VGA display, the installer disk first, an empty target
disk of at least 200 GB, and one wired network card on a bridged or NAT network with DHCP and internet. deep32-get's
`commands.txt` still makes a 160 GB disk: change `160GB` (Hyper-V) or `160G` (KVM) to `200GB` or `200G` before
running it.

## 2. Install from disc 1 (stop point)

At the VM console. The installer asks, in this order:

1. keyboard layout;
2. machine name: the "Workstation name" typed in the VM guide (`deep32` unless changed);
3. whether to allow root maintenance over SSH with the person's own key (off unless they choose it);
4. the user name;
5. the disk to install on: the empty 200 GB disk, never the installer disk. The person confirms it by typing its
   name, because the whole disk is erased;
6. whether to encrypt that disk;
7. the time zone.

Enter accepts a default. The install takes about 15 minutes, then prints
`Remove the install medium before starting the installed system.` and
`The operating system has halted. Please press any key to reboot.`

Tell the person: do not press a key there. With disc 1 still first in the boot order, that starts the installer
again. Turn the VM off instead (on Proxmox VE, stop the VM). Then do the VM guide's step "Boot the installed disk;
attach disc 2" and start the VM.

## 3. First boot (stop points)

1. The person sets the passwords for their user and for root at the console.
2. The Workstation makes its own tokens and keys, then starts Tailscale. The sign-in address shows on the text console
   (Ctrl+Alt+F1) in a status line with `signin=https://login.tailscale.com/a/…`, on the VM's serial port every 10
   seconds if it has one, and in the **First Start** window under **Sign-in address**. As root, `tailscale status`
   prints it too.
3. The person opens the address on any device and signs in with their own account. If the address expires, Tailscale
   makes a new one and First Start shows it.
4. If the sign-in stalls: "The Workstation checks DNSSEC through the DNS server DHCP gives it. A few routers' DNS breaks
   that and the sign-in stalls. Then give the VM's network another DNS server."

Good result: the Tailscale admin console lists the Workstation as connected, and the VM guide's step "First boot and
Tailscale" shows OK.

## 4. Give the Workstation disc 2

Once the Workstation is signed in, it finds disc 2 and imports it by itself. Use the way the person chose.

**A CD drive.** In a VM, the VM guide's step "Boot the installed disk; attach disc 2" attaches it as a read-only
CD/DVD, in place of disc 1 or as a second drive. On a real machine, a 25 GB BD-R in an optical drive.

**A USB stick.** The person writes the disc 2 image to a stick of 32 GB or more with balenaEtcher or `dd` and plugs
it in. Writing erases the stick: the person chooses the stick and confirms it. You never write a disk.

**Over the network.** New in this release; the names are those of its design.

1. Pick the sending PC: on the same tailnet, with the verified disc 2 (the wizard's download) and Tailscale signed in.
2. On that PC the person starts the wizard's Send disc 2. On the wizard's Done screen, when it shows "To send disc 2
   from this PC over your tailnet, press S on this screen", they press S. Or they run it later: Linux as
   root `bash install/deep32-wizard.sh --send-disc2`; Windows in PowerShell as administrator
   `powershell -NoProfile -ExecutionPolicy Bypass -File install\deep32-wizard.ps1 -SendDisc2`.
3. It shows the PC's tailnet name and a one-time ten-character code, and serves disc 2 over the tailnet with HTTPS on
   port 9447. Enter stops sending; sending also stops when the wizard exits. Keep it running until the Workstation's
   download has finished.
4. On the Workstation, in First Start, the person chooses **Get Disc 2 over the Network**, or runs
   `doas /usr/local/d32/sbin/d32-content-fetch` on the console.
5. They choose the PC on the tailnet and type the code (stop point). Without a sending PC they choose the Internet
   Archive mirror instead.
6. The Workstation downloads the image to its empty update slot, carries on after an interruption, shows the progress,
   and deletes the image when the import is done.

## 5. The import

- The import checks disc 2's signature and the free space first, then reads the disc once, in two parts, checking
  each file's size and SHA-256 against the signed manifest.
- The knowledge base comes first. The console then says "the knowledge base is ready; the Library is still importing".
  From then on search, Ask, publication and pairing work. The Library follows in the background.
- On the lab VM INSTALL.md describes (KVM on Proxmox VE, disc 2 on a virtual CD drive) the knowledge base was ready about 8 minutes after
  sign-in, and the whole import took about 2 hours.
- Until disc 2 is imported, the boot messages list `kiwix_serve(failed)`. That is expected.
- The person does not start the import by hand. Progress: the desktop's **Import Report**, or
  `/usr/local/d32/libexec/d32-import-report`, or `/usr/local/d32/libexec/d32-content-import --report`.
- Only if Import Report shows that the import stopped: **Try Again** there, or
  `doas /usr/local/d32/sbin/d32-import-resume`, or as root `/usr/local/d32/libexec/d32-content-import --device cd0`
  (`dmesg | grep ^cd` names the CD drive).

Good result: Import Report shows the knowledge base in use. At the very end, `tail /var/log/d32-import.log` ends with a
`finish outcome=imported` line.

## 6. Pair the Forge (stop point)

Only after Import Report shows the knowledge base in use. Skip this for a keyword-only layout.

1. The Forge shows a ten-character code for five minutes: the wizard's "Pair" screen, or on a Linux Forge
   `sudo grep -o 'pair=[A-Z2-7]*' /opt/deep32/status/status`. A Windows Forge keeps it in
   `C:\Deep32\status\windows-pair` while `forge_pair_windows.py` runs.
2. On the Workstation the person opens **Pairing**, chooses **Forge**, picks the Forge and types the code. From a
   terminal: `doas /usr/local/d32/sbin/d32-pairdesk -forge`.
3. Pairing turns meaning search on by itself. Within a minute,
   `doas /usr/local/d32/sbin/d32-gpu-ext status` shows `"verified": true` for `embed` and `rerank`. The wizard's
   What's next also lists `doas /usr/local/d32/sbin/d32-gpu-ext on` before that check.

Three failed attempts within 30 minutes lock pairing for 15 minutes. Never delete the lockout files. If the window
closed, the wizard's "New code (runs the setup again)" opens a new one.

## 7. Publish the gateway (stop point)

Once, on the Workstation, as root on its console (Ctrl+Alt+F1):

```sh
/usr/local/d32/sbin/d32-publish publish --accept-ct
```

With `--accept-ct` the person agrees that the Workstation's HTTPS name, `<workstation>.<tailnet>.ts.net`, goes into
the public Certificate Transparency logs. That is their decision; without it publication stays off. The command checks
the gateway and ends with the Connect command for Linux and Windows, which contains the address
`https://<workstation>.<tailnet>.ts.net/mcp`. That command says `--agent both`, which pairs twice, once per agent.
`doas /usr/local/d32/sbin/d32-publish status` shows whether publication runs.

## 8. Connect the agent (stop point)

On the agent PC, with the wizard's Agent PC role (`wizard-screens.md` 2.14), or by hand from the clone:

```sh
bash adapters/connect.sh --agent claude --workstation 'https://<workstation>.<tailnet>.ts.net/mcp'
```

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File adapters\connect.ps1 -Agent claude -Workstation 'https://<workstation>.<tailnet>.ts.net/mcp'
```

Use `codex` for Codex. Before it starts: if this conversation runs in the agent home Connect will change, the person
closes this session first, or connects a separate profile
([adapters/README.md section 3](../../../adapters/README.md#3-use-a-separate-profile)).

1. Connect checks the gateway, then waits at `Workstation one-time code for claude (hidden, 10 characters):`.
2. On the Workstation the person opens **Pairing** and chooses **Headend** (or runs
   `/usr/local/d32/sbin/d32-pairdesk -headend` as root). It shows a ten-character code for five minutes.
3. The person types it at Connect's prompt. On the console, they press Enter afterwards to close the pairing screen.
4. If the agent already has a configuration, Connect asks `Type merge to approve this file plan:`. The person decides.
5. The person starts the agent with the `Launch:` command Connect prints.

Good result: the same Connect command with `--check` (`-Check`) changes nothing and ends with
`claude: read-only authentication and eleven-tool checks passed; client version …`.

## 9. Verify

1. On the Workstation, **Pairing** lists the Forge as paired, and a search ranks by meaning.
2. The Forge's door answers `401` on ports 443 and 8443:

   ```sh
   curl -s -o /dev/null -w '%{http_code}\n' https://deep32-forge.YOUR-TAILNET.ts.net/health
   ```

3. The person starts the agent with its launcher, approves only the eleven Deep32 tools (`rag_status`, `search_rag`,
   `search_many`, `get_section`, `get_outline`, `lookup_id`, `lookup_rfc`, `find_command`, `grep_corpus`,
   `list_documents`, `verify_quote`) and runs `/deep32 first-session check` (Claude Code) or
   `$deep32 first-session check` (Codex). A profile set up by Connect uses its scoped name, such as `deep32_1a2b3c4d`.
4. `rag_status` reports keyword search, embeddings and reranking as healthy. Embeddings or reranking "degraded" means
   the Forge is not reachable.
5. A question the person knows, such as the BGP hold time in RFC 4271, comes back with the RFC cited and quoted.

`deep32 doctor` and `deep32 selftest` give no useful result in 0.1.0-alpha.2.

## 10. The Ask relay (optional)

[adapters/README.md section 5](../../../adapters/README.md#5-the-ask-relay) has every command. The relay PC's
Tailscale name must start with `deep32-` and be in the Workstation's tailnet. The pairing compares ten words on each
side: the person runs `/usr/local/d32/sbin/d32-pairdesk -relay` as root on the Workstation's text console and types
each side's words at the other side's prompt (stop point).
