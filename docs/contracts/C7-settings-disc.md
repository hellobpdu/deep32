# C7. Settings disc

Contract C7, version 1, draft of 2026-11-14. Not frozen. The three big discs are the same for every install. For
each node it creates, the installer writes a tiny fourth disc that holds names and choices only.
Written by `deep32-install`. Read by the install and first-boot scripts of disc 1 and disc 3. With no settings
disc, a node asks on its console instead.

## The rule

**No secret may ever be added to the settings disc, in this version or any later one.** That covers passwords,
passphrases, password hashes, PINs, recovery codes, Tailscale auth keys and login addresses, API keys, tokens,
private keys, pairing codes, cookies, sign-in files, an address with credentials in it, and anything derived from
any of these. Every key and token is made on the node at first boot or at pairing. A choice that needs
a secret, such as disk encryption, records only the choice; the secret is typed on the node's console.

## Form

An ISO 9660 image with Rock Ridge names, a few kilobytes in size, attached to the VM as a second optical drive. It
holds `settings.json` (ASCII JSON) and nothing else, except on a Forge, where it also holds the files Ubuntu's
unattended installer reads. Those are generated from `settings.json` and bound by the same rule.

## settings.json

| Field | Values | When absent |
|---|---|---|
| `format`, `format_version` | `"deep32-settings"`, `1` | the disc is refused |
| `role` | `workstation` or `forge` | the disc is refused |
| `node_name` | `^[a-z][a-z0-9-]{0,62}$`: the host name and the Tailscale machine name. With HTTPS certificates on, machine names are published in the public Certificate Transparency log, so it must carry nothing personal | asked |
| `user_name` | `^[a-z_][a-z0-9_-]{0,30}$`: the local account | asked |
| `time_zone` | an IANA name, such as `Europe/London` | asked |
| `keyboard` | a layout name | asked |
| `profile` | a profile id from the install matrix (C1) | asked |
| `hw_profile` | `vm` or `latitude-5430` | `vm` |
| `size` | `threads`, `mem_gb`, `disk_gb` the VM was given; a record only, the hypervisor sets them | omitted |
| `disk_encryption` | `true` or `false` | off in a VM, on for a real install |
| `transport` | `tailnet` or `loopback` (C8) | `tailnet` |
| `publish` | `serve-https` or `tailnet-plain` (C8) | `serve-https` |
| `features` | booleans only; version 1 knows `web_switch` | every feature off |
| `written_by` | the installer's name and version | omitted |

Today's laptop installer keeps the same kinds of value in its answer file (user, full name, domain, time zone, host
name). User, time zone and host name become the fields above; none of the old values remains as a default. The
optional features: the web switch is a choice here; the update check stays hidden until it is built; health pings and
notifications are not in version 1.0.

## Readers and writers

- A reader refuses an unknown field, a value of the wrong type or form, and a disc that holds any other file.
- The installer writes the file from this field list only, and its tests scan every disc it writes with gate G2's
  secret patterns.
- A new field needs a new version of this contract and, in the same change, a written check that the field cannot
  hold a secret.

## Open points

- OPEN: the default node names, and what a second install on the same tailnet does with a name already taken.
  Decides: the builder in stage 7.
- OPEN: the full name and the domain of today's answer file: new fields, or dropped. Decides: the builder in
  stage 5.
- OPEN: the volume label, and whether one label serves both roles: the Forge's Ubuntu installer has its own rule
  for finding its files, to be checked in stage 4. Decides: the builder in stages 4 and 5.
- OPEN: whether Ubuntu's unattended installer runs with no password field at all (the account locked until the
  first console sign-in). If it cannot, the rule wins and the builder stops and asks the maintainer (stage 4).
- OPEN: how the Workstation's installer finds a second disc. Today it finds one medium by its disk id and has no
  code for another optical disc. Decides: the builder in stage 5.
- OPEN: how a real install (`baremetal`) gets its settings, since no hypervisor attaches a disc there (a USB stick
  with the same image, or the console). Decides: the builder in stage 5.
- OPEN: static network settings. Version 1 assumes DHCP. An address is a choice and holds no secret, so it may be
  added. Decides: the builder.
