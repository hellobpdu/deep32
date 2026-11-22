# Citation style

## Cite forms

| Source | Form | Example |
|---|---|---|
| RFC section | `[RFC <n> §<section>]` | `[RFC 4271 §9.1.2.2]` |
| RFC appendix | `[RFC <n> App. <id>]` | `[RFC 2328 App. A.4.5]` |
| RFC that is not current | add its standing | `[RFC 3376 §4.1, obsoleted by RFC 9776]` |
| Cisco document | `[<short title> <release> › <heading path>]` | `[IP Routing CG IOS XE 17.x › OSPF NSSA › Configuring an NSSA ABR as a Forced NSSA LSA Translator]` |
| Cisco command reference | `[<CR short title> <release> › <command>]` | `[C9300 CR 17.18 › export-protocol netflow-v9]` |
| IANA registry | `[IANA <registry>: <code>]` | `[IANA OSPFv2 LS Types: 7]` |

Copy the hit's `cite` attribute, which already has this form. Always add the location in the `local` form,
`<file>:<line>` or `<file>:<start>-<end>` (for example `rfc3101.txt:917`), so any reader can find the passage in the
corpus at that line.

## Quote labels

Every quote is 25 words or fewer, copied character for character (keep the source's capital letters, even
mid-sentence), and carries exactly one label:

| Label | Meaning |
|---|---|
| **verified** | `verify_quote` returned `exact` |
| **normalized** | `verify_quote` returned `normalized`: it matched after joining a page break, hyphenation or whitespace |
| **unverified** | could not be checked; never present it as fact, and say why |

A result with `scope` `document` matched outside the section you named, and the reply says `found outside <ref>:
cite <section>`. Cite that section (its `cite`) with the `local` the check gave, never the section you asked about.

A quote is one unbroken run of the source text: never put `…` or `...` in it. To cite two separated sentences, give
two quotes, each with its own `verify_quote` check and its own marker. Never paraphrase inside quotation marks, and
never change case or punctuation: a quote that matches only after that is not verified.

## Answer template

This compact template is for when the user asks for a short or quick answer. The default layout is
`${CODEX_HOME}/skills/deep32/expanded-answer.md`.

```
**Answer.** At most 150 words. Direct, pinned to the release, and it corrects a false premise first.

**Evidence**
- <claim>: [RFC 3101 §3.1] | "<quote>" (verified · rfc3101.txt:870)
- <claim>: [IP Routing CG IOS XE 17.x › …] | "<quote>" (normalized · iosxe17-rtr-iprouting-cg.txt:93419)

**RFC vs Cisco.** Where the standard and IOS XE differ: defaults, extra knobs, compatibility modes, or
behaviour the RFC leaves open. Write "No difference found" if you checked and there is none.

**Confidence and gaps.** High, medium or low, and why. List missing documents, release drift (the document's
release against the pins), obsoleted or updated RFCs not in the corpus, and unverified quotes.

**Read.** Section ids read, then the search mode and depth.
```

For a troubleshooting answer, replace the single Answer with ranked hypotheses (see troubleshooting.md).

## Rules

- Every quoted string anywhere in the answer, including the RFC vs Cisco section and the gaps, is followed by its
  `(label · file:line)` marker.
- One claim per bullet, and each claim has its own citation. Merge bullets only when one quote proves both.
- Cite the most specific section. `[RFC 2328]` alone is not a citation.
- When sources disagree, quote both. Mark the claim **disputed** and propose a lab check: the commands to run and
  what each outcome would show, labelled "lab check, not evidence".
- Say "not in the corpus" rather than filling a gap from memory. Background knowledge is allowed only when it is
  labelled "unsourced".
