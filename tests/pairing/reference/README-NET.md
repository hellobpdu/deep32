# Deep32 network reference

`deep32net` provides discovery, code pairing, local certificate identities,
credential handover and durable peer records. It uses Python 3.12 or later,
the standard library and an openssl command for identity creation. It provides
no publishing or sign-in service. The commands take machine requests on stdin.

## Pairings

| Kind | Joined side, TLS server and A | Joiner, TLS client and B | Port | Handover |
|---|---|---|---:|---|
| relay | relay | Workstation | 7452 | pins only |
| forge | Forge | Workstation | 7453 | Forge token and knowledge-base public key |
| gateway | Workstation gateway | headend | 7454 | a fresh MCP token for this headend |

The joined side makes the code. Each side opens one five-minute window, with
one claim. Each claims only after receiving the other's HELLO_ACK. Probes and
refusals before that point consume no attempt. Three failed claimed windows
within 1800 seconds lock the local link for 900 seconds. A backwards wall clock
is clamped when recording failures. Opening another window cannot cancel a lock.
An interrupted durable claim becomes one failure on the next open. Success keeps
the failure history. An unused expiry adds no failure.

The exchange uses TLS 1.3, SPAKE2 over P-256, the pairing v2 transcript and full
HMAC-SHA256 confirmations. The joiner checks A's confirmation before sending B's.
After confirmation A sends the handover and COMMIT. B persists received files,
then its peer record, then sends COMMIT. A persists after receiving B's COMMIT.
If B's final send raises, B removes what it just wrote and counts a connection
failure. A process interruption during commit can leave one side enrolled;
local unpairing and a new window recover that case. Pairing listeners enrol only.

## Code

The alphabet is `ABCDEFGHIJKLMNOPQRSTUVWXYZ234567`, length 10. Generation
takes the first 10 base32 symbols of seven random bytes. Input is limited to
64 characters. Refuse non-ASCII input first; remove spaces, tabs and hyphens;
refuse empty input; uppercase; map `0` to `O`, `1` to `I`, `8` to `B`; refuse
any remaining symbol outside the alphabet; require exactly 10 symbols.
There is no Unicode normalisation for codes. The first failing check supplies
`code_reason`: `bad-request`, `too-long`, `non-ascii`, `empty`, `bad-symbol`,
or `length`. A joined-side request requires an already canonical code and uses
`not-canonical` on refusal. Codes go only in stdin JSON or library arguments.
`new-code` is the sole command that prints a code.

The canonical ASCII bytes enter PBKDF2-HMAC-SHA256 with 600000 iterations,
32 output bytes, salt `D32U/1 pairing v2` followed by A's pin, B's pin, A's
nonce and B's nonce. The result is reduced modulo the P-256 group order.
The 12-scalar-or-four-word password rule does not apply to codes.

## Commands and requests

Run `python3 -I -B bin/deep32-pair VERB ...` from this folder, or install the
bin files beside the package directory. The openssl command is the request's
`openssl` field (or `--openssl`) when given, else `DEEP32_OPENSSL`, else openssl
on PATH.
Keys and codes never go in command arguments or environment variables.

Every request on stdin is one ASCII JSON object of at most 65536 bytes.
Unknown, missing and wrongly typed fields are refused. Integers must be JSON
integers; booleans and decimal numbers do not stand in for integers. Lists below
give all permitted fields. Required fields have no brackets.

| Verb and arguments | Stdin request fields | Success fields |
|---|---|---|
| `new-code` | none | code followed by LF, without JSON |
| `identity --state-dir DIR --node forge\|workstation\|headend [--openssl PATH]` | none | `id`, `pin`, `created` |
| `serve` | `kind`, `code`, `expires_seconds`, `attempts`, `transport`, `publish`, `state_dir`; forge also `token_path`, `kb_public_path`; optional `port`, `ready_file`, `openssl` | `kind`, `peer_id`, `peer_pin`, `port` |
| `join` | `kind`, `code`, `transport`, `state_dir`; optional `address`, `port`, `expect_pin`, `peer_host_name`, `peer_dns_name`, `hosts_file`, `openssl` | `kind`, `peer_id`, `peer_pin`, `received` |
| `discover --want forge\|gateway\|relay --transport tailnet\|loopback [--status-json FILE] [--no-probe] [--port N]` | none | `transport`, `want`, `backend_state`, `self`, `candidates` |
| `peers --state-dir DIR` | none | `peers`, a list of peer records |
| `unpair --state-dir DIR --kind K --side joined\|joiner --peer-id ID [--token-path P] [--edge-env FILE] [--hosts-file FILE]` | none | `removed`, `rotated`, `restart_required`, `must_repair` |
| `make-token --path P [--rotate]` | none | `created`, `rotated` |
| `write-edge-tokens --state-dir DIR --env-file P` | none | `count` |
| `hosts --state-dir DIR --hosts-file P` | none | `entries` |
| `--help` | none | bin header text |
| `--version` | none | `deep32-pair 0.1.0` |

Serve accepts kind forge or gateway. `expires_seconds` must be 300 and
`attempts` must be 1. `publish` is `serve-https` or `tailnet-plain`. A serve
port may be zero to allocate a free local port; other ports are 1 to 65535.
`ready_file` receives just `port` and `pin` once the listener is bound. Serve
accepts no edge environment path. After gateway pairing the caller invokes
`write-edge-tokens` and restarts the service that consumes that file.

Join accepts relay, forge or gateway. Tailnet mode requires `address`; loopback
defaults it to `127.0.0.1` and refuses any other value. `expect_pin`, when given,
is 64 lowercase hex characters and must equal the certificate TLS presented.
`peer_host_name` and `peer_dns_name` are optional nonempty DNS-style names.
`hosts_file` is updated after a tailnet join only. `received` lists file names,
never their contents. Missing request values are never obtained interactively.

`python3 -I -B bin/pairing-helper pair-forge` takes exactly these required fields:
`code`, `expires_seconds`, `attempts`, `form`, `transport`, `publish`, `token_path`,
`kb_public_path`, `state_dir`. Optional fields are `port`, `ready_file`, `openssl`.
Form is `iso`, `wsl` or `docker`; expiry and attempts have the same fixed values
as serve. It runs forge serve. It never publishes or writes console status.
`pairing-helper host-signin` returns exit 64, reason `not-provided`.
The helper's version text is `pairing-helper 0.1.0`.

## Output and exits

Every ordinary result is exactly one compact JSON object with `schema` equal to
`deep32.net.v1`, `tool` equal to `0.1.0`, `verb`, `ok`, `exit`, `reason` and the
verb fields above. `reason` is null on success. Errors may add `code_reason`,
`locked_seconds` or `backend_state`. Stderr has at most one line:
`<program>: <reason>`. Peer error text is never displayed.

| Exit | Reasons |
|---:|---|
| 0 | success, `reason:null` |
| 1 | `internal` |
| 2 | `bad-request` |
| 3 | `locked` with `locked_seconds`, `peer-locked` |
| 4 | `expired`, `unreachable`, `tls-failed`, `pin-mismatch`, `same-identity`, `peer-refused`, `incompatible-peer`, or `protocol`, `timeout`, `connection` before claim |
| 5 | `password-or-identity`, `confirmation`, `protocol`, `timeout`, `connection`, `storage` after claim |
| 6 | `storage`, `busy`, `openssl-missing`, `identity-kind-mismatch`, `bind-failed` |
| 7 | `already-paired`, `exists`, `not-paired` |
| 8 | `bad-code`, with `code_reason` |
| 9 | `not-signed-in`, `status-unreadable` |
| 64 | `not-provided` |

## Local state

The state's parent must exist. A missing state directory is created mode 0700.
Directories are 0700 and files 0600 on POSIX. Existing symlinks and junctions
in a state path are refused. Windows filesystem access controls remain the
installer's responsibility. Writes use exclusive random temporary names,
file fsync, atomic replacement and directory fsync on POSIX.

```text
identity/cert.pem
identity/key.pem
identity/id
pairing/<kind>-<side>.lock
pairing/<kind>-<side>.pairlock
peers/<kind>-<side>/<peer_id>.json
private/received/<kind>/<peer_id>/forge-token
private/received/<kind>/<peer_id>/kb-public.pem
private/received/<kind>/<peer_id>/mcp-token
private/mcp-tokens/<peer_id>
```

Identity IDs are `forge-`, `workstation-` or `headend-` followed by 12 random hex
characters. Pins are SHA-256 of the complete certificate DER. Existing identities
are reused; incomplete identities are refused. The lock file is held exclusively
for a run. Pairlock text is `D32PAIR2 f0 f1 f2 until in_progress` plus LF, with
ordered failure timestamps. Corrupt state is refused rather than reset.

Peer records have keys in this order: `format` (`deep32-peer`), `format_version`
(1), `kind`, `side`, `peer_id`, `peer_role`, `peer_pin`, `own_pin`, `transport`,
`peer_address`, `peer_port`, `peer_host_name`, `peer_dns_name`, `publish`,
`paired_at` (integer Unix seconds), `received`, `given`. The record is the last
file written and is the commit marker. Joined-side address and port are null.
Absent host names and DNS names are null. Relay publish is null. Received and
given contain sorted file names; a record holds no code, token or private key.

## Tokens and revocation

| Item | Creation and handover | Revocation |
|---|---|---|
| engine token | `make-token`, local to Workstation | rotate locally; restart its consumers |
| MCP token per headend | gateway creates it at claim, persists after COMMIT, gives it to headend | joined gateway unpair deletes that token |
| Forge token | created by first boot; forge pairing reads it and gives it to Workstation | joined forge unpair rotates the shared token; restart door; other peers must pair again |
| knowledge-base signing key | first boot creates it; pairing transfers only its Ed25519 public half | external signing-key replacement and renewed enrolment |
| link keys and pins | local certificate creation and pairing enrolment | remove local peer record; relay runtime revocation is external |
| Ask password | relay's separate second factor | relay manages its verifier; this package does not handle it |

Tokens are 32 random bytes as 64 lowercase hex characters. `make-token` refuses
an existing file unless `--rotate` is supplied. Gateway token files are sorted
by peer ID when `write-edge-tokens` writes `RAG_MCP_TOKENS=` plus comma-separated
values. With no tokens the line is still written with an empty value. Other
lines are preserved byte for byte. The output file is 0600 on POSIX.

Joined forge unpair requires `--token-path`, returns `restart_required:["door"]`
and lists other forge-joined peer IDs in `must_repair`. Joiner unpair removes
received files and can update hosts. Removing an absent peer changes nothing
and returns `not-paired`. No token appears in command output.

## Transport, discovery and hosts

`loopback` reaches only `127.0.0.1`, runs no status command and returns one
loopback candidate. `tailnet` reaches a peer's IPv4 inside the shared address
range used by Tailscale. Listeners bind `127.0.0.1` in both modes. A separate
publisher supplies the forwarder. Tokens, codes and lockout apply in both modes.

Tailnet discovery reads `tailscale status --json` with a 15-second command
timeout, or the supplied status file. BackendState must be Running. It includes
peers whose host name or DNS first label starts `deep32-`, ignoring ASCII case,
and excludes shared-in and expired peers. Online is true only for JSON true.
The first suitable IPv4 is retained. Path is direct for an active peer with a
nonempty CurAddr, relayed for an active peer with a nonempty Relay, otherwise
unknown. Sort order is online first, lowercase host name, peer ID. The caller
chooses among candidates. A login address is never included in output.

`self` has `host_name`, `dns_name`, `ipv4`. A candidate has `peer_id`,
`host_name`, `dns_name`, `os`, `ipv4`, `port`, `online`, `path`, `probe`, in that
order. DNS names lose their final dot. In loopback both backend and self are
null, peer names and IDs are null, online is true and path is loopback.

A probe connects within 3 seconds, uses TLS 1.3, reads one HELLO within 10
seconds and sends no frame. Relay candidates are never probed. Offline or
IPv4-less peers also cause no connection. Probe output is either
`{done:true,deep32:true,role,id,pin,features}`, `{done:true,deep32:false,reason}`
with `not-deep32` or `wrong-role`, or `{done:false,reason}` with `offline`,
`no-ipv4`, `relay-not-probed`, `connect-refused`, `timeout` or `tls-failed`.
With probing off the field is null.

The hosts command manages only the block between `# deep32 peers begin` and
`# deep32 peers end`. Each tailnet joiner record with an address and DNS name
contributes `<address> <dns_name> <first-label>`, sorted by DNS name. Outside
bytes and file mode are preserved. An empty block is removed. Duplicate or
incomplete markers are refused without changing the file.

## Deviations from pairing v2

- D1: a Python joined side does not request a TLS client certificate. B sends
  its public DER certificate in HELLO. Its hash is bound by the exchange but
  possession of that key is not proven by TLS. A never uses that pin for runtime
  authentication; runtime access uses the handed-over token. Relay pairing
  keeps the existing relay and link client certificate behaviour.
- D2: Python arithmetic is variable-time. A fixed 256-round ladder avoids
  scalar-bit branches; it does not make Python integer operations constant-time.
- D3: owned mutable code, scalar, point, transcript and key buffers are zeroed
  after use. Python strings, integers and temporary immutable copies cannot be
  reliably erased. This reference cannot promise complete secret erasure.
- D4: frame 15 PAIR_HANDOVER, feature handover-v1, HELLO B cert and the forge,
  gateway, workstation and headend roles exist only on forge and gateway pairing
  connections. The relay link retains its existing 14 frame types.
- D5: codes bypass the typed password's 12-character-or-four-word rule; the
  scalar derivation and transcript stay unchanged.
- D6: a pairing listener always answers paired false and never resumes a link.

## Verification

From this folder run `python3 -I -B tests/run_all.py --tmp SCRATCH`. The scratch
directory is required. openssl must be on PATH or set through DEEP32_OPENSSL.
The suite guards all sockets and child processes, uses only loopback TLS and
removes generated identities and tokens. POSIX permission tests skip on other
platforms. `python3 -I -B tools/make_vectors.py --check` recomputes the public
fixtures and checks every byte. Its optional `--out` must stay under vectors/.
The two base fixture files are preserved byte for byte. Public frozen test
certificates have no accompanying private keys.
