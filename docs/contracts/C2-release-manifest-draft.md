# C2. Release manifest

Contract C2, version 1, DRAFT of 2026-11-14. Written by the release tooling; read by
`install/verify-release.ps1`, `install/verify-release.sh` and the installer. Once frozen, any change raises
the version and needs the release tooling, both verifiers and the installer retested.

Changed 2026-11-14 (same draft, never released): the manifest is signed with the release key holder's
OpenPGP key as a detached, armored signature, checked with `gpgv` against the public key shipped in
`install/` and a pinned fingerprint. The earlier OpenSSH signatures are gone; there is no unsigned mode.

## 1. What a release is

- Three discs, each with a role: `system` (disc 1, the Workstation installer, bootable), `content` (disc 2,
  data), `gpu` (disc 3, the Forge, bootable). Each disc has its own version.
- Each disc is uploaded as numbered parts to the GitHub release of its own tag, `<role>-<version>`.
- One code release, tag `v<version>`, holds `MANIFEST.json` and `MANIFEST.json.asc`. Its bill of materials
  names the three disc versions that go together.
- A disc that did not change is carried: the new manifest names the earlier disc release and repeats its
  part list; the parts stay where they are.

## 2. Parts

- Name: `deep32-<role>-<disc version><ext>.partNNN`, NNN from `001`, three digits (at most 999 parts).
- Size: every part except the last is exactly `part_size` bytes; the last holds the rest. `part_size` is at
  most 1,900 MiB (1,992,294,400 bytes), under GitHub's 2 GiB limit per release file. A GitHub release holds
  at most 1,000 files.
- Joining: the parts in index order, concatenated, are the disc image named `file`.

## 3. MANIFEST.json

ASCII JSON, two-space indent, LF line ends, a final newline, keys in the order below. The signature covers
these exact bytes: a reader verifies the bytes it read, then parses those same bytes, and never re-serialises
before verifying. Hashes are lower-case hex SHA-256; sizes are bytes.

| Field | Value |
|---|---|
| `format`, `format_version` | `"deep32-release-manifest"`, `1`. Readers refuse any other |
| `release.version`, `release.tag` | e.g. `"1.0.0"`, `"v1.0.0"` (tag defaults to `v<version>`) |
| `release.created` | `YYYY-MM-DDTHH:MM:SSZ`, given to the tool, never read from the clock, so the same inputs give the same bytes |
| `release.complete` | `true` when all three roles are present. Only complete manifests are released |
| `author.name`, `.contact_email`, `.github`, `.notice` | the visible attribution, from the project's single authorship record |
| `signer` | `{"type": "openpgp", "fingerprint": "<40 upper-case hex digits>"}`: the primary-key fingerprint of the release key that signs this manifest |
| `introduces` | list of `{"type": "openpgp", "fingerprint": ...}`: next release keys this release introduces (normally empty) |
| `bill_of_materials` | list of `{role, version, tag, file, size, sha256}`, one per disc |
| `discs` | list in role order `system`, `content`, `gpu`, each `{role, version, tag, file, size, sha256, part_size, parts}` |
| `discs[].parts` | list in order, each `{index, name, size, sha256}`, index from 1 |

Names (`file`, part names) match `[A-Za-z0-9][A-Za-z0-9._-]*`; a verifier refuses anything else.

## 4. Signature and keys

- The release key is an OpenPGP key (today: Ed25519, version 4, held by the release key holder only).
- Signing, by the key holder only, in the folder holding the manifest:
  `gpg --local-user <fingerprint> --armor --detach-sign --output MANIFEST.json.asc MANIFEST.json`.
  Git tags and commits of the repository are signed with the same key (`git tag -s`).
- Shipped in `install/` of the repository: `release-key.asc` (the public key, armored),
  `release-key.gpg` (the same key, binary: the keyring `gpgv` reads) and `release-key.fingerprint`
  (line 1: the 40 hex digits; line 2: the same, spaced as GnuPG prints it). The fingerprint is also printed
  in the README.
- Check, exactly: `gpgv --homedir <new empty folder> --keyring <path of install/release-key.gpg>
  --status-fd 1 MANIFEST.json.asc MANIFEST.json`. The keyring path must contain a `/` (a bare name is looked
  for in gpgv's own folder), and its form depends on the gpgv build (as checked on 2026-11-14): the gpgv of Linux,
  macOS and Gpg4win takes an ordinary full path (on Windows written `C:/...`); the gpgv of Git for Windows, an
  MSYS2 program, does not accept a `C:/...` path (it reads it as a key resource and reports
  `NO_PUBKEY`) and needs `./release-key.gpg`, relative to the current folder, or `/c/...`. The shipped
  verifiers write the keyring and the `--homedir` paths in the form the gpgv they found takes. The person's
  own GnuPG keyring is never used. The signature
  is good only when `gpgv` exits 0 and its status lines hold exactly one `GOODSIG` and one `VALIDSIG`, none
  of `BADSIG`, `ERRSIG`, `EXPSIG`, `EXPKEYSIG`, `REVKEYSIG` or `NO_PUBKEY`, the signature class in `VALIDSIG`
  (its ninth field, just before the primary-key fingerprint) is `00`, a binary signature over the exact
  bytes (a text-mode signature, class `01`, would also match the manifest with other line ends, and is
  refused), and the last field of `VALIDSIG` (the primary-key fingerprint) equals the pinned fingerprint. A
  signature made by a signing subkey of the release key is accepted: the pin is always the primary key's.
  A non-detached or clear-signed message given as `MANIFEST.json.asc` is refused (`gpgv` reports no
  signature over the data file). `signer.fingerprint` must equal the pin too.
- Where `gpgv` comes from: Linux distributions ship it; on Windows it comes with Git for Windows
  (`C:\Program Files\Git\usr\bin\gpgv.exe`) and with Gpg4win; macOS users run `brew install gnupg` once.
  A verifier looks for it on PATH, then in those Windows places. Not found is its own exit code, never a pass.
- Expiry: a signature by a key that has expired by the time of the check gives `EXPKEYSIG` and is refused.
  The key holder extends the expiry before it lapses and the repository's `install/release-key.*` are
  refreshed, so a current checkout always verifies.
- Changing the key: a release signed with the current key lists the next key in `introduces`. The installer
  adds a key to its trusted list only from a manifest that verified under a key it already trusts. Later
  releases are signed with the new key, and the repository then ships the new key and fingerprint.

## 5. Verification order (both verifiers, normative)

1. The signature is good under the rules of section 4, against the shipped key and the pinned fingerprint,
   and `signer` names that same key. Nothing in the manifest is used before this.
2. Form: format and version, roles, safe names, hash and size forms, requested roles present.
3. Every part: present, right size, right SHA-256. All parts are checked and every bad one is listed;
   nothing is joined if any fails.
4. Each disc is joined into `<file>.partial`, its size and SHA-256 are checked, and only then is it renamed
   to `<file>`. A disc that fails is deleted.

| Exit | Meaning |
|---|---|
| 0 | every check passed (and every disc was joined, unless told not to join) |
| 1 | usage error |
| 2 | an input file or folder is missing or unreadable, or `release-key.fingerprint` is malformed |
| 3 | `gpgv` was not found or could not be started |
| 4 | the signature is not good, is not by the pinned release key, or `signer` names another key |
| 5 | the manifest is not usable: not JSON, unknown format or version, unsafe names, a requested role absent |
| 6 | a part is missing or has the wrong size |
| 7 | a part has the wrong SHA-256 |
| 8 | a joined disc has the wrong size or SHA-256 (it is deleted) |
| 9 | a joined disc, or the verifier's private work folder, could not be written |
| 10 | (shell verifier only) another needed tool is missing: a SHA-256 tool |

## 6. For INSTALL.md: verify it yourself

You do not have to trust the installer. Download the parts of each disc, `MANIFEST.json` and
`MANIFEST.json.asc` into one folder, and take `install/release-key.gpg` and `install/release-key.fingerprint`
from the repository. Compare the fingerprint in that file with the one printed in the README. Then run
`gpgv --keyring ./release-key.gpg --status-fd 1 MANIFEST.json.asc MANIFEST.json` (write the keyring exactly
as `./release-key.gpg`: a name without `/` is looked for in gpgv's own folder, and the gpgv of Git for Windows
refuses a `C:/...` path, though `/c/...` works). `gpg` is not needed for this check; it only
shows the key's fingerprint, if you want to see it. It must print a `GOODSIG` line and a `VALIDSIG`
line whose last field is that fingerprint and whose field before it is `00`. Every part's SHA-256 (`Get-FileHash` on Windows, `sha256sum` on
Linux, `shasum -a 256` on macOS) must equal the one in the manifest; the parts joined in order must give the
disc's `sha256`. `install/verify-release.ps1` and `install/verify-release.sh` do exactly these steps.

## 7. Example (abridged)

```json
{
  "format": "deep32-release-manifest",
  "format_version": 1,
  "release": {"version": "1.0.0", "tag": "v1.0.0", "created": "2026-11-14T00:00:00Z", "complete": true},
  "author": {"name": "...", "contact_email": "...", "github": "...", "notice": "Copyright (C) 2026 Jeffrey A. Cummings"},
  "signer": {"type": "openpgp", "fingerprint": "B205B406548627AABF5250D8A078C5F25D025DD6"},
  "introduces": [],
  "bill_of_materials": [
    {"role": "system", "version": "1.0.0", "tag": "system-1.0.0", "file": "deep32-system-1.0.0.iso",
     "size": 3221225472, "sha256": "..."}
  ],
  "discs": [
    {"role": "system", "version": "1.0.0", "tag": "system-1.0.0", "file": "deep32-system-1.0.0.iso",
     "size": 3221225472, "sha256": "...", "part_size": 1992294400,
     "parts": [{"index": 1, "name": "deep32-system-1.0.0.iso.part001", "size": 1992294400, "sha256": "..."},
               {"index": 2, "name": "deep32-system-1.0.0.iso.part002", "size": 1228931072, "sha256": "..."}]}
  ]
}
```
