# C2. Release manifest

Contract C2, version 2, DRAFT of 2026-11-17.
Written by the release tooling; alpha.2 outer metadata is read by
`src/release-tools/verify-release.ps1`, `src/release-tools/verify-release.sh` and the download helpers.
The `install/verify-release.*` scripts retain the older signed `MANIFEST.json` interface.
Once frozen, any change raises the version and needs the release tooling, both verifiers and the installer retested.

Changed 2026-11-17: whole objects, MIRRORS version 2 and GPU bundles, signed root and child
checksum lists, explicit metadata coverage, native GnuPG verification, verification scopes and exit codes.


## 1. What a release is

- Three logical disc roles: `disc1` / `workstation` (the Workstation installer, bootable),
  `disc2` / `content` (data), and `disc3` / `gpu-pack` (the Forge).
- Alpha.2 uses release tag `v0.1.0-alpha.2` and architecture `amd64`.
  Whole-object assembly refuses pending producers and requires the exact declared transports.
- MIRRORS version 2 lists the Workstation and content images in `discs`, and GPU media in
  `bundles`: shared `core`, all seven `embedder-<class>` objects, shared `ingestion`, and declared optional companions.
  The classes are `turing`, `ampere80`, `ampere86`, `ada`, `hopper`, `blackwell100` and
  `blackwell120`.
- GitHub holds the small release assets listed by `ASSETS.txt`; whole images are fetched from
  the authenticated mirror URLs.
- The content image is carried unchanged from alpha.1; its external release metadata changes.
  An SPDX document ships beside each image, covered by the signed root checksum list.
  No SBOM is embedded in an image.

## 2. Whole objects

- Names are `deep32-0.1.0-alpha.2-disc1-workstation-amd64.img` or `.iso`,
  `deep32-0.1.0-alpha.2-disc2-content.iso`, and `deep32-0.1.0-alpha.2-disc3-gpu-pack-<bundle_id>.iso`.
- Each object is between 1 byte and 512 GiB (549,755,813,888 bytes).
  Its ordered SHA-256 list describes consecutive 64 MiB (67,108,864-byte) ranges; the final
  range holds the remainder, with at most 8,192 ranges.
  These are signed range hashes within one object, not separate part files.
- Alpha.2 has no numbered release parts, joining step or `ASSEMBLY.json`.
  Legacy split verification and signing require `--legacy-split` or `-LegacySplit` explicitly.
- The assembled small-upload list has at most 1,000 entries.

## 3. MIRRORS.json and checksums

Canonical JSON is ASCII, with sorted keys, two-space indentation, LF line endings and a final newline.
The root signature authenticates the original `SHA256SUMS` bytes; raw `MIRRORS.json` bytes
must match their authenticated root hash before JSON parsing.
Hashes are lowercase hexadecimal SHA-256, and sizes are bytes.

| Field | Value |
|---|---|
| `format`, `format_version` | `"deep32-mirrors"`, `2` for new alpha.2 assembly; version 1 remains readable for verification |
| `release`, `architecture` | `"v0.1.0-alpha.2"`, `"amd64"` |
| `revision` | Integer from 1 through 2,147,483,647; independent of the release tag |
| `source_commit` | 40 lowercase hexadecimal digits |
| `published_at` | Supplied UTC `YYYY-MM-DDTHH:MM:SSZ`; no clock-derived assembly value |
| `citation` | `concept_doi` and `release_doi`; the concept DOI is `https://doi.org/10.5281/zenodo.24272647` |
| `archive` | `item`, `details_url`, `torrent_url`, bound to the release's Internet Archive item |
| `capability_manifest` | `{file, sha256}` binding the original capability-manifest bytes |
| `discs` | Exactly `disc1` / `workstation` and `disc2` / `content`; each `{role, kind, file, size, sha256, chunk_bytes, chunk_sha256, mirrors}` |
| `bundles` | GPU transports, each `{bundle_id, bundle_sha256, file, size, sha256, chunk_bytes, chunk_sha256, mirrors}`; `bundle_sha256` hashes the canonical exact capability-bundle definition |
| `chunk_bytes`, `chunk_sha256` | `67108864` and an ordered list of SHA-256 hashes, with exactly `ceil(size / chunk_bytes)` entries |
| `mirrors` | From 1 through 16 ordered `{id, url}` entries; the first is the canonical Internet Archive download URL |

Flat filenames match `[A-Za-z0-9][A-Za-z0-9._-]*`, at most 200 characters, with no `..`,
trailing dot, reserved device stem or case-colliding duplicate.
Links, reparse points and special files are refused.

Root `SHA256SUMS` covers every whole object, `MIRRORS.json`, the capability manifest,
`ASSET-MAP.json`, `ASSETS.txt`, all three child checksum lists and every supplied small asset.
Each child list covers its own image or GPU transports, its fixed README and explicitly mapped
sidecars; a sidecar must also be covered by the root.
Neither the root nor a child list hashes itself or any signature.
`ASSETS.txt` lists exactly the sorted small uploads and four future signature names.
Ordinary small metadata is limited to 5 MiB, and the explicitly bound capability manifest to 8 MiB.
The assembler takes an explicit metadata source list and child memberships, never a directory sweep.

## 4. Signature and keys

- The release key is an OpenPGP key (today: Ed25519, version 4, held by the release key holder only).
- Only the key holder signs, using
  `src/release-tools/sign-release.sh --release-dir <folder> --key-id <fingerprint>`.
  After whole-metadata preflight, the script signs the three child lists, then the root,
  with `gpg --local-user <key> --detach-sign --armor --output <checksum>.sig <checksum>`.
  Existing signatures are never overwritten, and `--dry-run` invokes no signing command.
  Git tags and commits of the repository are signed with the same key (`git tag -s`).
- Shipped in `install/` of the repository: `release-key.asc` (the public key, armored),
  `release-key.gpg` (the same key, binary: the keyring `gpgv` reads) and `release-key.fingerprint`
  (line 1: the 40 hex digits; line 2: the same, spaced as GnuPG prints it). The fingerprint is also printed
  in the README.
- Alpha.2 outer verification uses GnuPG `gpg`, imports one supplied public key into a fresh
  private temporary home, and pins its primary fingerprint to the caller's expected 40- or 64-digit value before
  checking `SHA256SUMS.sig` and each child signature.
  Secret-key input, automatic key retrieval and an index-selected key are refused.
  The person's own GnuPG keyring is never used.
  A signature must have binary class `00`, never text class `01`, to bind exact bytes.
  Successful verification requires GnuPG success and exactly one `VALIDSIG` from the pinned
  primary, including a signing subkey whose reported primary matches the pin.
  There is no unsigned mode.
- GnuPG discovery uses an explicit executable when supplied; otherwise it checks native
  GnuPG/Gpg4win locations on Windows, then PATH, and skips implicit Git/MSYS `gpg.exe`.
  A missing working GnuPG executable is a needs-action failure.
- Expiry: a signature by a key that has expired by the time of the check gives `EXPKEYSIG` and is refused.
  The key holder extends the expiry before it lapses and the repository's `install/release-key.*` are
  refreshed, so a current checkout always verifies.
- Trust changes require the caller's expected public key and primary fingerprint; the outer
  format has no `introduces` field or automatic key enrollment.
- The retained `install/verify-release.*` scripts and the content importer use the separate
  `MANIFEST.json.asc` / `gpgv` interface, including the pinned signer, exact `GOODSIG`/`VALIDSIG` counts, bad-status
  refusals and binary signature-class check.

## 5. Verification order (both verifiers, normative)

1. Authenticate the root checksum list using the supplied public key and expected primary
   fingerprint before using release descriptors or payload URLs.
2. Hash raw MIRRORS bytes, parse and validate format, roles, safe names, sizes, hashes and
   the capability-manifest byte and exact-bundle bindings.
3. Check exact small-file coverage, sizes and hashes, `ASSETS.txt`, ASSET-MAP and all child
   memberships; missing and unexpected metadata are refused.
4. With explicit `metadata` scope, omit only the declared whole objects and report that
   image bytes were not checked.
   With explicit `whole` scope, verify size and SHA-256 of every declared whole object at its
   supplied path, or the default sibling `images/` path, without joining or copying it.
   Authenticate all three child signatures before reporting success.

| Exit | Meaning |
|---|---|
| 0 | The requested scope passed |
| 1 | Refusal, input/I/O or helper failure; the PowerShell wrapper maps helper failures here |
| 2 | Python helper argument-parser usage error |
| 10 | The direct helper needs a working GnuPG installation; the PowerShell wrapper maps this failure to 1 |

## 6. For INSTALL.md: verify it yourself

Download the signed small release assets, take the public key from the repository, and compare
its primary fingerprint with the independently expected fingerprint.
Run `src/release-tools/verify-release.sh <metadata-folder> <fingerprint> --scope metadata`,
or `src/release-tools/verify-release.ps1 -ReleaseDirectory <metadata-folder> -Fingerprint <fingerprint> -Scope metadata`.
To verify image bytes too, use `whole` scope with every declared object, supplying
`--image <disc-role-or-bundle-id>=<path>` or `-ImagePath <disc-role-or-bundle-id>=<path>` where needed.
The helpers require Python 3.10 or later, their bundled helper/schema files and working native
GnuPG; `gpgv` alone does not implement this outer check.
These verifiers use the same authenticated root, descriptor and child checks described above.

## 7. Example (abridged)

These checksum names show the four detached signatures; MIRRORS version 2 supplies the
complete image and bundle descriptors, and ASSET-MAP supplies each sidecar's association.

```text
SHA256SUMS
SHA256SUMS.sig
MIRRORS.json
ASSET-MAP.json
ASSETS.txt
deep32-0.1.0-alpha.2-disc1-workstation-amd64-SHA256SUMS
deep32-0.1.0-alpha.2-disc1-workstation-amd64-SHA256SUMS.sig
deep32-0.1.0-alpha.2-disc2-content-SHA256SUMS
deep32-0.1.0-alpha.2-disc2-content-SHA256SUMS.sig
deep32-0.1.0-alpha.2-disc3-gpu-pack-SHA256SUMS
deep32-0.1.0-alpha.2-disc3-gpu-pack-SHA256SUMS.sig
```

Sources: `src/release-tools/release_assemble.py`, `src/release-tools/deep32_get_manifest.py`,
`src/release-tools/contract_validate.py`, `src/release-tools/contracts/schemas/mirrors.schema.json`,
`src/release-tools/sign-release.sh`, `src/release-tools/verify-release.ps1`,
`src/release-tools/verify-release.sh`, `install/verify-release.ps1`.
