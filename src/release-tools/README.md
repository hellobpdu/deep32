# Release tools

These tools assemble, fetch, sign and verify Deep32 release files. The release operator supplies the image inputs and performs signing.

Run Python tools with `python3 -I -B`. `make-manifest.py` and `export-release-key.py` use the public attribution in `release-authorship.json` and the repository `install/` key files. Override their documented input options for a different release identity. No private key is included here.

`release.ps1` keeps its complete signature, manifest, report, approval and upload checks. It also requires the separately maintained release-check tooling at `src/release-checks/run_gates.py`; the source staging tree does not contain an approval record. Signing and publishing belong to the release operator.

Run `deep32-get` from the complete clone: it uses the sibling manifest/settings helpers, the shared C7 renderer and the install key/schema. A loose script copied from a release asset is not the clone-run installation kit. `--key` accepts an armoured public `.asc` or binary public keyring, `--gpg` selects an executable file and `--fingerprint` overrides the repository's expected primary identity only after independent confirmation. Windows discovers native Gpg4win before PATH and never selects Git/MSYS GnuPG automatically. An explicit `--gpg` choice receives native paths without MSYS translation. If GnuPG is missing, run `winget install --exact --id GnuPG.Gpg4win` and reopen PowerShell. Python 3.10+ is required on both platforms; `DEEP32_PYTHON` selects its Windows executable.

`deep32-get disc1 --prepare-vm --hypervisor hyper-v|kvm` writes a verified installer copy, recipe and command file. It creates or starts no VM. Read `commands.txt` and complete the host boot qualification before treating its recipe as supported. Cache and output filesystems must support no-overwrite hard-link publication; that capability is checked before payload transfer.

`deep32-get disc3 --install` defaults to the Forge role on Linux Docker and Windows Docker Desktop. J1, J2 and J4 pair that GPU host with a Workstation, which keeps content, the live index, engine and MCP gateway. Forge starts embeddings, reranker and their authenticated door; conversion and index-builder jobs use separate Compose profiles. J3 requires explicit `--docker-role full`, C7 `profile=headless` and `--content` for the matching signed content media on this host. J5 has no GPU-pack install. Both roles use the same Docker form, a dedicated private `--data-root` and protected settings staging. Keep the recorded read-only GPU media available for restart/resume. Local health does not complete the separate host publication, pairing and real-host acceptance gates. See [PACK.md](../gpu-pack/PACK.md).

`verify-release.sh DIRECTORY EXPECTED_PRIMARY_FINGERPRINT --disc disc3 --key PUBLIC_KEY` and the PowerShell `-Disc disc3 -Key PUBLIC_KEY` variant verify a selected disc from authenticated ASSEMBLY metadata. Other signed filenames may remain in a shared cache; unknown entries, unsafe paths, missing selected parts and a corrupt selected joined image are refused. This mode imports the public key into a fresh temporary home and does not use the person's keyring.

## Complete-release verification

Complete mode keeps its existing strict contract: DIRECTORY contains every signed asset. Use a fresh isolated keyring and the independently confirmed primary fingerprint. In Bash, from the repository root:

```sh
verification_dir=$(mktemp -d)
gpg --no-options --homedir "$verification_dir" --batch --import install/release-key.asc
GNUPGHOME="$verification_dir" bash src/release-tools/verify-release.sh ./complete-release B205B406548627AABF5250D8A078C5F25D025DD6
```

In Windows PowerShell, with GnuPG on PATH:

```powershell
$verificationDir = Join-Path ([IO.Path]::GetTempPath()) ('deep32-public-' + [Guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory($verificationDir)
$previousVerificationHome = $env:GNUPGHOME
try {
    gpg --no-options --homedir $verificationDir --batch --import install\release-key.asc
    if ($LASTEXITCODE -ne 0) { throw 'Public key import failed.' }
    $env:GNUPGHOME = $verificationDir
    powershell -NoProfile -ExecutionPolicy Bypass -File src\release-tools\verify-release.ps1 -ReleaseDirectory .\complete-release -Fingerprint B205B406548627AABF5250D8A078C5F25D025DD6
} finally { $env:GNUPGHOME = $previousVerificationHome }
```

The verifier checks each signature's primary fingerprint as well as every checksum. Keep the command's exit status; a copied file or successful key import is not verification. The temporary directory contains public verification material only and may be removed after the check. The legacy `install/verify-release` entrypoints target the earlier manifest format; use the source-release tools above for ASSEMBLY releases.
