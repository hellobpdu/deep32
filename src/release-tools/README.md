# Release tools

These tools assemble, fetch, sign and verify Deep32 release files. The release operator supplies the image inputs and performs signing.

Run Python tools with `python3 -I -B`. `make-manifest.py` and `export-release-key.py` use the public attribution in `release-authorship.json` and the repository `install/` key files. Override their documented input options for a different release identity. No private key is included here.

`release.ps1` keeps its complete signature, manifest, report, approval and upload checks. It also requires the separately maintained release-check tooling at `src/release-checks/run_gates.py`; the source staging tree does not contain an approval record. Signing and publishing belong to the release operator.
