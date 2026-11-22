"""deep32-engine API v2 (BUILD-P1 section 3): evidence for research agents.

    POST /v2/search          hybrid / bm25 / dense search; hits carry a citation, the section, the
                             RFC standing and a text window centred on the best-matching passage
    POST /v2/search_many     up to 8 queries fused into one list (reciprocal rank fusion); each
                             candidate reranked against its own queries; every query weighted 1
                             or more keeps its best candidate among the hits (so the hits are in
                             rank order, and their scores need not descend); retrieval.json
                             settings: SearchManySettings; "degraded_detail" says what happened
                             to each query (degraded_detail)
    GET  /v2/section         a section's full text, paged; ref = section id or chunk id; around = a
                             passage id or a file line to start at; with the citation, release,
                             platform, RFC standing and the same section at other releases
    GET  /v2/outline         a document's sections, or one section's subtree (under=)
    GET  /v2/documents       the corpus
    POST /v2/verify_quote    exact / normalized / not_found, with the location, whether it lies in the
                             cited section ("scope") and the sections it spans; retrieval.json
                             settings: VerifySettings
    POST /v2/grep            literal (default) or bounded regex search over the source texts
    GET  /v2/rfc/{n}         RFC standing, relations and errata (+ a section)
    GET  /v2/lookup          RFC / RFC section / BCP / STD / IANA / glossary / CLI identifiers; else
                             the sections a PowerShell cmdlet or API operation heads ("section")
    GET  /v2/command         CLI index, IOS abbreviations allowed

Bearer auth is applied where rag_core.api mounts the router. Searches and grep share the /search
admission slots (503 + Retry-After when they are exhausted); with serving.json pipeline.rerank_gate
1 their reranks pass one FIFO gate (rag_core.pipeline). Everything reads the root only.

Opt-in collections (retrieval.json opt_in_doc_types, default none): a search, search_many or grep
that names neither a doc_type nor docs leaves the documents of those doc types out (filter_docs),
and /v2/documents lists them only by doc_type.

Identical-text copies (retrieval.json fold_identical, default off): a search or search_many hit whose chunk
text and delivered window another, better-ranked hit already delivers (fold_key) is folded into that hit's
"also_in" and its place goes to the next candidate (HitBuilder.build). Off, the hits carry no "also_in" and are
the ones before.

Deep results (deep=true on /v2/search and /v2/search_many, default false): DEEP_HITS (32) hits in three tiers
(DEEP_TIERS): ranks 1-8 are the hits of the same request with limit 8, windows of max_chars as always; ranks 9-16
have windows of at most 800 characters, ranks 17-32 of at most 250, each centred on the best-matching line. Every
hit then carries "tier" (1, 2, 3), and a tier 2 or 3 hit carries
"get_section", the ref to read its section with. Every hit of every answer, deep or not, carries "reranked"
(whether the reranker scored it: its pool is retrieval.json reranker.candidates, 15, so ranks past it are the
fusion's order; false on every hit when the rerank was off, skipped or failed). `limit` is not used. search_many shares the 24 places of tiers 2
and 3 among its queries (deep_share). Without deep, every response is exactly the one before (tests/test_deep_tiers.py
pins 6afc29b's bodies).

Release preference (a search or search_many request's prefer_release, default none, e.g. ["17.9", "20.9", "2.3",
"3.1"]): with fold_identical on, a hit whose release is none of them is shown as a pinned copy folded into it, whose
delivered window is the hit's text byte for byte; the hit it was moves into also_in (HitBuilder.prefer). No rank, no
delivered text and no fold changes. Without fold_identical, or without prefer_release, the answer is the one before.

Top-tier evidence window (retrieval.json evidence.tier1_chars, default none): the hits delivered with the request's
full window (all of them without deep, tier 1 with it) are delivered with a window of that many characters
when the request asked for at least 2400 and less than that (HitBuilder.widen). Which hits, their order, folds
and also_in are decided on the request's own window size, as without the setting.

What else a hit says (retrieval.json evidence.covered_titles, evidence.table_context, default off): the headings of
the sections a merged passage covers ("covered_titles") and, for a window that begins inside a table, its caption
and header row ("table_context"); EvidenceSettings, HitBuilder.describe.

Every response carries "degraded": what this answer needed but could not use ("dense", "bm25",
"reranker", "kb", "structure"); [] means it is complete. BM25 always answers: a dense-only search
whose dense leg is down, failing or behind an open breaker is answered by BM25 and says so.
A backend the root is configured without counts as well: "dense" for a hybrid or dense query of a
root whose "retrievers" leave it out, "reranker" for a search that does not say rerank "off" on a
root without one, so a keyword-only or unreranked answer never reads as complete.
GET /v2/status (rag_core.api) reports the service as a whole.
"""
from __future__ import annotations

import itertools
import re
import sqlite3
import threading
import time
import unicodedata
from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Annotated, Any, Iterator, Literal

from fastapi import APIRouter, HTTPException, Path, Query, Request, status
from pydantic import BaseModel, Field

from rag_core import kb
from rag_core.errors import error_kind
from rag_core.reader import (
    DEFAULT_MAX_GAP_CHARS, VERIFY_ELLIPSIS, Doc, RootReader, Sec, chunk_rows, chunk_window, query_terms,
)
from rag_core.db import connect


router = APIRouter(prefix="/v2")

Mode = Literal["hybrid", "bm25", "dense"]
Rerank = Literal["auto", "on", "off"]

GREP_BUDGET_SECONDS = 3.0
RFC_SECTION_CHARS = 4000
# rag_core.structure: a root built by scripts/build_root.py has the index and its sources
BUILD_HINT = "serve a root built by scripts/build_root.py"


class Filters(BaseModel):
    doc_type: str | None = Field(default=None, max_length=32)
    docs: list[str] | None = Field(default=None, max_length=500)
    release_max: str | None = Field(default=None, max_length=32)
    include_obsolete: bool = True


# prefer_release (search, search_many): pinned releases such as "17.9", "20.9", "2.3", "3.1" (HitBuilder.prefer)
PinRelease = Annotated[str, Field(min_length=1, max_length=32, pattern=r"^[0-9]{1,3}(\.[0-9]{1,3}){0,3}[a-z]?$")]
PreferRelease = Annotated[list[PinRelease] | None, Field(min_length=1, max_length=8)]


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4096)
    limit: int = Field(default=8, ge=1, le=30)
    mode: Mode = "hybrid"
    rerank: Rerank = "auto"
    max_chars: int = Field(default=1200, ge=200, le=4000)
    filters: Filters = Field(default_factory=Filters)
    deep: bool = False      # DEEP_HITS hits in three tiers (DEEP_TIERS); limit is not used
    prefer_release: PreferRelease = None


class SubQuery(BaseModel):
    text: str = Field(min_length=1, max_length=4096)
    mode: Mode = "hybrid"
    weight: float = Field(default=1.0, gt=0, le=10)


class SearchManyRequest(BaseModel):
    queries: list[SubQuery] = Field(min_length=1, max_length=8)
    limit: int = Field(default=8, ge=1, le=30)
    max_chars: int = Field(default=1200, ge=200, le=4000)
    rerank: Rerank = "auto"
    filters: Filters = Field(default_factory=Filters)
    deep: bool = False      # as SearchRequest.deep; the deep places are shared among the queries (deep_share)
    prefer_release: PreferRelease = None


class VerifyQuoteRequest(BaseModel):
    ref: str = Field(min_length=1, max_length=512)
    quote: str = Field(min_length=1, max_length=5000)


class GrepRequest(BaseModel):
    pattern: str = Field(min_length=1, max_length=256)
    regex: bool = False
    docs: list[str] | None = Field(default=None, max_length=500)
    doc_type: str | None = Field(default=None, max_length=32)
    max_matches: int = Field(default=50, ge=1, le=200)
    ignore_case: bool = True


# --- shared state -------------------------------------------------------------------------------

_reader_lock = threading.Lock()


def reader(request: Request) -> RootReader:
    """The root's RootReader, loaded on first use (the /search startup path is unchanged)."""
    return root_reader(request.app.state)


def root_reader(state) -> RootReader:
    """The RootReader kept in the app state: made once, by the first request or by the
    source preload thread (rag_core.api), whichever comes first."""
    loaded = getattr(state, "reader", None)
    if loaded is None or loaded.paths != state.paths:
        with _reader_lock:
            loaded = getattr(state, "reader", None)
            if loaded is None or loaded.paths != state.paths:
                loaded = state.reader = RootReader(state.paths)
    return loaded


@contextmanager
def kb_db(request: Request, required: bool = False) -> Iterator[sqlite3.Connection | None]:
    db = kb.open_kb(request.app.state.paths.root / "kb" / "kb.sqlite")
    if db is None and required:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "knowledge base not built")
    try:
        yield db
    finally:
        if db is not None:
            db.close()


@contextmanager
def admitted(request: Request) -> Iterator[None]:
    """One of the /search admission slots (rag_core.api)."""
    slots = request.app.state.search_slots
    if not slots.acquire(timeout=request.app.state.search_queue_timeout):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "search capacity exhausted, retry shortly",
            headers={"Retry-After": "2"},
        )
    try:
        yield
    finally:
        slots.release()


def generation(request: Request) -> str:
    """The served index generation, fixed at startup (rag_core.api.lifespan): "generation" in
    serving.json with the root's overlay, else the root directory name. /v2/status reports the
    same value."""
    return request.app.state.generation


def ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


LEG_TIMERS = ("embed_ms", "qdrant_ms", "load_ms")


def leg_timings(legs: list[dict[str, Any]] | None) -> dict[str, float]:
    """timings_ms entries for the retrieval legs of one search: the slowest leg of each retriever
    ("bm25", "dense": they run in parallel, so the slowest one sets the retrieve stage), and the
    split of the slowest dense leg ("dense_embed", "dense_qdrant", "dense_load")."""
    out: dict[str, float] = {}
    slowest: dict[str, dict[str, Any]] = {}
    for leg in legs or ():
        name = leg.get("retriever")
        if name and isinstance(leg.get("ms"), (int, float)):
            if name not in slowest or leg["ms"] > slowest[name]["ms"]:
                slowest[name] = leg
    for name, leg in sorted(slowest.items()):
        out[name] = leg["ms"]
        for key in LEG_TIMERS:
            if isinstance(leg.get(key), (int, float)):
                out[f"{name}_{key[:-3]}"] = leg[key]
    return out


# --- search -------------------------------------------------------------------------------------

BM25_ONLY = frozenset({"bm25"})


def plan(request: Request, mode: str) -> tuple[frozenset[str] | None, set[str]]:
    """Retrievers to run for a mode, and what is degraded before anything runs: for hybrid,
    every retriever that failed to start, and dense when the root is configured without it
    (neither started nor failed to start); for one mode, that retriever if it is not running
    (BM25 answers in its place)."""
    orch = request.app.state.orchestrator
    if mode == "hybrid":
        before = set(orch.degraded)
        if "dense" not in orch.retrievers and "dense" not in orch.degraded:
            before.add("dense")         # configured off: a keyword-only answer says so
        return None, before
    if mode in orch.retrievers:
        return frozenset({mode}), set()
    return BM25_ONLY, {mode}


def newer_release(release: str | None, release_max: str) -> bool:
    """True when `release` is later than release_max on the same major line (17.x vs 17.9), or is
    numbered by year (26.x, from 2026) under a release_max numbered before (17.9, 20.9): kb.later_release.
    Other product lines (SD-WAN 20.x under 17.9, Catalyst Center 2.x) and unknown releases pass."""
    return kb.later_release(kb.version_key(release), kb.version_key(release_max))


def opt_in_doc_types(config: dict[str, Any]) -> frozenset[str]:
    """retrieval.json "opt_in_doc_types" (a root's overlay, e.g. set by scripts/derive_root.py --set
    or scripts/build_root.py --opt-in-doc-types): doc types that a search, search_many or grep
    leaves out unless its doc_type or docs names them, and that /v2/documents lists only by
    doc_type. Default []: every doc type is searched, as before. ValueError names a value it cannot
    take; rag_core.api checks it at start, as search_many_settings."""
    value = config.get("opt_in_doc_types", [])
    if not isinstance(value, list) or not all(isinstance(t, str) for t in value):
        raise ValueError(f"retrieval.json opt_in_doc_types must be a list of doc_type strings, not {value!r}")
    return frozenset(value)


def opt_in(request: Request) -> frozenset[str]:
    """The served root's opt-in doc types (opt_in_doc_types)."""
    return opt_in_doc_types(getattr(request.app.state.orchestrator, "config", None) or {})


GREP_KEYS = ("regex_engine",)


@dataclass(frozen=True)
class GrepSettings:
    """/v2/grep's keys in retrieval.json, "grep": {...} (a root's overlay, e.g. set by scripts/derive_root.py --set
    retrieval.grep.regex_engine=true); without them it works as it always did.

        "regex_engine": false | true
            false (default): a literal pattern is searched by stdlib re, which holds the GIL while it scans, a
            regex by the regex module. true: a literal too by the regex module, without the GIL
            (reader.EngineScan): the same matches, 6-44x sooner, and the other endpoints keep running beside it.
    """
    regex_engine: bool = False


def grep_settings(config: dict[str, Any]) -> GrepSettings:
    """The settings in a retrieval config. ValueError names a value they cannot take, or a regex_engine the
    environment cannot run (no regex module: deep32-engine's venv must have it, as production's has regex==2026.9.10);
    rag_core.api checks them at start, as search_many_settings."""
    grep = config.get("grep")
    if grep is None:
        return GrepSettings()
    if not isinstance(grep, dict) or set(grep) - set(GREP_KEYS):
        raise ValueError(f"retrieval.json grep must be an object of {list(GREP_KEYS)}, not {grep!r}")
    engine = grep.get("regex_engine", False)
    if not isinstance(engine, bool):
        raise ValueError(f"retrieval.json grep.regex_engine must be true or false, not {engine!r}")
    if engine:
        try:
            import regex  # noqa: F401
        except ImportError:
            raise ValueError("retrieval.json grep.regex_engine is true, but the regex module is not installed "
                             "(deep32-engine's venv needs regex, pinned regex==2026.9.10)") from None
    return GrepSettings(regex_engine=engine)


VERIFY_KEYS = ("ellipsis", "max_gap_chars")


@dataclass(frozen=True)
class VerifySettings:
    """/v2/verify_quote's keys in retrieval.json, "verify": {...} (a root's overlay, e.g. set by
    scripts/derive_root.py --set retrieval.verify.ellipsis=bounded); without them it works as it always did.

        "ellipsis": "any" | "bounded"
            The rule for a quotation with omissions ("...", "[...]" or the ellipsis character). any (default):
            its parts only have to occur in order, at any distance, in the cited section or else anywhere in
            the document. bounded: they must also lie in one section, each at most max_gap_chars after the
            part before it (reader.bounded_spans); otherwise the answer is not_found with "reason":
            "parts-too-far". A quotation without omissions is checked the same under both.
        "max_gap_chars": 600
            bounded only: the most characters between two neighbouring parts, counted in the comparison form
            (characters other than whitespace and hyphens, RFC page headers and footers left out), so the
            same omission counts the same in a hard-wrapped RFC and in a guide stored one paragraph a line.
            An integer of 0 or more.
    """
    ellipsis: str = VERIFY_ELLIPSIS[0]
    max_gap_chars: int = DEFAULT_MAX_GAP_CHARS


def verify_settings(config: dict[str, Any]) -> VerifySettings:
    """The settings in a retrieval config. ValueError names a value they cannot take; rag_core.api checks them at
    start, as search_many_settings."""
    verify = config.get("verify")
    if verify is None:
        return VerifySettings()
    if not isinstance(verify, dict) or set(verify) - set(VERIFY_KEYS):
        raise ValueError(f"retrieval.json verify must be an object of {list(VERIFY_KEYS)}, not {verify!r}")
    ellipsis = verify.get("ellipsis", VERIFY_ELLIPSIS[0])
    if ellipsis not in VERIFY_ELLIPSIS:
        raise ValueError(f"retrieval.json verify.ellipsis must be one of {list(VERIFY_ELLIPSIS)}, not {ellipsis!r}")
    gap = verify.get("max_gap_chars", DEFAULT_MAX_GAP_CHARS)
    if isinstance(gap, bool) or not isinstance(gap, int) or gap < 0:
        raise ValueError(f"retrieval.json verify.max_gap_chars must be an integer of 0 or more, not {gap!r}")
    return VerifySettings(ellipsis=ellipsis, max_gap_chars=gap)


def fold_identical(config: dict[str, Any]) -> bool:
    """retrieval.json "fold_identical" (a root's overlay, e.g. set by scripts/derive_root.py --set
    retrieval.fold_identical=true): false, null or unset (the default) or true. ValueError names a value it
    cannot take; rag_core.api checks it at start, as search_many_settings.

    true: HitBuilder folds a candidate whose chunk text and whose delivered window are each identical to a
    hit's (fold_key) into that hit instead of delivering it again, lists it in the hit's "also_in", and fills
    the freed place from the next candidates (the X2 replay's identical-text arm, "A_exact_nopin":
    staging/improve-2026-11/X2-REPLAY.md, with the window rule of staging/fold/FIXES.md: a fold never hides
    text the copy's own window would have shown).
    Unset or false, every response is exactly the one before the setting (tests/test_fold_identical.py pins
    4f4fe78's bodies), and /v2/status settings show nothing of it (rag_core.status)."""
    value = config.get("fold_identical")
    if value is None:
        return False
    if not isinstance(value, bool):
        raise ValueError(f"retrieval.json fold_identical must be true or false, not {value!r}")
    return value


def folding(request: Request) -> bool:
    """The served root's fold_identical."""
    return fold_identical(getattr(request.app.state.orchestrator, "config", None) or {})


EVIDENCE_KEYS = ("tier1_chars", "covered_titles", "table_context")
TIER1_MIN_CHARS = 2400      # deep32-gateway's default max_chars: a request below it asked for short windows
TIER1_MAX_CHARS = 4000      # the most a request's max_chars may ask for (SearchRequest)


@dataclass(frozen=True)
class EvidenceSettings:
    """retrieval.json "evidence": {...}: what a hit is delivered with. Without the object, or with a key unset,
    false or null, the hits are exactly the ones before it.

        "tier1_chars": null | N         evidence_tier1_chars (below)
        "covered_titles": false | true
            true: a hit of a passage merged from several sections carries "covered_titles", the headings of the
            sections it covers, in document order (RootReader.covered_titles, from chunk_spans.covered_json).
        "table_context": false | true
            true: a hit whose delivered window begins inside a table, below its header row, carries
            "table_context": {"caption", "header"} (RootReader.table_context, from the source lines above it).

    Both fields are added last (HitBuilder.describe), after the walk, prefer() and widen(), for the chunk and
    the lines the hit finally shows: no hit, order, text or fold changes. An engine whose RootReader has no
    such function (the Forge builder's copy validates a generation; it serves no agent) accepts the keys and
    adds nothing."""
    tier1_chars: int | None = None
    covered_titles: bool = False
    table_context: bool = False


def evidence_settings(config: dict[str, Any]) -> EvidenceSettings:
    """The settings in a retrieval config. ValueError names a value they cannot take; rag_core.api checks them at
    start, as search_many_settings."""
    evidence = config.get("evidence")
    if evidence is None:
        return EvidenceSettings()
    if not isinstance(evidence, dict) or set(evidence) - set(EVIDENCE_KEYS):
        raise ValueError(f"retrieval.json evidence must be an object of {list(EVIDENCE_KEYS)}, not {evidence!r}")
    flags = {}
    for key in ("covered_titles", "table_context"):
        value = evidence.get(key)
        if value is not None and not isinstance(value, bool):
            raise ValueError(f"retrieval.json evidence.{key} must be true or false, not {value!r}")
        flags[key] = bool(value)
    return EvidenceSettings(tier1_chars=evidence_tier1_chars(config), **flags)


def evidence_tier1_chars(config: dict[str, Any]) -> int | None:
    """retrieval.json "evidence": {"tier1_chars": null | N} (a root's overlay, e.g. set by scripts/derive_root.py
    --set retrieval.evidence.tier1_chars=3200): null or unset (the default), or an integer from TIER1_MIN_CHARS
    to TIER1_MAX_CHARS. ValueError names a value it cannot take; rag_core.api checks it at start, as
    search_many_settings.

    N: a hit delivered with the request's full window (every hit of an answer without tiers, tier 1 of a deep
    one) is delivered with a window of N characters instead, when the request's max_chars is at least
    TIER1_MIN_CHARS and less than N (HitBuilder.widen). The walk that picks and orders the hits still runs on
    the max_chars windows, so the hits, their order, the folds and also_in are the ones without the setting;
    only "text" and "local" of those hits differ. A request with a smaller max_chars (the deep research modes
    send 400 to 500) or one of N or more is answered exactly as without the setting.
    Unset or null, every response is exactly the one before the setting, and /v2/status settings show
    nothing of it (rag_core.status)."""
    evidence = config.get("evidence")
    if evidence is None:
        return None
    if not isinstance(evidence, dict) or set(evidence) - set(EVIDENCE_KEYS):
        raise ValueError(f"retrieval.json evidence must be an object of {list(EVIDENCE_KEYS)}, not {evidence!r}")
    chars = evidence.get("tier1_chars")
    if chars is None:
        return None
    if isinstance(chars, bool) or not isinstance(chars, int) or not TIER1_MIN_CHARS <= chars <= TIER1_MAX_CHARS:
        raise ValueError(f"retrieval.json evidence.tier1_chars must be null or an integer from {TIER1_MIN_CHARS} "
                         f"to {TIER1_MAX_CHARS}, not {chars!r}")
    return chars


FOLD_TOKEN = re.compile(r"[a-z0-9]+")


def fold_key(text: str | None) -> str:
    """What "identical text" compares (fold_identical), for the chunk text (chunks.text) and for the delivered
    window alike: the [a-z0-9]+ runs of the NFKC-normalised, lower-cased text, one space apart; the X2 replay's
    copy rule (x2lib.tokens, after algorithms-tools/dups.py). So case, punctuation, spacing and compatibility
    forms (fullwidth letters, ligatures) do not tell two texts apart; any other character, word or order does.
    "" for a text without any such run: never a copy."""
    return " ".join(FOLD_TOKEN.findall(unicodedata.normalize("NFKC", text or "").lower()))


def release_pins(prefer_release: list[str] | None) -> tuple[tuple[int, ...], ...]:
    """A request's prefer_release as version keys (kb.version_key), each once, in order: ("17.9", "2.3") ->
    ((17, 9), (2, 3)). () when it names none."""
    return tuple(dict.fromkeys(k for k in map(kb.version_key, prefer_release or ()) if k))


def pinned_release(release: str | None, pins: tuple[tuple[int, ...], ...]) -> bool:
    """A release is one of the pins when its version key starts with a pin's: pin 17.9 takes 17.9 and 17.9.4a, not
    17.18 or 17; pin 2.3 takes Catalyst Center 2.3.7.9. A document without a numbered release is never pinned."""
    key = kb.version_key(release)
    return bool(key) and any(key[:len(pin)] == pin for pin in pins)


def known_doc_type(corp: RootReader, doc_type: str) -> None:
    """A doc_type the corpus does not have is a 404 (search filters, grep)."""
    doc_types = sorted({doc.doc_type for doc in corp.docs.values()})
    if doc_type not in doc_types:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            f"unknown doc_type: {doc_type!r} (corpus has {doc_types})")


def filter_docs(
    corp: RootReader,
    db: sqlite3.Connection | None,
    filters: Filters,
    degraded: set[str],
    opt_in: frozenset[str] = frozenset(),
) -> tuple[frozenset[str] | None, frozenset[str] | None]:
    """Filters -> (the doc_ids retrieval may return, the doc_ids it must leave out); None: no
    restriction. A doc_key or doc_type the corpus does not have is a 404, as in /v2/grep: a typo
    must not silently narrow the search to nothing.

    The documents of an opt-in doc type (opt_in_doc_types) are left out unless doc_type or docs
    names them: without any filter as the second set, which is None when the corpus has none of
    them (the search then runs exactly as without the setting); with release_max or
    include_obsolete alone they are not in the first set. The two are never both given."""
    if filters == Filters():
        excluded = corp.doc_ids_of(opt_in) if opt_in else frozenset()
        return None, excluded or None
    if not corp.structured:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            f"filters need the structure index ({BUILD_HINT})")
    unknown = [d for d in dict.fromkeys(filters.docs or ()) if d not in corp.docs]
    if unknown:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown documents: {unknown[:10]}")
    if filters.doc_type is not None:
        known_doc_type(corp, filters.doc_type)
    named = filters.doc_type is not None or filters.docs is not None
    obsolete: set[int] = set()
    if not filters.include_obsolete:
        if db is None:
            degraded.add("kb")
        else:
            obsolete = {
                r["rfc"] for r in db.execute("SELECT rfc FROM rfc WHERE obsoleted_by != '[]'")
            }
    wanted = set(filters.docs) if filters.docs is not None else None
    return frozenset(
        doc.doc_id
        for doc in corp.docs.values()
        if (filters.doc_type is None or doc.doc_type == filters.doc_type)
        and (wanted is None or doc.doc_key in wanted)
        and not (filters.release_max and newer_release(doc.release, filters.release_max))
        and doc.rfc not in obsolete
        and (named or doc.doc_type not in opt_in)
    ), None


def retrieve(
    request: Request,
    text: str,
    *,
    mode: str,
    rerank: str,
    pool: int,
    doc_ids: frozenset[str] | None,
    degraded: set[str],
    embedded: dict | None = None,
    exclude_doc_ids: frozenset[str] | None = None,
) -> dict[str, Any]:
    """One query through the orchestrator. `embedded`: query vectors made beforehand
    (search_many's batch, orchestrator.query_vectors), used by the dense leg instead of TEI.
    `exclude_doc_ids`: documents left out (filter_docs' second set: the opt-in doc types)."""
    orch = request.app.state.orchestrator
    retrievers, before = plan(request, mode)
    degraded |= before
    if rerank != "off" and orch.reranker is None:
        degraded.add("reranker")            # asked for, or auto on a root configured without one

    def run(names: frozenset[str] | None) -> dict[str, Any]:
        result = orch.search(
            text,
            limit=pool,
            retrievers=names,
            doc_ids=doc_ids,
            rerank=rerank != "off",
            **({"embedded": embedded} if embedded else {}),
            **({"exclude_doc_ids": exclude_doc_ids} if exclude_doc_ids else {}),
        )
        degraded.update(e["retriever"] for e in result["task_errors"])
        degraded.update(result.get("degraded") or ())      # e.g. a retriever skipped by its breaker
        return result

    result = run(retrievers)
    answered = set(result.get("active_retrievers") or ())
    if retrievers is not None and "bm25" not in retrievers and not retrievers & answered:
        # the retriever asked for did not answer (it failed, or its breaker skipped it): BM25
        # answers. Judged on this call alone: search_many shares `degraded` across its queries.
        result = run(BM25_ONLY)
    return result


def _local_range(local: str | None) -> tuple[str, int, int] | None:
    m = re.fullmatch(r"(.+):(\d+)-(\d+)", local or "")
    return (m.group(1), int(m.group(2)), int(m.group(3))) if m else None


def _overlaps(a: tuple[str, int, int], b: tuple[str, int, int]) -> bool:
    """Same file and at least half of the shorter window shared."""
    if a[0] != b[0]:
        return False
    shared = min(a[2], b[2]) - max(a[1], b[1]) + 1
    return shared * 2 >= min(a[2] - a[1], b[2] - b[1]) + 1


# deep=true (SearchRequest.deep): (last rank of the tier, window characters at most; None: the request's max_chars)
DEEP_TIERS = ((8, None), (16, 800), (32, 250))
DEEP_HITS = DEEP_TIERS[-1][0]
DEEP_HEAD = DEEP_TIERS[0][0]    # tier 1: the hits of the same request with limit DEEP_HEAD


def deep_tier(n: int) -> int:
    """The tier (1, 2, 3) of the hit at 0-based position n (the last tier past DEEP_HITS: a candidate walked only
    for also_in, fold_identical)."""
    return next((t for t, (last, _) in enumerate(DEEP_TIERS, 1) if n < last), len(DEEP_TIERS))


@dataclass(frozen=True)
class DeepShare:
    """search_many deep: the places after tier 1 (DEEP_HITS - DEEP_HEAD, 24) shared among the queries. A candidate
    counts for the query that ranked it highest in the first stage (best_query); each query takes at most `cap` of
    those places (deep_share). A candidate whose query has taken its cap waits; places left once every candidate
    was offered go to the waiting ones in rank order, so 32 hits come back wherever the candidates have them."""
    queries: list
    cap: int

    def owner(self, cand: dict[str, Any]) -> str:
        return best_query(cand, self.queries)


def deep_share(queries: list) -> DeepShare:
    """Each query's cap: the 24 deep places split evenly, rounded up (1 query 24, 2 12, 3 8, 4 6, 8 3)."""
    return DeepShare(queries, -(-(DEEP_HITS - DEEP_HEAD) // len(queries)))


_LATER = object()       # HitBuilder.build: the candidates a DeepShare held back follow this mark


def standing_line(info: dict[str, Any], anchor: str | None) -> str:
    """An RFC's standing (kb.rfc_info), with the errata on the section `anchor` names, as a hit and /v2/section
    carry it in "status": "Proposed Standard; obsoletes RFC 1587; errata: 1 verified; errata on this section:
    EID 3001 (Verified)"."""
    line = info["standing"]
    base = anchor.split("-")[0] if anchor and anchor[0].isdigit() else anchor
    on = [e for e in info["errata"] if base and kb.errata_anchor(e["section"]) == base]
    if on:
        line += "; errata on this section: " + ", ".join(
            f"EID {e['id']} ({e['status']})" for e in on[:3]
        )
    return line


class HitBuilder:
    """Hit objects (BUILD-P1 section 3) for fused candidates."""

    def __init__(self, request: Request, db: sqlite3.Connection | None, max_chars: int,
                 degraded: set[str], prefer_release: list[str] | None = None, deep: bool = False):
        self.corp = reader(request)
        self.db = db
        self.max_chars = max_chars
        self.degraded = degraded
        # deep=true: the hits are tiered (DEEP_TIERS): their windows narrow with the rank, and each is labelled
        self.deep = deep
        self.rfcs: dict[int, dict[str, Any] | None] = {}
        # retrieval.json fold_identical: identical-text candidates fold into the hit they copy (build)
        self.fold = folding(request)
        # the request's prefer_release, which only picks among the copies folded into a hit (prefer)
        self.pins = release_pins(prefer_release) if self.fold else ()
        # prefer: each folded copy's row, document and window, by chunk id
        self.copies: dict[str, tuple[dict[str, Any], Doc | None, tuple[str, Sec | None, str | None]]] = {}
        # retrieval.json evidence.tier1_chars: the size the full-window hits are delivered with, when it is more
        # than the request asked for and the request asked for TIER1_MIN_CHARS or more (widen); None: as asked
        evidence = evidence_settings(getattr(request.app.state.orchestrator, "config", None) or {})
        tier1 = evidence.tier1_chars
        self.wide = tier1 if tier1 is not None and TIER1_MIN_CHARS <= max_chars < tier1 else None
        # retrieval.json evidence.covered_titles / table_context: the reader functions that make the two fields
        # (WP-E; None for a setting that is off, and in an engine copy whose RootReader has none: describe)
        self.covered_titles = getattr(self.corp, "covered_titles", None) if evidence.covered_titles else None
        self.table_context = getattr(self.corp, "table_context", None) if evidence.table_context else None
        # widen, describe: the row, document and terms each hit (and each folded copy prefer may show) was
        # windowed with; kept only when one of them will run
        self.keep_sources = self.wide is not None or self.covered_titles is not None or self.table_context is not None
        self.sources: dict[str, tuple[dict[str, Any], Doc | None, list[str]]] = {}
        if db is None:
            degraded.add("kb")

    def status(self, doc: Doc, anchor: str | None) -> str | None:
        if doc.rfc is None or self.db is None:
            return None
        if doc.rfc not in self.rfcs:
            self.rfcs[doc.rfc] = kb.rfc_info(self.db, doc.rfc)
        info = self.rfcs[doc.rfc]
        if info is None:
            return None
        return standing_line(info, anchor)

    def window(
        self,
        row: dict[str, Any],
        doc: Doc | None,
        terms: list[str],
        max_chars: int | None = None,
    ) -> tuple[str, Sec | None, str | None]:
        """A candidate's text window (centred on the best-matching passage), the section it is in, and the
        source lines it shows ("local"); without the structure index the chunk's own window, no section.
        Nothing is noted in degraded (place does that). max_chars: a deep tier's (chars_at), else the request's."""
        max_chars = self.max_chars if max_chars is None else max_chars
        if doc is not None and row.get("line_start"):
            ev = self.corp.evidence(
                doc, (row["line_start"], row["line_end"]), terms, max_chars
            )
            return ev["text"], ev["section"], ev["local"]
        return chunk_window(row["text"], terms, max_chars), None, None

    def chars_at(self, n: int) -> int | None:
        """The window size of the hit at 0-based position n: None (the request's max_chars) unless deep puts it in
        tier 2 or 3, whose windows are at most 800 and 250 characters (and never more than max_chars)."""
        if not self.deep:
            return None
        chars = DEEP_TIERS[deep_tier(n) - 1][1]
        return None if chars is None else min(chars, self.max_chars)

    def place(
        self,
        row: dict[str, Any],
        doc: Doc | None,
        terms: list[str],
        placed: tuple[str, Sec | None, str | None] | None = None,
        max_chars: int | None = None,
    ) -> tuple[str, Sec | None, str | None]:
        """window() for a candidate that is delivered or folded: without the structure index, "structure" is
        noted in degraded. `placed`: its window() already made (fold_identical compares it before it decides)."""
        if doc is None or not row.get("line_start"):
            self.degraded.add("structure")
        return placed if placed is not None else self.window(row, doc, terms, max_chars)

    def fold_into(
        self,
        hit: dict[str, Any],
        cand: dict[str, Any],
        row: dict[str, Any],
        doc: Doc | None,
        terms: list[str],
        placed: tuple[str, Sec | None, str | None],
    ) -> None:
        """fold_identical: a candidate with the chunk text and the delivered window of `hit` (fold_key of each)
        joins hit["also_in"], named and cited as it would be as a hit (its section is the one its own window,
        `placed`, shows), with its per_query_ranks in search_many. The same chunk listed twice joins once."""
        if row["chunk_id"] == hit["id"] or any(m["id"] == row["chunk_id"] for m in hit["also_in"]):
            return
        _, sec, _ = self.place(row, doc, terms, placed)
        member = {
            "id": row["chunk_id"],
            "doc_key": doc.doc_key if doc else None,
            "release": doc.release if doc else None,
            "section_id": sec.section_id if sec else None,
            "cite": doc.cite(sec) if doc else ' \u203a '.join(row["heading_path"]),
        }
        if "per_query_ranks" in cand:
            member["per_query_ranks"] = cand["per_query_ranks"]
        hit["also_in"].append(member)
        if self.pins:
            self.copies[row["chunk_id"]] = (row, doc, placed)
            if self.keep_sources:
                self.sources[row["chunk_id"]] = (row, doc, terms)

    def prefer(self, hits: list[dict[str, Any]]) -> None:
        """prefer_release (with retrieval.json fold_identical): a hit whose release is none of the pins
        (pinned_release), with a pinned copy among the ones folded into it, is shown as that copy instead, the
        best-ranked such copy whose delivered window is the hit's text byte for byte and whose source lines overlap
        no other hit's (_overlaps, as the walk's rule). The hit keeps its place, score, matched retrievers and text;
        it takes the copy's id, document, section, citation, standing, release, platform, source lines and url (and its
        per_query_ranks in search_many), and the hit it was moves to the front of also_in, named as any copy is.
        So the visible citation is the pinned release, nothing in the ranking or the delivered text changes,
        and the copies a hit stands for are the same set. A copy whose window differs in any byte (case,
        punctuation, spacing: the fold compares fold_key) stays in also_in. Runs after the walk, so it cannot move
        a hit, a fold or a refill."""
        wheres = [_local_range(h["local"]) for h in hits]
        for n, hit in enumerate(hits):
            if not hit["also_in"] or pinned_release(hit["release"], self.pins):
                continue
            for member in hit["also_in"]:
                if not pinned_release(member["release"], self.pins) or member["id"] not in self.copies:
                    continue
                row, doc, (text, sec, local) = self.copies[member["id"]]
                where = _local_range(local)
                if text != hit["text"] or (where and any(
                        other and _overlaps(where, other) for i, other in enumerate(wheres) if i != n)):
                    continue
                was = {
                    "id": hit["id"], "doc_key": hit["doc_key"], "release": hit["release"],
                    "section_id": hit["section_id"], "cite": hit["cite"],
                }
                if "per_query_ranks" in hit:
                    was["per_query_ranks"] = hit["per_query_ranks"]
                hit.update({        # the keys keep their order
                    "id": row["chunk_id"],
                    "doc_id": row["doc_id"],
                    "doc_key": doc.doc_key if doc else None,
                    "title": doc.title if doc else None,
                    "doc_type": doc.doc_type if doc else None,
                    "cite": doc.cite(sec) if doc else ' \u203a '.join(row["heading_path"]),
                    "section_id": sec.section_id if sec else None,
                    "section_path": sec.path if sec else row["heading_path"],
                    "status": self.status(doc, sec.anchor if sec else None) if doc else None,
                    "release": doc.release if doc else None,
                    "platform": doc.platform if doc else None,
                    "local": local,
                    "url": doc.url(sec) if doc else None,
                    **({"per_query_ranks": member["per_query_ranks"]}
                       if "per_query_ranks" in hit and "per_query_ranks" in member else {}),
                    # v9e merge (deep): a tier 2 or 3 hit's read ref follows its section, as build sets it
                    **({"get_section": sec.section_id if sec else row["chunk_id"]} if "get_section" in hit else {}),
                })
                hit["also_in"] = [was] + [m for m in hit["also_in"] if m is not member]
                wheres[n] = where
                break

    def widen(self, hits: list[dict[str, Any]]) -> None:
        """retrieval.json evidence.tier1_chars (self.wide): every hit delivered with the request's full window
        (all of them without deep, tier 1 with it) gets the window of self.wide characters instead: window() as
        the walk called it for this chunk, with the same terms, so it is centred on the same line and lies in
        the same section, and "local" names the lines it shows. It is made afresh at the larger size, not grown
        from the max_chars window. Runs last, after the walk and prefer(): which candidates are hits, their
        order, scores, sections, citations, folds and also_in were all decided on the max_chars windows and
        are not touched, so two hits of one file may now show overlapping lines. A hit prefer() shows as a pinned
        copy is widened as that copy, from the copy's own source lines."""
        for n, hit in enumerate(hits):
            if self.deep and n >= DEEP_HEAD:
                break                       # tiers 2 and 3 keep their sizes
            row, doc, terms = self.sources[hit["id"]]
            hit["text"], _, hit["local"] = self.window(row, doc, terms, self.wide)

    def describe(self, hits: list[dict[str, Any]]) -> None:
        """retrieval.json evidence.covered_titles and evidence.table_context: the two fields of a hit that say
        what its text does not. "covered_titles": the headings of the sections the passage covers, when it was
        merged from several (RootReader.covered_titles of the chunk's covered section ids, chunk_rows' "covered");
        "table_context": {"caption", "header"} when the delivered window begins inside a table below its header
        (RootReader.table_context of the document and the window's first source line, the first number of
        "local"). A field is absent when its function answers None. Runs last, for every hit of every tier, on
        the chunk the hit is finally shown as (a pinned copy after prefer) and the lines it finally shows
        (after widen); the keys are added after every other key."""
        for hit in hits:
            row, doc, _ = self.sources[hit["id"]]
            if self.covered_titles is not None:
                titles = self.covered_titles(row.get("covered") or [])
                if titles is not None:
                    hit["covered_titles"] = titles
            where = _local_range(hit["local"]) if self.table_context is not None and doc is not None else None
            if where:
                context = self.table_context(doc, where[1])
                if context is not None:
                    hit["table_context"] = context

    def build(
        self,
        conn,
        candidates: list[dict[str, Any]],
        terms_for,
        limit: int,
        share: DeepShare | None = None,
    ) -> list[dict[str, Any]]:
        """The first `limit` candidates (in their order: after the rerank) as hits, each one a passage no
        better-ranked hit shows: a candidate whose window shares at least half of the shorter window with a
        hit's in the same file is left out.

        With retrieval.json fold_identical (the X2 replay's identical-text arm, A_exact_nopin, with the window
        rule), first: a candidate whose chunk text is identical (fold_key) to a hit's AND whose own delivered
        window (the text it would be delivered with, `max_chars` and its terms) is identical (fold_key) to that
        hit's delivered text is not delivered again, but folded into that hit's "also_in" (every hit then has
        one, [] when nothing folded into it; the hit itself is kept as it is: the best-ranked copy). So a fold
        never hides text: what the folded copy would have shown, the hit shows. Its place goes to the next
        candidate, so `limit` hits come back wherever the candidates have them; once they are all placed, the
        rest of the candidates are walked only to fold their copies. A copy whose window differs (another
        section heading, other neighbouring text) is a candidate like any other. Unset, nothing of this runs:
        the hits are exactly the ones before. With the request's prefer_release too, a hit may then be shown as
        a pinned copy folded into it (prefer); without the fold, prefer_release changes nothing.

        Deep (HitBuilder(deep=True), `limit` DEEP_HITS): the same walk, with the window of the place a candidate
        would take (chars_at: tiers 2 and 3 narrower), and each hit labelled with its tier. fold_identical compares
        the max_chars windows in every tier (the ones it compares without deep), so a copy of a tier 1 hit that
        ranks past tier 1 still folds into it. `share` (search_many):
        past tier 1 a candidate whose query has taken its share of the places waits (DeepShare), and the waiting
        ones are walked last, in order, for the places left."""
        rows = chunk_rows(conn, [c["chunk_id"] for c in candidates])
        hits: list[dict[str, Any]] = []
        shown: list[tuple[str, int, int]] = []
        # fold_identical: (chunk text fold_key, delivered text fold_key) -> the hit; the chunk keys among them
        folds: dict[tuple[str, str], dict[str, Any]] = {}
        texts: set[str] = set()
        # deep share (search_many): places each query's candidates took past tier 1, and the ones held back
        taken: dict[str, int] = {}
        later: list[dict[str, Any]] = []
        walk = itertools.chain(candidates, [_LATER], later) if share is not None else candidates
        for cand in walk:
            if cand is _LATER:
                share = None                # the held-back candidates: for the places left, in order
                continue
            row = rows.get(cand["chunk_id"])
            if row is None:
                continue
            doc = self.corp.by_id.get(row["doc_id"])
            terms = terms_for(cand)
            chars = self.chars_at(len(hits)) if self.deep else None
            placed = None
            if self.fold:
                key = fold_key(row["text"])
                if key in texts:            # a hit has this chunk text: does it deliver this window too?
                    # (deep: the max_chars windows are compared whatever the tier, so a copy folds as without deep)
                    placed = self.window(row, doc, terms)
                    into = folds.get((key, fold_key(placed[0])))
                    if into is not None:
                        self.fold_into(into, cand, row, doc, terms, placed)
                        continue            # the text and the window of a better-ranked hit
                if len(hits) == limit:
                    continue                # every place is taken: walked on for also_in only
            owner = None
            if share is not None and len(hits) >= DEEP_HEAD:
                owner = share.owner(cand)
                if taken.get(owner, 0) >= share.cap:
                    later.append(cand)      # its query has its share of the deep places: it waits
                    continue
            text, sec, local = self.place(row, doc, terms, placed if chars is None else None, chars)

            where = _local_range(local)
            if where and any(_overlaps(where, other) for other in shown):
                continue                    # the same passage as a better-ranked hit
            if where:
                shown.append(where)

            hit = {
                "id": row["chunk_id"],
                "doc_id": row["doc_id"],
                "doc_key": doc.doc_key if doc else None,
                "title": doc.title if doc else None,
                "doc_type": doc.doc_type if doc else None,
                "cite": doc.cite(sec) if doc else ' \u203a '.join(row["heading_path"]),
                "section_id": sec.section_id if sec else None,
                "section_path": sec.path if sec else row["heading_path"],
                "status": self.status(doc, sec.anchor if sec else None) if doc else None,
                "release": doc.release if doc else None,
                "platform": doc.platform if doc else None,
                "score": round(float(cand.get("rerank_score", cand["fused_score"])), 6),
                "matched": cand["retrievers"],
                "text": text,
                "local": local,
                "url": doc.url(sec) if doc else None,
            }
            if "per_query_ranks" in cand:
                hit["per_query_ranks"] = cand["per_query_ranks"]
            if self.keep_sources:
                self.sources[row["chunk_id"]] = (row, doc, terms)
            if self.fold:
                hit["also_in"] = []
                # the fold key of its max_chars window (a deep tier 2 or 3 hit delivers a narrower one)
                delivered = fold_key(text if chars is None else (placed or self.window(row, doc, terms))[0])
                if key and delivered:
                    folds[(key, delivered)] = hit
                    texts.add(key)
            tier = deep_tier(len(hits)) if self.deep else 1
            if self.deep:
                hit["tier"] = tier
            hit["reranked"] = "rerank_score" in cand        # the reranker scored it (its score is a rerank score)
            if tier > 1:                    # the ref to read it in its section with (get_section)
                hit["get_section"] = sec.section_id if sec else row["chunk_id"]
            if owner is not None:
                taken[owner] = taken.get(owner, 0) + 1
            hits.append(hit)
            if len(hits) == limit and not self.fold:
                break
        if self.pins:
            self.prefer(hits)       # prefer_release: which copy each hit is shown as, nothing else
        if self.wide is not None:
            self.widen(hits)        # evidence.tier1_chars: the text and lines of the full-window hits, nothing else
        if self.covered_titles is not None or self.table_context is not None:
            self.describe(hits)     # evidence.covered_titles, table_context: two fields more, nothing else
        return hits


def pool_size(limit: int) -> int:
    """Fused candidates fetched per query: room to drop duplicate passages and filtered hits."""
    return min(60, max(3 * limit, 24))


@router.post("/search")
def search_v2(payload: SearchRequest, request: Request) -> dict[str, Any]:
    started = time.perf_counter()
    degraded: set[str] = set()
    # deep: DEEP_HITS hits from the pool of that many; the first DEEP_HEAD are limit 8's (the same candidates
    # in the same order: the fused list is cut after the rerank, and a longer cut starts with the shorter one)
    limit = DEEP_HITS if payload.deep else payload.limit
    with admitted(request), kb_db(request) as db:
        doc_ids, excluded = filter_docs(reader(request), db, payload.filters, degraded, opt_in(request))
        t = time.perf_counter()
        result = retrieve(
            request, payload.query, mode=payload.mode, rerank=payload.rerank,
            pool=pool_size(limit), doc_ids=doc_ids, degraded=degraded,
            exclude_doc_ids=excluded,
        )
        retrieve_ms = ms(t)
        t = time.perf_counter()
        terms = query_terms(payload.query)
        with connect(request.app.state.paths.sqlite / "rag.db") as conn:
            hits = HitBuilder(request, db, payload.max_chars, degraded, deep=payload.deep,
                              prefer_release=payload.prefer_release).build(
                conn, result["candidates"], lambda _: terms, limit,
            )
    rerank = result.get("rerank") or {}
    return {
        "hits": hits,
        "degraded": sorted(degraded),
        "timings_ms": {
            "retrieve": retrieve_ms,
            "rerank": rerank.get("ms"),
            # the wait at the pipeline's rerank gate (rag_core.pipeline), only behind one
            **({"rerank_wait": rerank["wait_ms"]} if "wait_ms" in rerank else {}),
            "assemble": ms(t),
            "total": ms(started),
            **leg_timings(result.get("legs")),
        },
        "generation": generation(request),
    }


FAIR_WEIGHT = 1.0       # a search_many query weighted at least this keeps a place in the hits
SEARCH_MANY_WORKERS = 4     # search_many_parallel true: queries retrieved at once, at most
RERANK_MODES = ("per_query", "best_query", "per_query_lists")       # search_many_rerank; the first is the default
SEARCH_MANY_KEYS = ("rerank_isolation",)        # retrieval.json "search_many": {...}


@dataclass(frozen=True)
class SearchManySettings:
    """/v2/search_many's keys in retrieval.json (the repo file or a root's overlay, e.g. set by
    scripts/derive_root.py --set retrieval.KEY=VALUE); without them it works as it always did.

        "search_many_parallel": false | true | N
            false (default): the queries are retrieved one after another. true: at most
            SEARCH_MANY_WORKERS of them at once, N: at most N; first one TEI call embeds every
            dense query (retrieve_each; a single query runs as with false). The answer is the
            same either way.
        "search_many_rerank": "per_query" | "best_query" | "per_query_lists"
            per_query (default): each head candidate is reranked against every query that
            retrieved it. best_query: once, against the query that ranked it highest
            (rerank_by_query). per_query_lists: each query's own first N candidates are
            reranked in a call of their own and the reranked lists are fused by rank, so
            no score of one call is compared with a score of another (rerank_lists); N is
            reranker.search_many_candidates (null: reranker.candidates), the same for
            every query.
        "search_many": {"rerank_isolation": false | true}
            false (default): one failed reranker call drops the rerank of the whole request (the fused
            order stands, degraded "reranker"), as always. true: a failed call costs its own query's
            scores only; the calls that answered are used (rerank_by_query, rerank_head), the answer is
            still degraded "reranker", and "degraded_detail" says "partial" and which queries were
            reranked. When every call fails the answer is the one without the setting.
    """
    workers: int = 0            # queries retrieved at once; 0: one after another
    rerank: str = RERANK_MODES[0]
    rerank_isolation: bool = False      # a failed rerank call costs its own query's scores only


def search_many_settings(config: dict[str, Any]) -> SearchManySettings:
    """The settings in a retrieval config. ValueError names a value they cannot take; rag_core.api
    checks them at start, so a bad one fails there and not in a request."""
    parallel = config.get("search_many_parallel", False)
    if parallel is None or parallel is False:
        workers = 0
    elif parallel is True:
        workers = SEARCH_MANY_WORKERS
    elif isinstance(parallel, int) and parallel >= 1:
        workers = parallel
    else:
        raise ValueError("retrieval.json search_many_parallel must be false, true or a number of queries "
                         f"(1 or more), not {parallel!r}")
    rerank = config.get("search_many_rerank", RERANK_MODES[0])
    if rerank not in RERANK_MODES:
        raise ValueError(f"retrieval.json search_many_rerank must be one of {list(RERANK_MODES)}, not {rerank!r}")
    many = config.get("search_many")
    if many is None:
        many = {}
    if not isinstance(many, dict) or set(many) - set(SEARCH_MANY_KEYS):
        raise ValueError(f"retrieval.json search_many must be an object of {list(SEARCH_MANY_KEYS)}, not {many!r}")
    isolation = many.get("rerank_isolation", False)
    if not isinstance(isolation, bool):
        raise ValueError(f"retrieval.json search_many.rerank_isolation must be true or false, not {isolation!r}")
    return SearchManySettings(workers=workers, rerank=rerank, rerank_isolation=isolation)


def breakers_closed(orch) -> bool:
    """Every retriever's circuit breaker is closed (rag_core.breaker). search_many's queries run at
    once only then: an open breaker skips every query's leg alike, but a half-open one lets one
    trial call through, and which query's leg took it would depend on timing."""
    return all(b.state == "closed" for b in (getattr(orch, "breakers", None) or {}).values())


def retrieve_each(
    request: Request,
    queries: list[SubQuery],
    settings: SearchManySettings,
    *,
    pool: int,
    doc_ids: frozenset[str] | None,
    degraded: set[str],
    timings: dict[str, float],
    exclude_doc_ids: frozenset[str] | None = None,
    legs: list[set[str]] | None = None,
) -> Iterator[dict[str, Any]]:
    """retrieve() for each search_many query (rerank off), the results in query order. `legs`, when given,
    receives one set per query, in query order: what that query needed and could not use (degraded_detail's
    "legs_down"); `degraded` gets their union, as always.

    By default one query after another, as search_many always did. With search_many_parallel, at
    most settings.workers at once, after orchestrator.query_vectors has embedded every dense query
    in one TEI call (timings["dense_embed"]; a query without a vector embeds its own). The caller
    fuses the results in query order either way, and each query keeps its own BM25 fallback and
    its own degraded entries (merged into `degraded` after the last one), so the hits are the same
    whenever the dense vectors are: TEI's differ in the last bits (at most 9e-8, measured
    2026-10-01) between a text embedded alone and in a batch, or in calls TEI happens to batch
    together. While a retriever breaker is not closed, the queries run one after another, and so
    does a single query: with nothing to run beside it or to batch it with, its TEI call made
    first would only hold up its BM25 leg instead of running beside it (+54 ms of retrieve at the
    profile's p50s, measured with fake legs)."""
    orch = request.app.state.orchestrator
    workers = min(settings.workers, len(queries))
    own: list[set[str]] = [set() for _ in queries]
    if legs is not None:
        legs.extend(own)
    if not workers or len(queries) < 2 or not breakers_closed(orch):
        for q, mine in zip(queries, own):
            result = retrieve(
                request, q.text, mode=q.mode, rerank="off",
                pool=pool, doc_ids=doc_ids, degraded=mine, exclude_doc_ids=exclude_doc_ids,
            )
            degraded |= mine
            yield result
        return

    embedded = None
    query_vectors = getattr(orch, "query_vectors", None)
    dense = [q.text for q in queries if q.mode in ("hybrid", "dense")]
    if query_vectors is not None and dense and (doc_ids is None or doc_ids):
        t = time.perf_counter()
        embedded = query_vectors(dense)
        if embedded:
            timings["dense_embed"] = ms(t)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(
                retrieve, request, q.text, mode=q.mode, rerank="off",
                pool=pool, doc_ids=doc_ids, degraded=mine, embedded=embedded,
                exclude_doc_ids=exclude_doc_ids,
            )
            for q, mine in zip(queries, own)
        ]
        results = [future.result() for future in futures]
    for mine in own:
        degraded |= mine
    yield from results


def best_query(cand: dict[str, Any], queries: list[SubQuery]) -> str:
    """The query that ranked `cand` highest in the first stage (its lowest per_query_ranks
    entry); on a tie the heavier query, then the earlier one, as keep_each_query orders them."""
    ranks = cand["per_query_ranks"]
    return min(ranks, key=lambda i: (ranks[i], -queries[int(i)].weight, int(i)))


def rerank_calls(
    reranker,
    jobs: dict[str, tuple[str, list[dict[str, Any]]]],
    failures: dict[str, BaseException] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """One reranker call per job {query index: (query text, its candidates)}, all at once (at most 8, within the
    reranker client's 16 connections): {query index: what the call returned}, in job order. Without `failures`
    the first failed call raises, without waiting for the calls still running (they end by the client's timeout
    and are discarded). With it (retrieval.json search_many.rerank_isolation) no failed call raises: every call
    is waited for (a hung one ends by the client's timeout), and a failed one is noted there as {query index:
    its exception}, in query order, and has no answer here."""
    answers: dict[str, list[dict[str, Any]]] = {}
    if jobs:
        pool = ThreadPoolExecutor(max_workers=len(jobs))
        try:
            futures = {i: pool.submit(reranker.rerank, text, mine) for i, (text, mine) in jobs.items()}
            if failures is None:
                done, _ = wait(futures.values(), return_when=FIRST_EXCEPTION)
                for future in done:
                    if future.exception() is not None:
                        future.result()         # raises the failure
            else:
                wait(futures.values())          # rerank_isolation: every call ends, by its timeout at the latest
            for i, future in futures.items():   # no failure, or rerank_isolation: every call is done
                if failures is not None and future.exception() is not None:
                    failures[i] = future.exception()
                    continue
                answers[i] = future.result()
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
    return answers


def rerank_by_query(
    reranker,
    queries: list[SubQuery],
    head: list[dict[str, Any]],
    rows: dict[str, dict[str, Any]],
    mode: str = RERANK_MODES[0],
    failures: dict[str, BaseException] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, float]]]:
    """Rerank the fused head of search_many. Each candidate is scored against every query that
    retrieved it (its per_query_ranks), and its rerank_score is the best of those scores: a
    candidate only another query found is judged by that query, not by the first one. With mode
    "best_query" (search_many_rerank) each candidate is scored once, against the query that
    ranked it highest (best_query): the reranker reads the head once, not once per query that
    found a candidate (its time grows with the characters it reads). One reranker call per query
    that has candidates in the head, all at once (at most 8, within the reranker client's 16
    connections), so a slow or hung service costs one timeout, not one per batch of calls.

    Returns the head in rerank order (candidates the service scored for none of their queries
    follow in fused order; candidates without a chunk row are dropped, as HitBuilder would),
    and each query's scores {query index: {chunk_id: score}}. per_query_ranks are not changed:
    they stay every query's first-stage rank. Raises as soon as any call fails, without waiting
    for the calls still running (they end by the client's timeout and are discarded): the
    caller keeps the fused order then.

    `failures` (retrieval.json search_many.rerank_isolation, rerank_calls): given a dict, no failed call raises;
    a failed one is noted there and has no entry in the scores, and the calls that answered are used as always:
    a candidate's rerank_score is the best of the scores it got, and one that no answered call scored follows
    the scored ones in fused order."""
    head = [c for c in head if c["chunk_id"] in rows]
    jobs: dict[str, tuple[str, list[dict[str, Any]]]] = {}
    for i, q in enumerate(queries):
        mine = [
            {"chunk_id": c["chunk_id"], "doc_id": rows[c["chunk_id"]].get("doc_id"),       # reranker.title
             "text": rows[c["chunk_id"]]["text"], "heading_path": rows[c["chunk_id"]]["heading_path"]}
            for c in head if str(i) in c["per_query_ranks"]
            and (mode != "best_query" or best_query(c, queries) == str(i))
        ]
        if mine:
            jobs[str(i)] = (q.text, mine)
    scores = {
        i: {item["chunk_id"]: float(item["rerank_score"]) for item in answer if item.get("rerank_score") is not None}
        for i, answer in rerank_calls(reranker, jobs, failures).items()
    }
    order = {c["chunk_id"]: n for n, c in enumerate(head)}
    out = []
    for c in head:
        best = [s[c["chunk_id"]] for s in scores.values() if c["chunk_id"] in s]
        out.append({**c, "rerank_score": max(best)} if best else dict(c))
    out.sort(key=lambda c: (
        "rerank_score" not in c, -c.get("rerank_score", 0.0), order[c["chunk_id"]],
    ))
    return out, scores


def rerank_lists(
    reranker,
    queries: list[SubQuery],
    head: list[dict[str, Any]],
    rows: dict[str, dict[str, Any]],
    depth: int,
    k: int,
    failures: dict[str, BaseException] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, float]]]:
    """search_many_rerank "per_query_lists": every query's own list reranked on its own, the reranked lists
    fused by rank.

    `head`: the candidates some query ranked among its first `depth` (rerank_head), in fused order. Query i's
    list is the candidates it ranked 1 to `depth` in the first stage (per_query_ranks), in that order; it goes
    to the reranker in one call with that query's text. One call per query that has a list, all at once
    (rerank_calls; `failures` as there). The order a call returns, counting only the candidates it scored, gives
    each of them a reranked rank r = 1, 2, ... for that query. A candidate's value is the weighted reciprocal
    rank sum over the queries that scored it, sum of weight / (k + r), with search_many's own k (rrf_k): only
    ranks are combined, never the scores of two calls (the reranker scores a list as a whole, so a score means
    something only beside the other scores of its call).

    Returns the head in the new order: the scored candidates by value, the higher first; equal values by the
    best reranked rank any query gave, then by chunk id; then the candidates no answered call scored, in fused
    order. A scored candidate carries the value as "rerank_score" (so "score" is that value and "reranked" is
    true); per_query_ranks stay the first-stage ranks. And each query's own scores {query index: {chunk_id:
    score}}, as rerank_by_query returns them: keep_each_query reads them to find the candidate a query's own
    call scored highest, which is that query's reranked rank 1."""
    head = [c for c in head if c["chunk_id"] in rows]
    jobs: dict[str, tuple[str, list[dict[str, Any]]]] = {}
    for i, q in enumerate(queries):
        mine = sorted((c for c in head if c["per_query_ranks"].get(str(i), depth + 1) <= depth),
                      key=lambda c: c["per_query_ranks"][str(i)])
        if mine:
            jobs[str(i)] = (q.text, [
                {"chunk_id": c["chunk_id"], "doc_id": rows[c["chunk_id"]].get("doc_id"),
                 "text": rows[c["chunk_id"]]["text"], "heading_path": rows[c["chunk_id"]]["heading_path"]}
                for c in mine
            ])
    scores: dict[str, dict[str, float]] = {}
    value: dict[str, float] = {}
    best: dict[str, int] = {}
    for i, answer in rerank_calls(reranker, jobs, failures).items():
        scored = [item for item in answer if item.get("rerank_score") is not None]
        scores[i] = {item["chunk_id"]: float(item["rerank_score"]) for item in scored}
        for r, item in enumerate(scored, 1):
            cid = item["chunk_id"]
            value[cid] = value.get(cid, 0.0) + queries[int(i)].weight / (k + r)
            best[cid] = min(best.get(cid, r), r)
    order = {c["chunk_id"]: n for n, c in enumerate(head)}
    out = [{**c, "rerank_score": value[c["chunk_id"]]} if c["chunk_id"] in value else dict(c) for c in head]
    out.sort(key=lambda c: (
        (0, -c["rerank_score"], best[c["chunk_id"]], c["chunk_id"]) if "rerank_score" in c
        else (1, order[c["chunk_id"]], 0, "")
    ))
    return out, scores


def rerank_head(
    request: Request,
    reranker,
    queries: list[SubQuery],
    ranked: list[dict[str, Any]],
    mode: str,
    degraded: set[str],
    isolate: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, float]], float]:
    """search_many's rerank: the fused head (the reranker's search_many_candidates, which are its candidates unless
    retrieval.json reranker.search_many_candidates says otherwise) through rerank_by_query (mode per_query_lists:
    every query's own first search_many_candidates through rerank_lists), and the breaker told how
    it went. (ranked, each query's scores, ms); a failure keeps the fused order, no scores, degraded "reranker".
    `isolate` (search_many.rerank_isolation): a call that fails costs its own query's scores only. While at least
    one call answered, the breaker is told one success (the service answers; a half-open trial closes it) and the
    answer is degraded "reranker"; when every call failed it is one failure (the first failed call's, in query
    order) and the fused order stands, exactly as without the setting."""
    breaker = getattr(reranker, "breaker", None)
    t = time.perf_counter()
    depth = getattr(reranker, "search_many_candidates", None) or reranker.candidates
    lists = mode == RERANK_MODES[2]
    if lists:       # per_query_lists: every candidate some query ranked among its own first `depth`
        head = [c for c in ranked if min(c["per_query_ranks"].values()) <= depth]
    else:
        head = ranked[:depth]
    with connect(request.app.state.paths.sqlite / "rag.db") as conn:
        rows = chunk_rows(conn, [c["chunk_id"] for c in head])
    failures: dict[str, BaseException] | None = {} if isolate else None
    try:
        if lists:
            k = int(request.app.state.orchestrator.config.get("rrf_k", 60))
            reranked, scores = rerank_lists(reranker, queries, head, rows, depth, k, failures)
        else:
            reranked, scores = rerank_by_query(reranker, queries, head, rows, mode, failures)
        if failures and not scores:
            raise next(iter(failures.values()))     # every call failed: one failure, the first in query order
        if lists:
            first = {c["chunk_id"] for c in head}
            ranked = reranked + [c for c in ranked if c["chunk_id"] not in first]
        else:
            ranked = reranked + ranked[len(head):]
        if breaker is not None:
            breaker.record_success()
        if failures:
            degraded.add("reranker")                # some calls failed; the scores of the others stand
    except Exception as exc:    # the fused order stands
        if breaker is not None:
            breaker.record_failure(error_kind(exc))
        degraded.add("reranker")
        scores = {}
    return ranked, scores, ms(t)


def gated_rerank_head(
    gate,
    request: Request,
    reranker,
    queries: list[SubQuery],
    ranked: list[dict[str, Any]],
    mode: str,
    degraded: set[str],
    timings: dict[str, float],
    isolate: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, float]], float | None]:
    """rerank_head behind the pipeline's rerank gate (rag_core.pipeline), held once across all of this
    search_many's calls (one job: no other search's rerank runs between them); timings["rerank_wait"] is the wait.
    As orchestrator._gated_rerank: an open breaker skips without queueing (its state is read, never allow());
    inside the gate allow() decides, as without it; a search the gate refuses or times out ("skip") makes no call,
    records no breaker failure and is degraded "reranker". The gate is released however the calls end, the first
    failure included (rerank_by_query raises without waiting for the others)."""
    breaker = getattr(reranker, "breaker", None)
    if breaker is not None and breaker.state == "open":
        timings["rerank_wait"] = 0.0
        degraded.add("reranker")
        return ranked, {}, None
    with gate.hold() as (admitted, wait_ms):
        timings["rerank_wait"] = wait_ms
        if not admitted or breaker is not None and not breaker.allow():
            degraded.add("reranker")
            return ranked, {}, None
        return rerank_head(request, reranker, queries, ranked, mode, degraded, isolate)


def degraded_detail(
    legs: list[set[str]],
    scores: dict[str, dict[str, float]],
    degraded: set[str],
) -> dict[str, Any]:
    """search_many's "degraded_detail": what happened to each query, which "degraded" (one list for the whole
    answer) cannot say. "queries": one entry per query, in order: its "index" (0-based), "legs_down" (the
    retrievers it needed and could not use, sorted: failed, skipped by a breaker, failed to start or configured
    off; a dense-mode query whose dense leg is down was answered by BM25) and "reranked" (the reranker scored
    at least one of its candidates). "rerank": "ok" (every call made answered), "partial"
    (search_many.rerank_isolation: some answered, some failed) or "skipped" (no call answered, or none was made:
    rerank off, no reranker, its breaker open, the rerank queue full, a failure without isolation, or nothing
    to rerank)."""
    answered = any(scores.values())
    return {
        "queries": [
            {"index": i, "legs_down": sorted(down), "reranked": bool(scores.get(str(i)))}
            for i, down in enumerate(legs)
        ],
        "rerank": "skipped" if not answered else "partial" if "reranker" in degraded else "ok",
    }


def best_of_query(
    i: str,
    ranked: list[dict[str, Any]],
    scores: dict[str, dict[str, float]],
) -> str | None:
    """Query i's best candidate: the one the reranker scored highest against it, else (no
    rerank, or none of its candidates reached the reranked head) the one it retrieved first."""
    mine = scores.get(i)
    if mine:
        position = {c["chunk_id"]: n for n, c in enumerate(ranked)}
        return min(mine, key=lambda cid: (-mine[cid], position.get(cid, len(position))))
    found = [c for c in ranked if i in c["per_query_ranks"]]
    return min(found, key=lambda c: c["per_query_ranks"][i])["chunk_id"] if found else None


def keep_each_query(
    ranked: list[dict[str, Any]],
    queries: list[SubQuery],
    limit: int,
    scores: dict[str, dict[str, float]],
) -> list[dict[str, Any]]:
    """Fairness, after the rerank: the best candidate of each query weighted FAIR_WEIGHT or
    more is placed among the first `limit`, so a query whose hits all rank below another's
    (a narrow facet next to a broad one, or one the reranker scores lower) still shows its
    best one. Heavier queries go first, then query order; at most `limit` places are kept.
    A guaranteed candidate that was already among the first `limit` does not move; one that
    was not takes the place of the lowest candidate that is not guaranteed. Lighter queries
    get no place: their weight only enters the fused score."""
    fair = sorted(
        (i for i, q in enumerate(queries) if q.weight >= FAIR_WEIGHT),
        key=lambda i: (-queries[i].weight, i),
    )
    present = {c["chunk_id"] for c in ranked}
    kept: list[str] = []
    for i in fair:
        best = best_of_query(str(i), ranked, scores)
        if best in present and best not in kept:
            kept.append(best)
    kept = kept[:limit]
    if set(kept) <= {c["chunk_id"] for c in ranked[:limit]}:
        return ranked
    free = limit - len(kept)
    top: list[dict[str, Any]] = []
    rest: list[dict[str, Any]] = []
    for c in ranked:
        if len(top) < limit and (c["chunk_id"] in kept or free > 0):
            if c["chunk_id"] not in kept:
                free -= 1
            top.append(c)
        else:
            rest.append(c)
    return top + rest


def deep_tail(
    head: list[dict[str, Any]],
    ranked: list[dict[str, Any]],
    queries: list[SubQuery],
    results: list[dict[str, Any]],
    k: int,
) -> list[dict[str, Any]]:
    """search_many deep: the candidates after `head` (limit 8's, whose hits are tier 1): every other candidate of
    the queries' deeper lists (pool_size(DEEP_HITS) each), fused as search_many fuses them (weighted reciprocal
    rank, per_query_ranks over every rank a query has), in that order. A candidate the reranker scored keeps its
    rerank_score (it is "reranked")."""
    seen = {c["chunk_id"] for c in head}
    scored = {c["chunk_id"]: c["rerank_score"] for c in ranked if "rerank_score" in c}
    wide: dict[str, dict[str, Any]] = {}
    for i, (q, result) in enumerate(zip(queries, results)):
        for rank, cand in enumerate(result["candidates"], 1):
            if cand["chunk_id"] in seen:
                continue
            item = wide.setdefault(cand["chunk_id"], {
                "chunk_id": cand["chunk_id"], "fused_score": 0.0,
                "retrievers": set(), "per_query_ranks": {},
            })
            item["fused_score"] += q.weight / (k + rank)
            item["retrievers"].update(cand["retrievers"])
            item["per_query_ranks"][str(i)] = rank
    tail = sorted(wide.values(), key=lambda c: (-c["fused_score"], c["chunk_id"]))
    for item in tail:
        item["retrievers"] = sorted(item["retrievers"])
        if item["chunk_id"] in scored:
            item["rerank_score"] = scored[item["chunk_id"]]
    return tail


@router.post("/search_many")
def search_many(payload: SearchManyRequest, request: Request) -> dict[str, Any]:
    """Each query runs on its own (no rerank; one after another, or several at once with
    search_many_parallel: retrieve_each), the ranked lists are fused by weighted
    reciprocal rank, and with rerank on (or auto and configured) the fused head is reranked,
    each candidate against the queries that retrieved it (rerank_by_query; with
    search_many_rerank "best_query" once, against the one that ranked it highest). Then every query
    weighted 1 or more keeps its best candidate among the first `limit` (keep_each_query).
    A hit's text window is centred on the terms of the queries that found it.
    "degraded_detail" says what happened to each query and to the rerank (degraded_detail).

    deep: the fusion, rerank and fairness of limit 8 (whose hits are tier 1), each query retrieved to
    pool_size(DEEP_HITS) for the candidates after them (deep_tail), and DEEP_HITS hits in all, whatever the number
    of queries: the 24 places of tiers 2 and 3 are shared among the queries (deep_share).

    For API clients: the hits are in rank order, which is not always descending "score". A
    candidate keep_each_query moved up keeps its own score (fused or rerank), so it can sit
    above hits that scored higher. Use the position, not the score, to order the hits."""
    started = time.perf_counter()
    degraded: set[str] = set()
    orch = request.app.state.orchestrator
    k = int(orch.config.get("rrf_k", 60))
    settings = search_many_settings(orch.config)
    timings: dict[str, float] = {}
    deep = payload.deep
    limit = DEEP_HEAD if deep else payload.limit
    with admitted(request), kb_db(request) as db:
        doc_ids, excluded = filter_docs(reader(request), db, payload.filters, degraded, opt_in(request))
        t = time.perf_counter()
        fused: dict[str, dict[str, Any]] = {}
        legs: list[set[str]] = []
        results = retrieve_each(
            request, payload.queries, settings,
            pool=pool_size(DEEP_HITS if deep else limit), doc_ids=doc_ids, degraded=degraded, timings=timings,
            exclude_doc_ids=excluded, legs=legs,
        )
        if deep:        # fused below as limit 8 fuses them: each query's first pool_size(8); the rest is deep_tail's
            deeper = list(results)
            results = [{**r, "candidates": r["candidates"][: pool_size(limit)]} for r in deeper]
        for i, (q, result) in enumerate(zip(payload.queries, results)):
            for rank, cand in enumerate(result["candidates"], 1):
                item = fused.setdefault(cand["chunk_id"], {
                    "chunk_id": cand["chunk_id"], "fused_score": 0.0,
                    "retrievers": set(), "per_query_ranks": {},
                })
                item["fused_score"] += q.weight / (k + rank)
                item["retrievers"].update(cand["retrievers"])
                item["per_query_ranks"][str(i)] = rank
        ranked = sorted(fused.values(), key=lambda c: (-c["fused_score"], c["chunk_id"]))
        for item in ranked:
            item["retrievers"] = sorted(item["retrievers"])
        retrieve_ms = ms(t)

        rerank_ms = None
        scores: dict[str, dict[str, float]] = {}
        reranker = orch.reranker
        if payload.rerank != "off" and reranker is None:
            degraded.add("reranker")            # asked for, or auto on a root configured without one
        if payload.rerank != "off" and getattr(orch, "rerank_error", None):
            degraded.add("reranker")            # configured, but it could not start
        breaker = getattr(reranker, "breaker", None)
        gate = getattr(orch, "rerank_gate", None)
        if payload.rerank != "off" and reranker is not None and ranked and gate is not None:
            ranked, scores, rerank_ms = gated_rerank_head(
                gate, request, reranker, payload.queries, ranked, settings.rerank, degraded, timings,
                settings.rerank_isolation,
            )
        elif payload.rerank != "off" and reranker is not None and ranked and breaker is not None \
                and not breaker.allow():
            degraded.add("reranker")            # its breaker is open: no call, no wait
        elif payload.rerank != "off" and reranker is not None and ranked:
            ranked, scores, rerank_ms = rerank_head(
                request, reranker, payload.queries, ranked, settings.rerank, degraded, settings.rerank_isolation,
            )
        detail = degraded_detail(legs, scores, degraded)
        ranked = keep_each_query(ranked, payload.queries, limit, scores)
        candidates = ranked[: pool_size(limit)]
        if deep:
            candidates += deep_tail(candidates, ranked, payload.queries, deeper, k)

        t = time.perf_counter()
        terms = {
            str(i): query_terms(q.text) for i, q in enumerate(payload.queries)
        }

        def terms_for(cand: dict[str, Any]) -> list[str]:
            return list(dict.fromkeys(
                term for i in cand["per_query_ranks"] for term in terms[i]
            ))

        with connect(request.app.state.paths.sqlite / "rag.db") as conn:
            hits = HitBuilder(request, db, payload.max_chars, degraded, deep=deep,
                              prefer_release=payload.prefer_release).build(
                conn, candidates, terms_for, DEEP_HITS if deep else limit,
                share=deep_share(payload.queries) if deep else None,
            )
    return {
        "hits": hits,
        "degraded": sorted(degraded),
        "degraded_detail": detail,
        "timings_ms": {
            "retrieve": retrieve_ms, "rerank": rerank_ms, "assemble": ms(t),
            "total": ms(started), **timings,
        },
        "generation": generation(request),
    }


# --- reading ------------------------------------------------------------------------------------

def _need_structure(corp: RootReader) -> None:
    if not corp.structured:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            f"structure index missing ({BUILD_HINT})")


def _structure_degraded(corp: RootReader) -> list[str]:
    """degraded for a KB answer: without the structure index its section ids and doc_keys
    cannot be checked against (or opened in) the corpus."""
    return [] if corp.structured else ["structure"]


@router.get("/section")
def get_section(
    request: Request,
    ref: str = Query(min_length=1, max_length=512),
    offset: int = Query(default=0, ge=0),
    max_chars: int = Query(default=20000, ge=1, le=100000),
    around: str | None = Query(default=None, min_length=1, max_length=80),
) -> dict[str, Any]:
    """A page of a section's text (ref: a section id or a chunk id), with its citation, the document's release
    and platform, an RFC's standing with the errata on this section ("status", as a hit carries it; null for
    another document or without the KB) and "same_section_in": the same section in the same book at other
    releases (RootReader.same_section_in; [] when there is none or it cannot be decided).

    `around` starts the page at a place inside the section instead of at `offset`: a passage id (chk_...: the
    first line of that passage, or the section's first line when the passage begins above it) or a line of the
    source file as "local" names them. The answer echoes it as "around" and says the offset it starts at in
    "offset"; read on with offset = next_offset. 400: `around` together with an offset, neither form, or a
    place outside the section; 404: an unknown passage, or one without source lines."""
    corp = reader(request)
    _need_structure(corp)
    found = corp.resolve(ref)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown section or chunk: {ref}")
    doc, sec = found
    if around is not None:
        if offset:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "give offset or around, not both")
        offset = corp.line_offset(doc, sec, around_line(corp, doc, sec, around.strip()))
    prev, nxt = corp.neighbours(doc, sec)
    page = corp.section_text(doc, sec, offset, max_chars)
    with kb_db(request) as db:
        info = kb.rfc_info(db, doc.rfc) if db is not None and doc.rfc is not None else None
    return {
        "doc_key": doc.doc_key,
        "section_id": sec.section_id,
        "title": sec.heading or doc.title,
        "cite": doc.cite(sec),
        "section_path": sec.path,
        "release": doc.release,
        "platform": doc.platform,
        "status": standing_line(info, sec.anchor) if info else None,
        **({"around": around} if around is not None else {}),
        **page,
        "prev_section_id": prev.section_id if prev else None,
        "next_section_id": nxt.section_id if nxt else None,
        "same_section_in": corp.same_section_in(doc, sec),
        "degraded": [],
    }


def around_line(corp: RootReader, doc: Doc, sec: Sec, around: str) -> int:
    """/v2/section's `around` as a line of the section: a passage id's first line (the section's first line when
    the passage begins above the section and reaches into it), or the line given. HTTPException otherwise."""
    span = f"{sec.section_id} (lines {sec.line_start}-{sec.line_end})"
    if around.startswith("chk_"):
        lines = corp.chunk_lines(around)
        if lines is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown passage: {around}")
        doc_id, first, last = lines
        if first is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"passage {around} has no source lines")
        if doc_id != doc.doc_id or last < sec.line_start or first > sec.line_end:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"passage {around} is not in {span}")
        return max(first, sec.line_start)
    if around.isascii() and around.isdigit():
        line = int(around)
        if not sec.line_start <= line <= sec.line_end:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"line {line} is outside {span}")
        return line
    raise HTTPException(status.HTTP_400_BAD_REQUEST,
                        "around must be a passage id (chk_...) or a line of the source file")


def subtree(doc: Doc, top: Sec, depth: int) -> list[Sec]:
    """`top` and the sections under it down to `depth` levels below it, in document order: the
    run of sections after `top` whose level is deeper than its own (as deep32-gateway's outline_subtree
    cuts a depth-6 outline). The preamble (level 0) is nobody's parent (rag_core.sections)."""
    out = [top]
    if top.level == 0:
        return out
    for sec in doc.sections[top.ordinal + 1:]:
        if sec.level <= top.level:
            break
        if sec.level <= top.level + depth:
            out.append(sec)
    return out


@router.get("/outline")
def get_outline(
    request: Request,
    doc: str = Query(min_length=1, max_length=200),
    depth: int = Query(default=3, ge=1, le=6),
    under: str | None = Query(default=None, min_length=1, max_length=512),
) -> dict[str, Any]:
    """A document's sections down to heading level `depth`. With `under` (a section id of this
    document, or its anchor alone), only that section and its subsections down to `depth`
    levels below it: one chapter of a guide with thousands of headings, cut here instead of
    by the caller from the whole outline."""
    corp = reader(request)
    _need_structure(corp)
    found = corp.docs.get(doc) or corp.docs.get(doc.lower())
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown document: {doc}")
    extra: dict[str, Any] = {}
    if under is None:
        sections = [s for s in found.sections if s.level <= depth]
    else:
        ref = under.strip()
        located = corp.resolve(ref if "#" in ref else f"{found.doc_key}#{ref}")
        if located is None or located[0] is not found:
            raise HTTPException(status.HTTP_404_NOT_FOUND,
                                f"unknown section in {found.doc_key}: {under}")
        sections = subtree(found, located[1], depth)
        extra["under"] = located[1].section_id
    return {
        "doc_key": found.doc_key,
        "title": found.title,
        **extra,
        "sections": [
            {
                "section_id": s.section_id, "level": s.level, "heading": s.heading,
                "n_chars": s.n_chars, "line_start": s.line_start, "line_end": s.line_end,
            }
            for s in sections
        ],
        "degraded": [],
    }


@router.get("/documents")
def list_documents(
    request: Request,
    doc_type: str | None = Query(default=None, max_length=32),
) -> dict[str, Any]:
    """The corpus, or the documents of one doc_type. Without doc_type the opt-in doc types
    (opt_in_doc_types) are left out and counted instead: "opt_in": {doc_type: documents}, present
    only when the corpus has such documents."""
    corp = reader(request)
    _need_structure(corp)
    hidden = frozenset() if doc_type else opt_in(request)
    counts: dict[str, int] = {}
    with kb_db(request) as db:
        documents = []
        for doc in corp.docs.values():
            if doc_type and doc.doc_type != doc_type:
                continue
            if doc.doc_type in hidden:
                counts[doc.doc_type] = counts.get(doc.doc_type, 0) + 1
                continue
            info = kb.rfc_info(db, doc.rfc) if db is not None and doc.rfc else None
            documents.append({
                "doc_key": doc.doc_key,
                "title": doc.title,
                "doc_type": doc.doc_type,
                "release": doc.release,
                "platform": doc.platform,
                "status": info["standing"] if info else None,
                "n_sections": len(doc.sections),
                "n_chunks": corp.n_chunks.get(doc.doc_id, 0),
                "source_url": doc.source_url,
            })
    # without the KB an RFC's status is unknown (null)
    return {"documents": documents, **({"opt_in": dict(sorted(counts.items()))} if counts else {}),
            "degraded": [] if db is not None else ["kb"]}


@router.post("/verify_quote")
def verify_quote(payload: VerifyQuoteRequest, request: Request) -> dict[str, Any]:
    """ref: a section id, a chunk id or a doc_key (whole document). The answer: "result" (exact, normalized,
    not_found), "section_id" (where the match begins), "local", "match_text", then "scope" (only for a section or
    chunk ref: "section" when the match lies in the cited section, "document" when it was found elsewhere in the
    document), "sections" (every section the match is in), "gap_chars" and, with not_found under verify.ellipsis
    "bounded", "reason": "parts-too-far" (RootReader.verify_quote; retrieval.json settings: VerifySettings)."""
    corp = reader(request)
    _need_structure(corp)
    found = corp.resolve(payload.ref)
    if found is None:
        doc = corp.docs.get(payload.ref.strip()) or corp.docs.get(payload.ref.strip().lower())
        if doc is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown reference: {payload.ref}")
        found = (doc, None)
    settings = verify_settings(getattr(request.app.state.orchestrator, "config", None) or {})
    return {**corp.verify_quote(*found, payload.quote, ellipsis=settings.ellipsis,
                                max_gap_chars=settings.max_gap_chars), "degraded": []}


@router.post("/grep")
def grep(payload: GrepRequest, request: Request) -> dict[str, Any]:
    """Scope: the documents named by docs, of doc_type, or both (as search filters); with neither,
    every document but those of the opt-in doc types (opt_in_doc_types)."""
    corp = reader(request)
    _need_structure(corp)
    if payload.docs is None:
        docs = list(corp.docs.values())
    else:
        unknown = [d for d in payload.docs if d not in corp.docs]
        if unknown:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown documents: {unknown[:10]}")
        docs = [corp.docs[d] for d in dict.fromkeys(payload.docs)]
    if payload.doc_type is not None:
        known_doc_type(corp, payload.doc_type)
        docs = [doc for doc in docs if doc.doc_type == payload.doc_type]
    elif payload.docs is None:
        hidden = opt_in(request)
        if hidden:
            docs = [doc for doc in docs if doc.doc_type not in hidden]
    engine = grep_settings(getattr(request.app.state.orchestrator, "config", None) or {}).regex_engine
    with admitted(request):
        try:
            matches, truncated = corp.grep(
                payload.pattern, regex=payload.regex, docs=docs,
                max_matches=payload.max_matches, ignore_case=payload.ignore_case,
                budget_seconds=GREP_BUDGET_SECONDS, **({"regex_engine": True} if engine else {}),
            )
        except (re.error, ValueError) as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"bad pattern: {exc}") from None
    return {"matches": matches, "truncated": truncated, "degraded": []}


# --- knowledge base -----------------------------------------------------------------------------

def _section_brief(corp: RootReader, section_id: str) -> dict[str, Any] | None:
    found = corp.resolve(section_id)
    if found is None:
        return None
    doc, sec = found
    return {
        "section_id": sec.section_id,
        "heading": sec.heading,
        "cite": doc.cite(sec),
        "section_path": sec.path,
        "local": doc.local(sec.line_start, sec.line_end),
        "n_chars": sec.n_chars,
    }


def heading_result(doc: Doc, sec: Sec) -> dict[str, Any]:
    """A /v2/lookup result of kind "section": the section a heading names, cited as a search hit is."""
    return {
        "kind": "section",
        "doc_key": doc.doc_key,
        "doc_type": doc.doc_type,
        "section_id": sec.section_id,
        "heading": sec.heading,
        "cite": doc.cite(sec),
        "local": doc.local(sec.line_start, sec.line_end),
    }


@router.get("/rfc/{number}")
def rfc(
    request: Request,
    number: int = Path(ge=1, le=99999),        # SQLite integers overflow past 2**63 - 1
    section: str | None = Query(default=None, max_length=64),
) -> dict[str, Any]:
    corp = reader(request)
    with kb_db(request, required=True) as db:
        info = kb.rfc_info(db, number)
    if info is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown RFC: {number}")
    doc = corp.docs.get(f"rfc{number}")
    out = {**info, "doc_key": doc.doc_key if doc else None, "degraded": _structure_degraded(corp)}
    if section:
        anchor = kb.section_anchor(section)
        found = corp.resolve(f"rfc{number}#{anchor}")
        titled = doc.by_title(anchor) if found is None and doc is not None else None
        if titled:
            found = doc, titled                     # a numbered heading asked for by its title
        if found is None:
            # in_corpus: whether the RFC is; found: this section of it is not
            out["section"] = {"section_id": f"rfc{number}#{anchor}", "in_corpus": doc is not None,
                              "found": False}
        else:
            sec = found[1]
            number_anchor = sec.anchor.split("-")[0] if sec.anchor[0].isdigit() else sec.anchor
            page = corp.section_text(*found, 0, RFC_SECTION_CHARS)
            out["section"] = {
                **_section_brief(corp, sec.section_id), "in_corpus": True, "found": True,
                "text": page["text"], "next_offset": page["next_offset"],
                "errata": [e for e in info["errata"]
                           if kb.errata_anchor(e["section"]) == number_anchor],
            }
    return out


@router.get("/lookup")
def lookup(
    request: Request,
    identifier: str = Query(alias="id", min_length=1, max_length=200),
) -> dict[str, Any]:
    """kb.lookup, then, only where it answers "unknown", the sections headed by the identifier
    (RootReader.headed: a PowerShell cmdlet or about topic, an HTTP API operation "METHOD /path"
    or a bare "/path"), as results of kind "section". An explicit identifier, so the opt-in doc
    types are not left out."""
    corp = reader(request)
    with kb_db(request, required=True) as db:
        found = kb.lookup(db, identifier)
    if found["kind"] == "unknown":
        headed = [heading_result(doc, sec) for doc, sec in corp.headed(identifier)]
        if headed:
            found = {"kind": "section", "results": headed}
    for result in found["results"]:
        if result["kind"] == "rfc_section":
            brief = _section_brief(corp, result["section_id"])
            # as in /v2/rfc: in_corpus whether the RFC is, found whether this section of it is
            result["in_corpus"] = f"rfc{result['rfc']}" in corp.docs
            result["found"] = brief is not None
            result.update(brief or {})
        elif "rfc" in result and result["kind"] in ("rfc", "bcp", "std"):
            doc = corp.docs.get(f"rfc{result['rfc']}")
            result["doc_key"] = doc.doc_key if doc else None
    return {**found, "degraded": _structure_degraded(corp)}


@router.get("/command")
def command(
    request: Request,
    q: str = Query(min_length=1, max_length=300),
    platform: str | None = Query(default=None, max_length=64),
    release: str | None = Query(default=None, max_length=32),
) -> dict[str, Any]:
    corp = reader(request)
    with kb_db(request, required=True) as db:
        results = kb.find_command(db, q, platform=platform, release=release)
    return {"results": results, "degraded": _structure_degraded(corp)}
