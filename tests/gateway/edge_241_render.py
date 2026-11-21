"""edge 2.4.1's text rendering of search_rag and search_many, a verbatim copy (rag_edge.py lines 486-614 of
the deployed 2.4.1): NO_HITS through render_hits. Not a test module.

test_also_in.py renders deep32-engine answers without also_in (every answer of a deep32-engine without retrieval.fold_identical)
and with also_in [] on every hit with this copy and with the edge as it is, and requires the same bytes. Keep it
verbatim; it is 2.4.1's, so it never needs a refresh.
"""

from __future__ import annotations

import json
import re
from typing import Any, Mapping

# ---- verbatim from rag_edge.py 2.4.1, lines 486-614
NO_HITS = "no passages matched; try other wording, mode=bm25 for exact identifiers, or grep_corpus"
# Every container tag this module emits.
TAGS = ("hit", "query", "section", "outline", "documents", "verify", "matches", "rfc", "lookup", "commands")
_OUR_TAG = re.compile(rf"<(/?)({'|'.join(TAGS)})\b", flags=re.IGNORECASE)
_ATTR_SAFE = str.maketrans({'"': "'", "<": "＜", ">": "＞"})
_QUERY_SAFE = str.maketrans({"<": "＜", ">": "＞"})
DEBUG_KEYS = frozenset({"score", "matched", "generation", "timings_ms", "debug"})


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _flat(value: Any, limit: int = 300) -> str:
    text = ", ".join(map(str, value)) if isinstance(value, list) else str(value)
    return " ".join(text.split())[:limit]


def _attrs(values: Mapping[str, Any]) -> str:
    """Attribute values are one line with no double quote and no angle bracket: they cannot carry a tag."""
    pairs = ((k, _flat(v).translate(_ATTR_SAFE)) for k, v in values.items() if v not in (None, "", [], {}))
    return " ".join(f'{k}="{v}"' for k, v in pairs)


def _body(text: str) -> str:
    """Quoted document text: defuse every tag of ours, opening or closing ("<hit" becomes "<hit_"), so the text
    can neither end its container early nor forge another one, such as a <hit> with a fake citation."""
    return _OUR_TAG.sub(r"<\1\2_", text)


def _number(value: Any) -> Any:
    return f"{value:.4g}" if isinstance(value, float) else value


def _ranks(ranks: Mapping[str, Any]) -> str:
    # per_query_ranks keys are 0-based indexes into `queries`; shown 1-based like the <query n=...> lines.
    return ",".join(f"{int(i) + 1 if str(i).isdigit() else i}:{r}" for i, r in ranks.items())


def render_hit(hit: Mapping[str, Any], n: int, debug: bool = False) -> str:
    attrs = {"cite": hit.get("cite"), "id": hit.get("id"), "section": hit.get("section_id"),
             "status": hit.get("status"), "release": hit.get("release"), "local": hit.get("local")}
    if hit.get("per_query_ranks"):
        attrs["q"] = _ranks(hit["per_query_ranks"])
    if debug:
        attrs |= {"score": _number(hit.get("score")), "matched": hit.get("matched")}
    return f"<hit n={n} {_attrs(attrs)}>\n{_body(hit.get('text') or '')}\n</hit>"


def _without_debug(res: Mapping[str, Any]) -> dict[str, Any]:
    """A search payload minus what only debug=true shows: scores, matched retrievers, generation and timings."""
    hits = [{k: v for k, v in h.items() if k not in DEBUG_KEYS} for h in res.get("hits") or []]
    return {k: v for k, v in res.items() if k not in DEBUG_KEYS} | {"hits": hits}


def with_degraded(res: Mapping[str, Any], text: str) -> str:
    """Every deep32-engine v2 answer lists in `degraded` what it could not use ("dense", "kb", "structure"...): a text
    answer says so on its first line, so the model knows a partial answer is partial."""
    degraded = res.get("degraded") or []
    return "degraded: " + "; ".join(map(str, degraded)) + "\n" + text if degraded else text


def _rank(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def render_query(n: int, q: SubQuery) -> str:
    """<query n=1 mode="bm25">text</query>. The text is the model's own, but it is shown without angle brackets, like an
    attribute value: it can neither close <query> nor forge a tag, and parsers that look for the first "<hit" (as
    eval_v2 does) never find one in it. The query sent to deep32-engine is unchanged."""
    return f'<query n={n} mode="{q.mode}">{_flat(q.text, 2000).translate(_QUERY_SAFE)}</query>'


def render_per_query(queries: list[SubQuery], hits: list[Mapping[str, Any]]) -> str | None:
    """The line after search_many's hits:
    "per query: 1 → hits 1, 2, 4 (first-stage best 1); 2 → hit 5; 3 (weight 0.5) → no hit".

    deep32-engine p1.1 keeps the best passage of each query weighted 1 or more among the hits (while `limit` allows), so
    the hits are in rank order and their scores need not descend. This line shows that guarantee at work: the hits
    each query retrieved (per_query_ranks), in list order, and the one it ranked highest itself. per_query_ranks are
    first-stage ranks (before any rerank), hence "first-stage best": with the reranker on, the passage deep32-engine kept
    for a query is the reranker's pick, which can be another of its hits. "no hit" names a query none of whose
    passages made the list. The line comes after the hits, so the <query> lines and the hits keep edge v2's layout,
    which eval_v2's parser reads. It is left out for a single query, when nothing matched, and when deep32-engine does
    not report per_query_ranks for every hit."""
    if len(queries) < 2 or not hits or not all(isinstance(h.get("per_query_ranks"), Mapping) for h in hits):
        return None
    parts = []
    for i, q in enumerate(queries):
        mine = [(rank, n) for n, h in enumerate(hits, 1)
                if (rank := _rank(h["per_query_ranks"].get(str(i)))) is not None]
        label = f"{i + 1}" + (f" (weight {q.weight:g})" if q.weight != 1.0 else "")
        if not mine:
            parts.append(f"{label} → no hit")
        elif len(mine) == 1:
            parts.append(f"{label} → hit {mine[0][1]}")
        else:
            parts.append(f"{label} → hits {', '.join(str(n) for _, n in mine)} (first-stage best {min(mine)[1]})")
    return "per query: " + "; ".join(parts)


def opt_in_note(doc_types: tuple[str, ...]) -> str:
    """What the no-hits line of a search or grep adds when the call left opt-in collections out (opt_in_unsearched),
    e.g. " (opt-in collections powershell, system were not searched; pass doc_type= to search one)": without it the
    model would read "no passages matched" as the whole corpus. "" when none were left out."""
    if not doc_types:
        return ""
    if len(doc_types) == 1:
        return f" (opt-in collection {doc_types[0]} was not searched; pass doc_type= to search it)"
    return f" (opt-in collections {', '.join(doc_types)} were not searched; pass doc_type= to search one)"


def render_hits(res: dict[str, Any], fmt: str, debug: bool, queries: list[SubQuery] | None = None,
                opt_in: tuple[str, ...] = ()) -> str:
    """`opt_in`: the opt-in collections a search without hits left out (opt_in_unsearched), named after NO_HITS."""
    if fmt == "json":
        return _json(res if debug else _without_debug(res))
    hits = res.get("hits") or []
    out = [render_query(n, q) for n, q in enumerate(queries or [], 1)]
    out += [render_hit(h, n, debug) for n, h in enumerate(hits, 1)] or [NO_HITS + opt_in_note(opt_in)]
    per_query = render_per_query(queries or [], hits)
    if per_query:
        out.append(per_query)
    if debug:
        out.append("debug: " + _json({k: res[k] for k in ("generation", "timings_ms", "debug") if k in res}))
    return with_degraded(res, "\n".join(out))
# ---- end of the verbatim 2.4.1 code
