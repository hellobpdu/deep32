<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/figures/banner-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="docs/figures/banner-light.svg">
  <img alt="Deep32" src="docs/figures/banner-light.svg" width="100%">
</picture>

**Deep32 answers network and security engineering questions from evidence, not from memory.**<br>
Every claim cites a passage. Every quotation is checked against the source text.<br>
When the standard and the vendor disagree, the answer says so.

<p>
  <a href="#roadmap"><img alt="Status: pre-release" src="https://img.shields.io/badge/status-pre--release-9a6700?style=flat-square"></a>
  <a href="#evidence-tools"><img alt="Interface: Model Context Protocol" src="https://img.shields.io/badge/interface-Model_Context_Protocol-0969da?style=flat-square"></a>
  <a href="#retrieval-pipeline"><img alt="Retrieval: BM25, dense, rerank" src="https://img.shields.io/badge/retrieval-BM25_%2B_dense_%2B_rerank-1a7f37?style=flat-square"></a>
  <a href="#verifying"><img alt="Signed with OpenPGP" src="https://img.shields.io/badge/signed-OpenPGP-8250df?style=flat-square"></a>
  <a href="https://orcid.org/0009-0003-8091-4637"><img alt="ORCID 0009-0003-8091-4637" src="https://img.shields.io/badge/ORCID-0009--0003--8091--4637-a6ce39?style=flat-square&logo=orcid&amp;logoColor=white"></a>
</p>

[Why](#why) · [Architecture](#architecture) · [Retrieval](#retrieval-pipeline) · [Tools](#evidence-tools) · [Protocol](#answer-protocol) · [Corpus](#corpus) · [Evaluation](#evaluation) · [Roadmap](#roadmap) · [Limits](#scope-and-limits)

</div>

> [!NOTE]
> **Pre-release.** Deep32 runs today as a private deployment. This repository will hold the design notes first, then the code and signed release images. Nothing here is installable yet. The numbers on this page were measured on that deployment.

## Why

A language model answers a protocol question fluently whether or not it knows the answer. In network and security engineering the wrong answers have recognizable shapes, and they end up in configurations.

| The failure | An example | The check |
|---|---|---|
| A vendor default recalled as the standard | "BGP's hold time is 180 seconds." That is Cisco's default. RFC 4271 suggests 90. | Standard and vendor text are retrieved and compared as separate sources |
| An obsolete document cited as current | RFC 1771 quoted for BGP-4, years after RFC 4271 replaced it | The standing of every cited RFC is looked up: status, obsoleted-by, errata |
| The right command on the wrong release | Syntax from one software train presented for another | Passages carry their platform and release, and a search can prefer or cap a release |
| A quotation nobody wrote | "The RFC states…" followed by text that is not in it | Every quotation is matched against the source section before it is used |

Deep32 is built so that each of these has a mechanical check.

1. **The model is a reader, not a source.** It answers from retrieved passages, or it says the corpus does not cover the question.
2. **Checks are mechanical.** A quotation is verified by matching it against the stored text, not by asking a model whether it looks right.
3. **Disagreement is a result.** Where an RFC and a vendor document differ, both are reported, each with its citation.
4. **Everything is versioned.** Documents carry hashes, passages carry content-derived ids, and an index is an immutable generation that can be compared with the one before it and rolled back.
5. **It runs on your own hardware.** The corpus, the indexes and the retrieval models are local. Only the language model is hosted.

## Architecture

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/figures/architecture-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="docs/figures/architecture-light.svg">
  <img alt="Deep32 architecture: an agent calls an MCP gateway, the gateway calls the search and evidence engine, the engine uses GPU services for embeddings and reranking; a build pipeline produces the immutable index generation the engine serves." src="docs/figures/architecture-light.svg" width="100%">
</picture>

Five parts and two paths. One path runs when a question is asked, the other when the corpus changes.

| Part | What it does | Built with |
|---|---|---|
| **Agent** | Asks, reads and writes the answer under the [answer protocol](#answer-protocol). Claude Code and Codex today. Any MCP client can call the gateway. | Model Context Protocol |
| **Gateway** | Publishes the eleven [evidence tools](#evidence-tools). Stateless, one bearer token per client. Tells the client that everything it returns is document text, never instructions. | Python, MCP |
| **Engine** | Hybrid search, section reader, identifier lookup and quote check. Serves one immutable index generation at a time. | Python, FastAPI, SQLite FTS5, Qdrant |
| **GPU services** | Embeddings and cross-encoder reranking. If they are down, the engine still answers with keyword search and reports what it could not use. | Text Embeddings Inference, `jina-embeddings-v5-text-small`, `jina-reranker-v3.5` |
| **Build pipeline** | Turns source documents into a validated [generation](#index-generations). | Python, git |

In the reference deployment these run on three machines joined by a private WireGuard overlay (Tailscale), with HTTPS between the services.

## Retrieval pipeline

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/figures/pipeline-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="docs/figures/pipeline-light.svg">
  <img alt="Deep32 retrieval pipeline: queries go to keyword and semantic search in parallel, results are fused, the top 15 are reranked, identical passages are folded, and 32 hits are delivered in three tiers." src="docs/figures/pipeline-light.svg" width="100%">
</picture>

| Stage | What happens | Setting |
|---|---|---|
| **Plan** | One call carries 1 to 8 queries. Identifiers go in as keyword queries, paraphrases as semantic queries. | `search_many` |
| **Keyword** | BM25 over SQLite FTS5 | 50 candidates per query |
| **Semantic** | 1024-dimension vectors, cosine distance, no quantization. A passage is embedded together with its document title and heading path. | 50 candidates per query |
| **Fuse** | Reciprocal rank fusion | k = 60, 30 kept |
| **Rerank** | A cross-encoder scores each candidate against the query that found it | top 15 |
| **Fold** | The same passage often appears in several books and releases. Copies with identical text fold into one hit that lists the others. | `also_in` |
| **Deliver** | 32 hits in three tiers: 8 full passages, 8 shorter ones and 16 pointers. Each is centered on its best-matching line and carries a reference for reading the whole section. | 2,400 / 800 / 250 characters |

The 32 in the name is that last row. The strip under the wordmark is one deep result drawn to scale.

**Each stage earns its place.** Share of 696 labeled questions whose correct passage is ranked first:

| Keyword alone | Semantic alone | Fused | Fused and reranked |
|:---:|:---:|:---:|:---:|
| 0.710 | 0.783 | 0.822 | **0.956** |

**It degrades in the open.** Every response carries a `degraded` list: the backends that answer needed and could not use. Keyword search always answers. The semantic leg and the reranker sit behind circuit breakers (three failures open a breaker for 30 seconds, then one trial call), so a GPU outage costs quality, not availability, and the agent is told.

## Evidence tools

The gateway gives the agent eleven tools and nothing else. There is no "answer this" tool: the agent has to find the evidence.

| Tool | What it returns |
|---|---|
| `search_rag` | One hybrid, keyword or semantic search. Each hit has a citation, its section path, the RFC's standing and a text window centered on the best-matching passage. |
| `search_many` | Up to 8 searches fused into one ranked list. Every query keeps its best passage in the result. |
| `get_outline` | A document's section tree |
| `get_section` | The full text of a section, paged, with its neighbors |
| `grep_corpus` | Literal or bounded regular-expression search over the source text |
| `find_command` | The CLI index: syntax, task steps, examples and show output. Abbreviations work (`sh ip ospf nei`). |
| `lookup_id` | Resolves an identifier: RFC and section numbers, BCP and STD numbers, IANA registry values, protocol terms, CLI commands, PowerShell cmdlets |
| `lookup_rfc` | RFC Editor metadata: status, obsoletes and obsoleted-by, updates, errata |
| `verify_quote` | Whether a quotation appears in a section: `exact`, `normalized` or `not_found` |
| `list_documents` | The corpus catalog |
| `rag_status` | Live health: generation, corpus fingerprint, counts, backend and breaker state |

## Answer protocol

The agent works to a fixed protocol.

1. **Resolve every identifier.** RFC numbers, LSA types, path attributes, CLI commands: looked up with `lookup_id` and `find_command`, never recalled.
2. **Search once, widely.** One `search_many` call with three to six queries, inside one collection when the question belongs to one.
3. **Read whole sections.** `get_outline`, then `get_section`, including the Restrictions and Defaults sections nearby. A search hit is a pointer, not evidence.
4. **Check standing.** `lookup_rfc` for every RFC cited. An obsoleted RFC is never presented as current, and errata are read.
5. **Verify every quotation.** `verify_quote` before a quotation is used.
6. **Compare the sources.** RFC text against vendor behavior and defaults. Differences are reported, not smoothed over.
7. **Say what is missing.** If the corpus does not cover it, the answer says so.

| Verdict | Meaning | Consequence |
|---|---|---|
| `exact` | The quotation appears character for character | It may be quoted |
| `normalized` | It matches once whitespace, RFC page breaks and hyphenation are collapsed | It may be quoted |
| `not_found` | It does not appear in that section | The quotation is dropped and the claim is re-read |

### Worked example

**Question.** What are BGP's default keepalive and hold timers?

The answer most often given from memory is "60 and 180 seconds". The tool results below are real, from the reference deployment on 14 November 2026.

| # | Check | Result |
|:---:|---|---|
| 1 | `lookup_rfc 4271` | Draft Standard. Obsoletes RFC 1771. Updated by twelve later RFCs. 16 errata on record, none against section 10. |
| 2 | `verify_quote` in RFC 4271, section 10:<br>"The suggested default value for the HoldTime is 90 seconds." | `normalized`<br>The sentence breaks across lines 5008 and 5009 of the RFC text. |
| 3 | `verify_quote` in RFC 4271, section 10:<br>"The suggested default value for the KeepaliveTime is 1/3 of the HoldTime." | `normalized` |
| 4 | `verify_quote` in Cisco IOS IP Routing: BGP Command Reference, `timers bgp`:<br>"Frequency (in seconds) with which the Cisco IOS software sends keepalive messages to its peer. The default is 60 seconds." | `exact` |
| 5 | `verify_quote`, same section:<br>"Interval (in seconds) after not receiving a keepalive message that the software declares a peer dead. The default is 180 seconds." | `exact` |
| 6 | `verify_quote` in RFC 4271, section 10:<br>"The suggested default value for the HoldTime is 180 seconds." | `not_found` |

**Answer.** The standard and the implementation differ. RFC 4271, section 10, suggests a hold time of 90 seconds and a keepalive of one third of it, which is 30 seconds. Cisco IOS and IOS XE default to a keepalive of 60 seconds and a hold time of 180 seconds. Both statements are quoted from their sources, and the answer names which is which.

Row 6 is the point of the system. The sentence a model is most likely to remember is one the RFC never wrote, and the check rejects it.

## Corpus

| Collection | Documents | What is in it |
|---|:---:|---|
| **IETF RFCs** | 365 | RFC text, with RFC Editor status, relations and errata |
| **Cisco documentation** | 489 | 263 TechNotes, 126 configuration guides, 48 command references, 17 API guides, 15 validated designs, 14 white papers, 3 MIBs and 3 release notes, for IOS XE, SD-WAN, Catalyst Center, ISE and Secure Client |
| **PowerShell** | 241 | Windows PowerShell 5.1 reference, the 7.x differences and the Windows networking modules. Opt-in. |
| **System** | rebuilt per release | Deep32's own source, configuration and design notes, so it can answer questions about itself. Opt-in. |

An opt-in collection is searched only when a query names it, so PowerShell help never competes with routing text.

Beside the passages, each build derives structured indexes that the lookup tools read:

| Index | Size | Source |
|---|---|---|
| RFC index | 9,842 RFCs with status and relations | RFC Editor |
| Errata | 1,217 records | RFC Editor |
| IANA registries | 2,366 entries | IANA |
| CLI index | about 150,000 command entries with mode, platform and release | The command references and configuration guides |
| Feature history | 2,578 entries | Vendor feature tables |

Every document has a manifest entry: source URL, SHA-256, platform, release, license terms and a redistribution flag. Vendor documents remain the property of their publishers, and this repository holds no vendor text.

## Index generations

The corpus changes. Answers must not change by accident, so an index is never edited in place.

- **Immutable generations.** A build produces a self-contained generation: a snapshot of the sources, the SQLite store with its keyword index and section map, the vectors, the structured indexes and a build report. The engine serves exactly one.
- **Content-derived ids.** Document and passage ids come from SHA-256 hashes of their content. An unchanged passage keeps its id across builds, so its embedding is reused. One corpus update embedded 904 new passages and reused 62,617.
- **A gate before serving.** A new generation is replayed against the labeled question set and compared with the serving one, question by question. The gate has zero slack: one question lost and not won back fails it.
- **Switch and rollback.** Serving a generation is one setting. Earlier generations stay on disk, so a rollback is the same switch in reverse, and the evaluation that follows a switch rolls back by itself on anything but a pass.
- **Clean inputs.** Conversion runs in a sandbox with no network. Converted text has to pass a quality gate before it can enter a build, including a scan for instruction-like lines, which is how a prompt injection planted in a document would look. Flagged lines are graded by a person.

## Evaluation

Retrieval is measured. Answer accuracy is not measured yet.

**Measured.** 696 questions on enterprise routing, switching, VPN, SD-WAN, network services and access control, each labeled with the passages that answer it.

| Measure | Result |
|---|:---:|
| Correct passage ranked first (hit@1) | 0.956 |
| Correct passage in the top 8 (hit@8) | 0.991 |
| Median time for one search through the gateway | about 0.57 s |

hit@1 is a lower bound. A top hit that nobody has judged yet counts as a miss, and judging those would put the figure between 0.956 and 0.983. Measured in October and November 2026 on the private reference deployment. The release will be measured again on its own corpus, and those numbers will be published with it.

**Planned.** The experiment this project exists for: a fixed set of protocol questions answered with the verification steps on and off, and scored for correctness against the sources.

## Reference deployment

Nothing here needs a data center. The reference system answers in about half a second on a server that is more than ten years old, a thin client and one consumer graphics card.

| Node | Hardware | Runs |
|---|---|---|
| Engine | A 2013 dual-socket Xeon server (AVX, no AVX2), as one virtual machine | Search API, SQLite, Qdrant |
| Gateway | A fanless thin client (Pentium Silver, no AVX) | MCP gateway |
| GPU and agent | A desktop PC with one 11 GB NVIDIA card, the models under WSL2 | Embeddings, reranker, the agent |

## Release plan

The release is planned as three images, verified against one signed manifest. This is a plan and may change.

| Image | Kind | Holds |
|---|---|---|
| **SYSTEM** | Bootable installer | A workstation node on OpenBSD: the engine and the gateway, a desktop with an Ask window and an offline reference library |
| **CONTENT** | Data | A ready-built index generation, the corpus blocks that may be redistributed and the content of the reference library |
| **GPU** | Bootable, and importable into WSL2 or Docker | Ubuntu with CUDA: the embeddings server, the reranker and the index builder |

- No key, token or sign-in ships on any image. Each install makes its own.
- Every node joins the installer's own Tailscale network. The design is the same on one machine as on several.
- One install matrix drives the hardware check, the install plan and the test record.
- Claude Code and Codex are the first agents. An OpenAI-compatible adapter for a locally served model is designed and comes later.

## Roadmap

- [x] Hybrid retrieval engine, gateway and evidence tools, in private use
- [x] Quotation checks and RFC standing checks
- [x] Gated index generations with rollback
- [x] Retrieval evaluation on a labeled question set
- [ ] Design document: the architecture and the verification method
- [ ] Answer-level evaluation with verification on and off
- [ ] Code release
- [ ] Signed release images
- [ ] Archived releases with a DOI

## Scope and limits

- **A verified quotation proves the text is in the source.** It does not prove the reasoning built on it.
- **The corpus bounds the answers.** Outside it, the right answer is "not covered".
- **Answer accuracy is unmeasured.** The figures above are for retrieval.
- **One vendor and the IETF.** The corpus holds Cisco documentation and RFCs. Other vendors' behavior is out of scope today.
- **The language model is hosted.** The question and the retrieved passages go to the model's provider. Everything else stays local.
- **It is not a lab.** A cited answer is a reason to test a change, not a replacement for testing it.

## Verifying

Commits are signed with this OpenPGP key. Tags and release manifests will be too.

```
B205 B406 5486 27AA BF52  50D8 A078 C5F2 5D02 5DD6
```

```sh
gpg --keyserver hkps://keys.openpgp.org --recv-keys B205B406548627AABF5250D8A078C5F25D025DD6
gpg --fingerprint B205B406548627AABF5250D8A078C5F25D025DD6
git verify-commit HEAD
```

The key is published on [keys.openpgp.org](https://keys.openpgp.org/search?q=B205B406548627AABF5250D8A078C5F25D025DD6). Release images will ship with a detached signature over their manifest and a verifier that checks the signer's fingerprint, not only that a signature exists.

## Citing

```bibtex
@software{cummings_deep32,
  author = {Cummings, Jeffrey A.},
  title  = {Deep32: Evidence-first question answering for network and security engineering},
  year   = {2026},
  url    = {https://github.com/hellobpdu/deep32},
  note   = {Pre-release}
}
```

A DOI will be added with the first archived release.

## License

No license is granted yet. The first release will carry the PolyForm Noncommercial License 1.0.0 for code and CC BY-NC 4.0 for documentation. Third-party software, models and documents keep their own terms. The two retrieval models named above are published by Jina AI under CC BY-NC 4.0.

## Author

**Jeffrey A. Cummings** · [ORCID 0009-0003-8091-4637](https://orcid.org/0009-0003-8091-4637) · [jacummings@acm.org](mailto:jacummings@acm.org) · [@hellobpdu](https://github.com/hellobpdu)

Security reports are welcome by email, encrypted to the key above.

<sub>Personal work. Not affiliated with or endorsed by my employer. Cisco, IOS and Catalyst are trademarks of Cisco Systems, Inc. Other names belong to their owners. No affiliation or endorsement is implied.</sub>
