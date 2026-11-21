# Pairing source checks

The `reference/` directory is the accepted pairing package imported unchanged. The first import commit preserves its exact bytes. Runtime copies live in the Workstation site tree and the GPU kit; these checks run those copies.

Use Python 3.12 or later, standard library only, with OpenSSL available. Supply a scratch directory outside the repository. All generated identities, credentials and fixtures are synthetic. The reference and cross-node suites refuse non-loopback sockets and privileged/network helper processes. HTTP model requests use an in-process connection stand-in; service/caller tests use local fakes.

```text
python -I -B tests/pairing/run_nodes.py --tmp /tmp/synthetic-pairing
python -I -B tests/pairing/test_forwarder.py --scratch /tmp/synthetic-forwarder -v
python -I -B tests/pairing/test_enrol.py --scratch /tmp/synthetic-enrol -v
python -I -B tests/pairing/test_forge.py --scratch /tmp/synthetic-forge -v
python -I -B tests/pairing/test_callers.py --tmp /tmp/synthetic-callers --report /tmp/synthetic-callers/report.json
```

`run_nodes.py` stages the unchanged reference tests beside the actual runtime package/launchers for each node and records source hashes. `--only reference|integration` and `--node workstation|forge` select a subset. Cross-node integration checks the actual enrolled settings, bearer forwarding, wrong codes, durable lockout, token rotation and re-pairing. Caller checks need an explicitly selected local Bash; on Windows they use Git for Windows Bash, never WSL.

Windows runs skip two reference POSIX ownership tests per node and one real symlink-creation test when the account lacks that privilege. Metadata/descriptor permission-refusal tests still run. A Windows source test is not OpenBSD permission or securelevel acceptance. Publication, firewall destinations, the Windows host bridge, actual model attestation, image/disc rebuilds and real-host tests remain release gates.
