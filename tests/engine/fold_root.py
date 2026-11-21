"""The fold fixture: tests/v2_root.py's tiny root plus sibling copies of its Cisco guide, for retrieval.json
fold_identical (rag_core.api_v2.HitBuilder, the X2 replay's identical-text fold). Not a test module.

prepare(paths, kb_src) adds, before the structure and KB are built (RootClient's prepare):
  c9300-261-rtng   CISCO_179 under another title line: chunks 0, 2 and 3 have CISCO_179's text exactly, chunk 1
                   differs in one word (a near copy, never folded), and chunk 4 repeats chunk 3 in the same document
  c9300-1712-rtng  chunk 0 has CISCO_179's chunk 0 words in another case, other punctuation and spacing, and
                   fullwidth letters (the same fold key only after NFKC); chunk 1 is its own
  punct-a, punct-b one chunk each with the same text and no [a-z0-9] run at all (an empty fold key: never a copy)

REQUESTS are the request shapes whose response bodies tests/data/fold_off_4f4fe78.json pins: captured from
rag-stack 4f4fe78 (deep32-engine's served head when fold_identical was added) by capture() on this root, timing values
masked (masked()). WordReranker is test_api_v2's deterministic stand-in, copied so the pinned bodies do not move
with that module.
"""
from __future__ import annotations

import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import json
import re
from unittest import mock

from v2_root import CISCO_179, add_document

TOKEN = "v" * 40
AUTH = {"Authorization": f"Bearer {TOKEN}"}

CISCO_261 = "\n".join(
    ["# Configuring OSPF (Cisco IOS XE 26.1)"]
    + CISCO_179.split("\n")[1:-1]
    + ["### show ip ospf neighbor (again)",
       "To display OSPF neighbor information, use the show ip ospf neighbor command.",
       "show ip ospf neighbor [interface-type interface-number] [neighbor-id] [detail]",
       ""]
).replace("command to force translation.", "command to force the translation.")

CISCO_1712 = "\n".join([
    "# Configuring OSPF, 17.12",
    "ospf is a LINK STATE interior   gateway protocol",
    "## Restrictions",
    "An  NSSA ABR translates \"Type 7\" LSAs into Type‑5 LSAs, only when it is the "
    "ＥＬＥＣＴＥＤ translator!",
    "## Neighbors",
    "Display OSPF neighbors and their NSSA translator state with the show ip ospf neighbor detail command.",
    "",
])

PUNCT_A = "# Separator A\n— · —\n"
PUNCT_B = "# Separator B\n— · —\n"

SIBLINGS = [
    # doc_key, doc_type, text, title
    ("c9300-261-rtng", "cisco", CISCO_261, "Routing Configuration Guide, 26.1"),
    ("c9300-1712-rtng", "cisco", CISCO_1712, "Routing Configuration Guide, 17.12"),
    ("punct-a", "cisco", PUNCT_A, "Separator A"),
    ("punct-b", "cisco", PUNCT_B, "Separator B"),
]


def prepare(paths, kb_src) -> None:
    for key, doc_type, text, title in SIBLINGS:
        add_document(paths, key, doc_type, text, title)


class WordReranker:
    """test_api_v2.WordReranker: scores a document by the share of the query's words it contains (times a
    per-query factor), and records each call as (query, chunk ids)."""
    candidates = 10

    def __init__(self, boost=None):
        self.boost = boost or {}
        self.calls = []
        self.texts = {}

    @staticmethod
    def words(text):
        return {w.strip(".,()").lower() for w in text.split() if len(w) > 2}

    def score(self, query, chunk_id):
        wanted = self.words(query)
        have = self.words(self.texts[chunk_id])
        return len(wanted & have) / len(wanted) * self.boost.get(query, 1.0)

    def rerank(self, query, items):
        self.calls.append((query, [i["chunk_id"] for i in items]))
        for n, item in enumerate(items, 1):
            self.texts[item["chunk_id"]] = item["text"]
            item["rerank_score"] = self.score(query, item["chunk_id"])
            item["fused_rank"] = n
        return sorted(items, key=lambda i: -i["rerank_score"])


# name: (path, body, rerank with WordReranker)
REQUESTS = {
    "search_default": ("/v2/search", {"query": "NSSA ABR elected translator Type-7 LSAs"}, False),
    "search_limit3": ("/v2/search", {"query": "show ip ospf neighbor", "limit": 3, "max_chars": 300}, False),
    "search_limit1": ("/v2/search", {"query": "area nssa translate type7 always", "limit": 1}, False),
    "search_cisco": ("/v2/search", {"query": "OSPF NSSA translator", "mode": "bm25", "rerank": "off",
                                    "filters": {"doc_type": "cisco"}, "limit": 10, "max_chars": 4000}, False),
    "search_docs": ("/v2/search", {"query": "OSPF link-state interior gateway protocol",
                                   "filters": {"docs": ["c9300-179-rtng", "c9300-261-rtng", "c9300-1712-rtng"]},
                                   "limit": 5}, False),
    "search_release": ("/v2/search", {"query": "NSSA translator Type-7", "filters": {"release_max": "17.9"}},
                       False),
    "search_dense": ("/v2/search", {"query": "Type-7 translator", "mode": "dense", "limit": 4}, False),
    "search_rerank": ("/v2/search", {"query": "NSSA ABR elected translator", "rerank": "on", "limit": 4}, True),
    "search_30": ("/v2/search", {"query": "OSPF", "limit": 30, "max_chars": 200}, False),
    "many_two": ("/v2/search_many", {"queries": [{"text": "NSSA ABR elected translator", "mode": "bm25"},
                                                 {"text": "show ip ospf neighbor"}], "limit": 6}, False),
    "many_weighted": ("/v2/search_many", {"queries": [{"text": "Type-7 LSAs NSSA translator", "mode": "bm25"},
                                                      {"text": "OSPF neighbor", "weight": 0.5},
                                                      {"text": "link-state interior gateway protocol",
                                                       "weight": 2}],
                                          "filters": {"doc_type": "cisco"}, "limit": 4, "max_chars": 600}, False),
    "many_rerank": ("/v2/search_many", {"queries": [{"text": "NSSA ABR elected translator", "mode": "bm25"},
                                                    {"text": "area nssa translate type7 always", "mode": "bm25"}],
                                        "rerank": "on", "limit": 5}, True),
}

_TIMINGS = re.compile(r'"timings_ms":\{[^{}]*\}')
_NUMBER = re.compile(r':-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?')


def masked(content: bytes) -> str:
    """A response body as sent, with every number in timings_ms (wall-clock milliseconds, different on every run)
    replaced by 0: the keys, their order and every other byte are kept."""
    text = content.decode("utf-8")
    return _TIMINGS.sub(lambda m: _NUMBER.sub(":0", m.group(0)), text)


def post(client, path, body, rerank=False):
    """One request; with rerank, behind WordReranker (no boost) as the root's reranker. The raw response."""
    orch = client.app.state.orchestrator
    if rerank:
        with mock.patch.object(orch, "reranker", WordReranker()):
            return client.post(path, json=body, headers=AUTH)
    return client.post(path, json=body, headers=AUTH)


def capture(client) -> dict[str, str]:
    """Every REQUESTS shape on a RootClient(prepare=prepare)'s client: {name: masked body}."""
    out = {}
    for name, (path, body, rerank) in REQUESTS.items():
        r = post(client, path, body, rerank)
        if r.status_code != 200:
            raise AssertionError(f"{name}: {r.status_code} {r.text}")
        out[name] = masked(r.content)
    return out


def load_pinned(path) -> dict[str, str]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)["bodies"]
