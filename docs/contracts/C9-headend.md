# C9. Headend

Contract C9, version 2, draft of 2026-11-17. Not frozen. How an agent such as Claude Code or Codex reaches Deep32:
the link protocol between the Workstation and the relay as it exists, the engine adapter interface, what the relay
reports, the MCP entry, and the box an Ask session runs in. Written by the headend stage (stage 8). Read by the
relay, the Workstation's Ask window and link client, the source connector in `adapters/`, and gate G11.

Changed: the Workstation link dialect, SAS pairing, status and session fields, connector name, and pending relay qualification.

Two hook-ins. **MCP:** the agent calls the gateway's eleven tools itself. **Relay:** the Ask window
sends a question over the link; the relay starts the agent with its own packaged research skill and streams the
answer back. The agent's tool calls go back over the link to the Workstation's gateway, so an Ask session needs no
MCP entry on the headend. The relay implementation and its native-agent qualification are pending T-0226.
The direct MCP hook-in uses the source connector below; rebuilt-media and real-client acceptance remain open.

## The link: D32U/1, as shipped

The wire rows describe the shipped Workstation client. The relay-only transport allowance and turn/link limits below remain requirements pending T-0226; they are not claims that the partial relay implements them. The Ask password is planned for beta and is absent from alpha.2.

| Item | Rule |
|---|---|
| Name | `proto="D32U/1"`, version 1, exactly `features:["research-v1"]`; the Workstation sends role `node` and accepts `relay` or `hub` |
| Transport | the Workstation dials TCP. The relay binds 127.0.0.1 by default, or an RFC 1918 address by local setting; wildcard, public, IPv6 and Tailscale binds are refused. The distribution keeps loopback only; tailnet reach is a forwarder to it (C8) |
| Security | mutual TLS 1.3 (1.2 only by local opt-in), self-signed ECDSA P-256 certificates, trust = SHA-256 pin of the peer's DER certificate; no plaintext form |
| Framing | 12-byte big-endian header: u32 payload length, u32 stream, u8 type, u8 channel, u16 flags. Payload at most 1 MiB; 13 frame types (1 HELLO to 13 PAIR_COMMIT); the only flag is END_MESSAGE on DATA; at most 256 received frames a second |
| Channels | 1 research sessions (the Workstation opens), 2 reverse MCP (the relay opens), 4 status; 3, 5, 6 and unknown channels, including 7, are refused |
| Flow | 256 KiB credit per stream with WINDOW_UPDATE; ping every 15 s; a 45 s silence limit; a 180 s credit wait |
| Pairing | Ten-word SAS from the certificate pins and ordered HELLO nonces; both protected consoles confirm the words; PAIR_CONFIRM and PAIR_COMMIT have empty payloads; persist the peer pin and ID before local COMMIT, then require both commits; separate from C8 gateway enrollment |
| Ask password | planned for beta; absent from alpha.2. Planned policy: a second factor set at first use; PBKDF2-SHA256, 600,000 iterations; 5 failures per 5 minutes; a grant lives 12 hours; at most 16 windows; Ask messages at most 16 KiB, wiped after use |
| Limits | 30 research turns an hour; a turn at most 1 hour; 8 links; 16 sessions and 64 streams per link; 4 reverse MCP calls per session; an answer at most 1 MiB |

The in-tree Workstation parser, frame codec and state machine define the current interoperable dialect; the external specification and vectors remain an open input.
The Workstation parser permits at most two configured links.
Sources: `src/workstation/node/asklink/src/{d32u.h,frame.c,msg.c,link.c,sas.c,chatd.h,chatd.c,ident.c}`.

## Engine adapters

The adapter interface and shipping requirements in this section remain pending T-0226; the merged native launchers refuse with `native-contract-unqualified`.
Sources: `adapters/relay/deep32_relay/{engine.py,native.py}`.

An engine is one adapter behind a fixed interface: `detect` (present, version, signed in; reads no credential),
`offers` (the models and strengths it can run, probed from the installed tool), `open` (a boxed session with the
research skill), `turn`, `events` (streamed text, tool calls, the final answer, errors), `interrupt`, `close`.

- Shipped in v1.0: `claude-code` (wire engine name `claude`) and `codex` (wire name `codex`).
- `openai-compatible`: a configuration slot only (address, model name, tool-calling support) with no adapter
  behind it in v1.0. When it comes, the relay runs the agent loop itself.
- No fixed model list. Each adapter uses the agent installed on the PC; the relay downloads no agent
  build. The Workstation accepts optional HELLO `codex_version` only as the literal `0.160.0`; omit the field when that is not the actual version.
  Options that differ between agent versions are probed; an adapter that cannot apply every option of the box
  refuses to start.

## What the relay reports

HELLO may carry a Boolean `signed_in` and a `profiles` array containing at most one object named `research`, with separate `models` and `efforts` lists.
The Workstation keeps the first 16 entries of each list; every entry must be a nonempty string of at most 64 UTF-8 bytes without NUL.
Channel 4 status carries exactly `{"code":value}`, where value is `OFF`, `LISTENING`, `PAIRING`, `CONNECTED`, `DISCONNECTED`, `CODEX_READY`, `CODEX_SIGNED_OUT`, `BUSY`, `IDLE` or `ERROR`.
There is no channel 7, `ask.state`, `ask.capabilities` or `ask.codex.login.begin` in this dialect.
`session.open` names profile `research`, kind `research`, skill `deep32`, caller class `research` or `research-relay`, and a model and effort from the independent advertised lists; it has no engine field.
The relay engine is selected at its endpoint, not by a new wire selector. No other profile exists.
Sources: `src/workstation/node/asklink/src/msg.c`, `msg.h`, `link.c` and `chatd.c`.

## The MCP entry

The source connector writes one entry per selected agent, pointing at the Workstation's published gateway
(`https://<node>.<tailnet>.ts.net/mcp`) with that headend's MCP token from pairing (C8). Claude Code gets an `http`
server in the user's configuration; Codex gets `[mcp_servers.<name>]` with `url` and `bearer_token_env_var`. The
entry name and token variable are scoped to a stable logical headend identity: `deep32_alpha_<id8>` and
`DEEP32_ALPHA_<ID8>_MCP_TOKEN`. Each selected agent receives its own C8 enrolment, credential and revocation
identity; selecting both opens two sequential pairing windows. No token is written to a project folder or a
tracked file.
The source entry points are the wrappers below, not an installed `deep32-connect` executable.

The next-release source command is `bash adapters/connect.sh --agent codex|claude|both --workstation URL`, or
`powershell -NoProfile -ExecutionPolicy Bypass -File adapters/connect.ps1 -Agent codex|claude|both -Workstation URL`.
`--check` or `-Check` is read-only and starts no pairing. The normal action detects the agent and sign-in, checks
the HTTPS gateway, obtains the code through a hidden local prompt, and uses the existing C8 Joiner. Configuration
holds an environment reference. A private launcher loads the enrolled credential into the selected child only;
Windows stores it with CurrentUser DPAPI and user-only directory permissions, Linux with mode 0600 under a
mode 0700 directory. It is never an argument, settings value or output field.

JSON and TOML are parsed before merging; duplicate or ambiguous entries are refused. Existing changed files
require a concrete backup/merge choice. Unrelated entries and instruction text survive, and skills use scoped
names. MCP authentication and eleven tools are checked internally; actual client restart and revocation remain
release acceptance checks. Claude's HTTP environment substitution must pass a bounded, private `claude mcp list`
connectivity probe; failure reports `agent-credential-mode-unsupported` without writing a literal bearer fallback.

Needs rebuilt disc 1 and real-agent Windows/Linux acceptance before the connector is supported end to end.

## The box (gate G11)

These are relay requirements pending T-0226; the merged fail-closed native launchers do not establish a qualified agent session.

An Ask session's agent may call the gateway's eleven search tools and nothing else: `rag_status`, `search_rag`,
`search_many`, `get_section`, `get_outline`, `lookup_id`, `lookup_rfc`, `find_command`, `grep_corpus`,
`list_documents`, `verify_quote`. No file reads, no commands, no web, no other MCP server. Required controls:

- Claude Code: built-in tools off (`--tools ""`, the built-ins disallowed), only the eleven allowed, one MCP server
  under `--strict-mcp-config`, no settings layers, its own configuration folder, a cleared environment, a fresh
  empty working folder, no session persistence. Every `system/init` event must name the model, the folder,
  exactly the eleven tools and the one server, or the session ends with an error.
- Codex: read-only sandbox, approval never, web search off, history off, eleven features off (shell, patching,
  plugins, hooks, apps, multi-agent, memories, image viewing and others), strict configuration. Before the first
  turn it checks the layered configuration, exactly one enabled MCP server and the features list.
- Tool calls go to a per-session loopback proxy with a random bearer, which forwards them only while a verified
  turn runs (deadline 3600 s, the session's lifetime). The user's own skills and MCP entries are never used by an Ask session; the relay's
  packaged skill is a cleaned copy of the research skill.

Sources: `adapters/relay/deep32_relay/{native.py,engine.py,proxy.py,sessions.py}` and `adapters/relay/RESEARCH.md`.

## Open points

- OPEN: the specification and its vectors live outside this repository today; they are exported in stage 8.
  Decides: the builder.
- Claude Code source uses a literal environment header reference and a private child-process launcher; its actual
  HTTP substitution, restart and revocation behaviour is UNVERIFIED until the real-client acceptance run.
- OPEN: the session's internal MCP server name, today the original gateway's name, renamed with the install identity (C8) or kept.
  Decides: the builder in stage 8; pending T-0226.

Sources: `src/workstation/node/asklink/src/{d32u.h,frame.c,msg.c,msg.h,link.c,sas.c,chatd.h,chatd.c,ident.c}`, `adapters/connect.py` and `adapters/relay/deep32_relay/{engine.py,native.py,proxy.py,sessions.py,transport.py}`.
