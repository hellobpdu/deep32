# C7. Settings disc

Contract C7, version 2, draft of 2026-11-17. Not frozen. Settings hold names
and choices only, separate from the common release payloads. Workstation install and first-boot
scripts read the settings-disc choices; the current Docker Forge lifecycle receives protected `settings.json`
instead of creating a fourth disc. The retained legacy Forge ISO/autoinstall interface also
reads settings media. With no settings disc, Workstation asks on its console; the legacy
unattended Forge interface stops unless its required settings are supplied.

Changed: role-specific validation and defaults; thin-form settings handoff; Forge publication choices; resolved media, account and discovery decisions.

## The rule

**No secret may ever be added to the settings disc, in this version or any later one.** That covers passwords,
passphrases, password hashes, PINs, recovery codes, Tailscale auth keys and login addresses, API keys, tokens,
private keys, pairing codes, cookies, sign-in files, an address with credentials in it, and anything derived from
any of these. Every key and token is made on the node at first boot or at pairing. A choice that needs
a secret, such as disk encryption, records only the choice; the secret is typed on the node's console.

## Form

The intended settings-disc form is an ISO 9660 image with Rock Ridge names, attached as another optical drive;
the ISO writer and image-size limit are not established by this source. Workstation media
holds `settings.json` (ASCII JSON) and nothing else; the retained legacy Forge ISO interface also requires the
Ubuntu unattended-install files generated from those choices and bound by the same no-secret rule.

The source renders seed files and writes protected JSON for the thin forms; a general settings-ISO writer and
automatic optical attachment are still unverified. The Workstation reader limits `settings.json`
to 4096 bytes in canonical sorted, indented form; the Forge reader accepts bounded ASCII JSON up to 65536 bytes.

## settings.json

| Field | Values | When absent |
|---|---|---|
| `format`, `format_version` | `"deep32-settings"`, `1` | the disc is refused |
| `role` | `workstation` or `forge` | the disc is refused |
| `node_name` | `^[a-z][a-z0-9-]{0,62}$`; Workstation also refuses `localhost`. Workstation sets its host and Tailscale machine name; Docker Forge and the legacy WSL interface use the owning host's existing Tailscale identity without renaming it. HTTPS certificates publish machine names in Certificate Transparency, so use nothing personal. | Workstation asks; Forge requires it; clone-run prompt defaults to `deep32-forge` |
| `user_name` | Forge: `^[a-z_][a-z0-9_-]{0,30}$`, except `root`; Workstation requires an initial lowercase letter and refuses its system-account list. The local account. | Workstation asks; Forge requires it; clone-run prompt defaults to `deep32` |
| `time_zone` | a zone such as `Europe/London`; Workstation checks its syntax and available zone list, while Forge checks a restricted name syntax without zone-database membership. | Workstation asks; Forge requires it; clone-run prompt defaults to `Etc/UTC` |
| `keyboard` | a layout name; Workstation checks a supplied layout with `kbd`, and Forge checks its allowed string form | Workstation asks; Forge requires it; clone-run prompt defaults to `us` |
| `profile` | a profile id from the install matrix (C1); Workstation accepts its supported subset, and the clone-run writer loads ids from `install/matrix.json` | Workstation records `unset`; Forge requires it; clone-run prompt defaults to `two-machines` |
| `hw_profile` | `vm` or `latitude-5430` | `vm` |
| `size` | positive integers `threads`, `mem_gb`, `disk_gb` the VM was given; Workstation caps each at 65535; a record only, the hypervisor sets them | omitted |
| `disk_encryption` | `true` or `false`; unattended Forge accepts only `false`, otherwise use console provisioning | Workstation: off for `vm`, on for `latitude-5430`; unattended real Forge requires explicit `false` |
| `transport` | readers and pairing core accept `tailnet` or `loopback` (C8); the Docker Forge lifecycle requires `tailnet` | `tailnet` |
| `publish` | readers and pairing core accept `serve-https` or `tailnet-plain` (C8); clone-run and Docker Forge publication require `serve-https` | `serve-https` |
| `features` | booleans only; version 1 knows `web_switch` | every feature off |
| `written_by` | the installer's name and version | omitted |
| `forge_ports` | Forge Docker only: integer ports from 1 to 65535, with the six names below; booleans, strings, unknown names and duplicate local or published ports are refused | defaults below |
| `forge_accept_ct` | Forge Docker only: boolean agreement to publishing the node name in Certificate Transparency when HTTPS certificates are requested | publication refuses until explicitly `true` |

`forge_ports` may override any subset of `embeddings` (8081), `reranker` (8082), `pairing` (7453),
`embeddings_https` (443), `reranker_https` (8443) and `pairing_tcp` (7453). The first three are host
loopback listeners; the last three belong to the owning host's tailnet-only Serve. Docker's internal model
ports stay private. Settings contain no service URL or credential: the runtime derives model URLs from the
signed-in host name and these integers, then hands them over inside authenticated Forge pairing. Both
new fields are incapable of holding text or a secret; the C7 reader tests their types, ranges and names.

The Forge refuses an occupied local port or any unowned route on a selected published port. It does not
alter the conflicting service. A host already using 443/8443 and 8081/8082 can select HTTPS 18443/19443
and loopback 18081/18082. Revoke and unpair both nodes before changing a paired Forge's ports.

The Workstation installer maps user, time zone and host name to the fields above; its full name uses the installer's
default and its DNS domain is `invalid`, with no C7 fields for either. The clone-run writer's
defaults are generic choices, not personal values from a previous installation. The
optional features: the web switch is a choice here; the update check stays hidden until it is built; health pings and
notifications are not in version 1.0.

## Readers and writers

- A reader refuses an unknown field, a value of the wrong type or form, and a disc that holds any other file.
- The installer writes the file from this field list only, and its tests scan every disc it writes with gate G2's
  secret patterns.
- A new field needs a new version of this contract and, in the same change, a written check that the field cannot
  hold a secret.

## Open points

- The clone-run Forge prompt defaults to `deep32-forge`; Workstation asks for its node name and writes it to
  `link.conf`. Same-tailnet name-collision handling remains OPEN; thin Forge forms consume the
  owning host's actual name rather than choosing or renaming it.
- Full name and DNS domain are not C7 fields: the Workstation uses the installer's full-name default and the
  fixed domain `invalid`.
- The retained legacy Forge ISO interface requires volume label `CIDATA`, with exactly `settings.json`, `user-data` and `meta-data`; it checks the
  seed against the approved renderer. Workstation requires no volume label and accepts only
  `settings.json` on the selected settings medium.
- The retained legacy Ubuntu seed has no password field, sets `lock_passwd: true` and disables root; its ISO first boot sets the
  account password on the protected console. This is the source decision, not proof of an
  executed Ubuntu install.
- Workstation enumerates optical devices from `hw.disknames`, mounts partition `c` or the only data partition
  read-only, and refuses more than one medium containing `settings.json`.
- Baremetal Workstation can use matching optical settings media or console answers; there is no separate
  non-optical USB settings reader in this source.
- OPEN: static network settings. Version 1 assumes DHCP. An address is a choice and holds no secret, so it may be
  added. Decides: the builder.

Sources: `src/workstation/distrib/site/usr/local/d32/libexec/d32-settings.sh` (field predicates, validation and defaults);
`src/workstation/ship/install/d32-ramdisk.ksh` (`_d32load`, `_d32resp`);
`src/workstation/ship/install/auto_install-guided.conf.in`;
`src/workstation/distrib/site/etc/d32/site.d/90-node-settings.sh`;
`src/gpu-pack/autoinstall/render-settings.py`; `src/gpu-pack/autoinstall/select-settings.py`;
`src/gpu-pack/autoinstall/user-data`;
`src/gpu-pack/firstboot/firstboot.py` (`local_account`, `signin`);
`src/release-tools/deep32_settings.py` (`DEFAULTS`, `collect`, `install`);
`src/gpu-pack/forms/forge_docker_role.py` (`settings`, `preflight_publication`, `prepare`);
`src/gpu-pack/forms/forge_publication.py` (`ports`, `plan`, `endpoints`).
