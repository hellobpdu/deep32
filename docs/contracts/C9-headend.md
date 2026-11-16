# C9. Headend

Contract C9, version 1, draft of 2026-11-14. Not frozen. How an agent such as Claude Code or Codex reaches Deep32:
the link protocol between the Workstation and the relay as it exists, the engine adapter interface, what the relay
reports, the MCP entry, and the box an Ask session runs in. Written by the headend stage (stage 8). Read by the
relay, the Workstation's Ask window and link client, `deep32-connect`, and gate G11.

Two hook-ins. **MCP:** the agent calls the gateway's eleven tools itself. **Relay:** the Ask window
sends a question over the link; the relay starts the agent with its own packaged research skill and streams the
answer back. The agent's tool calls go back over the link to the Workstation's gateway, so an Ask session needs no
MCP entry on the headend. In v1.0 the relay ships for Windows; a portable relay is assessed in stage 8 and built
only if it fits and the maintainer says go. Without a relay, the MCP hook-in still works on every
platform.

## The link: D32U/1, as shipped

| Item | Rule |
|---|---|
| Name | `proto="D32U/1"`, version 1, features `research-v1` and `pairing-v2`, role `relay` |
| Transport | the Workstation dials TCP. The relay binds 127.0.0.1 by default, or an RFC 1918 address by local setting; wildcard, public, IPv6 and Tailscale binds are refused. The distribution keeps loopback only; tailnet reach is a forwarder to it (C8) |
| Security | mutual TLS 1.3 (1.2 only by local opt-in), self-signed ECDSA P-256 certificates, trust = SHA-256 pin of the peer's DER certificate; no plaintext form |
| Framing | 12-byte big-endian header: u32 payload length, u32 stream, u8 type, u8 channel, u16 flags. Payload at most 1 MiB; 14 frame types (1 HELLO to 14 PAIR_SHARE); the only flag is END_MESSAGE on DATA; at most 256 frames a second |
| Channels | 1 research sessions (the Workstation opens), 2 reverse MCP (the relay opens), 4 status, 7 Ask window control; 3, 5 and 6 are refused |
| Flow | 256 KiB credit per stream with WINDOW_UPDATE; ping every 15 s; a 45 s silence limit; a 180 s credit wait |
| Pairing | pairing v2 (C8): SPAKE2 over P-256 with PAIR_SHARE (65 bytes), PAIR_CONFIRM (32-byte MAC), PAIR_COMMIT |
| Ask password | a second factor the person sets at first use; PBKDF2-SHA256, 600,000 iterations; 5 failures per 5 minutes; a grant lives 12 hours; at most 16 windows; Ask messages at most 16 KiB, wiped after use |
| Limits | 30 research turns an hour; a turn at most 1 hour; 8 links; 16 sessions and 64 streams per link; 4 reverse MCP calls per session; an answer at most 1 MiB |

The normative text is the D32U/1 specification, version 1.2, with its pairing-v2 document, schemas and test vectors.

## Engine adapters

An engine is one adapter behind a fixed interface: `detect` (present, version, signed in; reads no credential),
`offers` (the models and strengths it can run, probed from the installed tool), `open` (a boxed session with the
research skill), `turn`, `events` (streamed text, tool calls, the final answer, errors), `interrupt`, `close`.

- Shipped in v1.0: `claude-code` (wire engine name `claude`) and `codex` (wire name `codex`).
- `openai-compatible`: a configuration slot only (address, model name, tool-calling support) with no adapter
  behind it in v1.0. When it comes, the relay runs the agent loop itself.
- No fixed model list. Each adapter uses the agent installed on the PC; the relay downloads no agent
  build. Versions come from the probe: the HELLO frame's `codex_version`, hard-coded today, is filled from it.
  Options that differ between agent versions are probed; an adapter that cannot apply every option of the box
  refuses to start.

## What the relay reports (channel 7)

`ask.state` carries `lock` and, per engine, `ready`, `needs_login` or `unavailable`. `ask.capabilities` returns
tuples `{engine, model, effort, billing, preferred}`, refreshed at most every 30 seconds; the Ask window fills its
Engine, Model and Strength menus from them. `ask.codex.login.begin` starts Codex's device sign-in. `session.open`
must name profile `research`, skill `deep32`, caller class `research` or `research-relay`, an engine, and a model
and effort among the reported tuples. No other profile exists.

## The MCP entry

`deep32-connect` writes one entry per agent it finds, pointing at the Workstation's published gateway
(`https://<node>.<tailnet>.ts.net/mcp`) with that headend's MCP token from pairing (C8). Claude Code gets an `http`
server in the user's configuration; Codex gets `[mcp_servers.<name>]` with `url` and `bearer_token_env_var`. The
entry name and the token variable must not collide with an existing install (gate G13); they are not
named yet, so stage 8 settles them (C8). No token is written to a project folder or a tracked file.

## The box (gate G11)

An Ask session's agent may call the gateway's eleven search tools and nothing else: `rag_status`, `search_rag`,
`search_many`, `get_section`, `get_outline`, `lookup_id`, `lookup_rfc`, `find_command`, `grep_corpus`,
`list_documents`, `verify_quote`. No file reads, no commands, no web, no other MCP server. As shipped:

- Claude Code: built-in tools off (`--tools ""`, the built-ins disallowed), only the eleven allowed, one MCP server
  under `--strict-mcp-config`, no settings layers, its own configuration folder, a cleared environment, a fresh
  empty working folder, no session persistence. Every `system/init` event must name the model, the folder,
  exactly the eleven tools and the one server, or the session ends with an error.
- Codex: read-only sandbox, approval never, web search off, history off, eleven features off (shell, patching,
  plugins, hooks, apps, multi-agent, memories, image viewing and others), strict configuration. Before the first
  turn it checks the layered configuration, exactly one enabled MCP server and the features list.
- Tool calls go to a per-session loopback proxy with a random bearer, which forwards them only while a verified
  turn runs (deadline 170 s). The user's own skills and MCP entries are never used by an Ask session; the relay's
  packaged skill is a cleaned copy of the research skill.

## Open points

- OPEN: the specification and its vectors live outside this repository today; they are exported in stage 8.
  Decides: the builder.
- OPEN: how Claude Code receives the MCP bearer without a token in its configuration file. Decides: the builder in
  stage 8.
- OPEN: the session's internal MCP server name, today the original gateway's name, renamed with the install identity (C8) or kept.
  Decides: the builder in stage 8.
