# Production document converter

## What this is

This is the pinned add8 PyMuPDF document conversion chain and its separate command-line adapter. The converter code is licensed under AGPL-3.0-or-later; see ../LICENSE. Project documentation is licensed under CC-BY-4.0; see the repository licenses/LICENSE-DOCS. It prepares a source, converts it to text, cleans the text, applies document rules and writes quality and instruction checks.

## Layout

chain/ mirrors the build tree and holds 25 pinned Python files. pipeline_v21.py holds three verbatim pipeline copies; adapter.py holds the document orchestration copies and the separate program interface. CHAIN.json lists 27 code files with their sizes, hashes and origins. Its chain_id hashes the sorted file paths and hashes. README.md and CHAIN.json are outside that list. chain_sha256 hashes CHAIN.json itself.

## The five transforms

| Id | File | Single line change |
|---|---|---|
| T1 | pilot/vendor-add8/pdf_subset.py | Remove a private build-tree path from a docstring. |
| T2 | pilot/vendor-add8/prep_add8.py | Replace a catalogue scope phrase in a docstring. |
| T3 | pilot/vendor-add3/fetch_add3.py | Replace a catalogue scope phrase in a docstring. |
| T4 | pilot/vendor-add/fetch_add.py | Use the distribution's fixed user agent. |
| T5 | pilot/local-add5/prep_doc/wp_ise_c9800_gbp.py | Remove a literal address from a docstring. |

## Commands

```text
python3 -I -B adapter.py convert --input FILE --output DIR --job FILE [--lexicon DIR] [--python PATH]
python3 -I -B adapter.py fetch --request FILE --output DIR [--python PATH]
python3 -I -B adapter.py chain-id
```

Each command verifies the chain first. Output folders must be new. JSON files use ASCII escapes, one-space indentation and LF. Standard output is one JSON object with tool, tool_version, command and status, followed by command results. Errors also carry exit and error; the same message goes to standard error.

The convert job has these keys, in order:

| Key | Type | Meaning |
|---|---|---|
| format | string | deep32-convert-job |
| format_version | integer | 1 |
| doc_key | string | Lower-case document key. |
| entry | object | doc_key, title, doc_type; optional sections (string list) and pdf_pages (string). |
| overrides | object | Prepare, finish and PDF rules, or reason fields. |
| source | object | file, format, sha256, url, method; the last two may be null. Formats: pdf, html, epub, mib-json. |
| review | object | reviewed_lines (objects with line and reason strings), short_reason (string or null). |

The conversion result has these keys, in order:

| Key | Type | Meaning |
|---|---|---|
| format | string | deep32-convert-result |
| format_version | integer | 1 |
| status | string | ok or failed. |
| doc_key | string | Document key. |
| chain | object | name, chain_id, chain_sha256, pymupdf_version (null except for a PDF run). |
| source | object | file, format, sha256, bytes of the input. |
| rules | object or null | Effective conversion rules. |
| steps | list | Pipeline steps used. |
| notes | list | Step summaries, without timings. |
| finish | object or null | Finish rule counts and problems. |
| v21 | object or null | sha256 before finish; null for MIB. |
| text | object or null | sha256, bytes, lines of text.txt. |
| qa | object or null | Quality result after review. |
| injection | object or null | Instruction scan result. |
| invisible_check | integer or null | Invisible character count. |
| regraded | boolean | Whether a review changed the quality result. |
| error | string or null | Exception type and reason on a failed step. |

A conversion may complete with a FAIL quality verdict; its caller applies the quality gate. Reviewed benign instruction lines and a reviewed short source can change a FAIL to WARN. Reviews name the reviewer operator. Optional lexicon text files affect only the run-together-words metric.

The fetch request has these keys:

| Key | Type | Meaning |
|---|---|---|
| format | string | deep32-fetch-request |
| format_version | integer | 1 |
| entry | object | Required doc_key, title, doc_type, fetch strings; optional html_url, pdf_url, epub_url, page_end strings and chapter_urls string list. |

The fetch result has these keys:

| Key | Type | Meaning |
|---|---|---|
| format | string | deep32-fetch-result |
| format_version | integer | 1 |
| status | string | Fetch record status. |
| doc_key | string | Document key. |
| record | object | Fetch record with fetched_at removed. |
| file | string or null | Relative root/cisco/ path of the fetched file. |

The fetch command also leaves catalog.json, rfcs.txt, overrides.json, fetch.out, fetch.err and root/ for inspection. It succeeds only when the fetched file exists and matches the recorded hash. chain-id returns chain_id, chain_sha256 and files.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Completed; conversion reports exist or fetch succeeded. |
| 2 | Command usage error. |
| 3 | Bad job, request, input or existing output folder. |
| 4 | Missing or invalid chain manifest, or a listed file differs. |
| 5 | Conversion step or quality report failed. |
| 6 | Fetch failed. |

## How each source format runs

PDF runs an optional page subset, clipping, band or dehyphenation pass, then convert and postclean, followed by finish. The v2.1 pipeline's optional prepare steps do nothing for a PDF. HTML runs prep_source, document preparation, prep_classes, convert and postclean, then finish. Optional add8 preparation rules run before that pipeline on a copy. ePub runs prep_source, convert and postclean, then finish; optional add8 preparation rules process its HTML members on a re-zipped copy. MIB JSON checks the stored MIB text hashes, extracts the requested inclusive line ranges and applies the always-on finish rules. It does not run the v2.1 pipeline. Every route runs quality and instruction checks. Added documents have no page map.

## What it never does

Convert has no network activity or listener. It writes only under --output, keeps its input unchanged and reads only its input, job file, optional lexicon folder and its own code. Fetch alone contacts source addresses. The adapter does not import builder code. It stores no timing or current-time field in its result.

## Windows

Windows is a test platform only. The adapter normalises CR LF to LF in final and v2.1 text before hashing on Windows. The chain writes LF itself on the Linux target. The lab procedure checks native LF output there.

## Tests

The offline converter suite is in tests/converter/. Its PDF reader stand-in is test data and is copied only into throwaway converter copies. tests/converter/lab/compare_pymupdf.py compares that PDF structure with real PyMuPDF on the lab target. Run tests/run_all.py with --tmp DIR to run the offline suites.
