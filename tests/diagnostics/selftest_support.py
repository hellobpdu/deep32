"""Invented selftest bundle bytes for offline diagnostics tests only."""

from copy import deepcopy
import hashlib
import json

from deep32_diag.selftest_contract import aggregate, aggregate_numerators


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


def bundle_bytes(*, fixture_only=True, questions=None, ranks=None):
    """Return invented q/ref bytes; no filesystem, clock or service access.

    Default: two public-demo beacon/kite questions, first ranks 1 and 2,
    release 0.1.0-alpha.2, sample-generation and all-zero corpus fingerprint.
    Optional ``questions`` is a replacement question array; ``ranks`` must
    match its length. The fixture flag is explicit in both documents.
    """
    if questions is None:
        questions = [
            {"id": "fixture-beacon", "text": "What colour is the sample beacon?",
             "collection": "fixture", "labels": [
                 {"doc_key": "fixture-beacon-manual", "exact_span": "The sample beacon is amber."}]},
            {"id": "fixture-kite", "text": "What shape is the paper kite?",
             "collection": "fixture", "labels": [
                 {"doc_key": "fixture-kite-manual", "exact_span": "The paper kite is a diamond."}]},
        ]
    if ranks is None:
        ranks = [1, 2] if len(questions) == 2 else [None] * len(questions)
    if len(ranks) != len(questions):
        raise ValueError("Invented ranks must match the question count.")
    document = {"schema_version": 1, "bundle_id": "fixture-beacons-v1",
                "release": "0.1.0-alpha.2", "fixture_only": fixture_only,
                "questions": deepcopy(questions)}
    questions_raw = encode(document)
    settings = {"fold_identical": False, "reranker": {
        "candidates": 15, "max_chars": 1600, "window": "match"}}
    reference = {
        "schema_version": 1, "bundle_id": document["bundle_id"],
        "release": document["release"], "fixture_only": fixture_only,
        "questions_sha256": hashlib.sha256(questions_raw).hexdigest(),
        "question_count": len(questions), "generation": "sample-generation",
        "corpus_fingerprint": "0" * 64,
        "models": {
            "embedding": {"model": "fixture-embedding", "revision": "1" * 40},
            "reranker": {"model": "fixture-reranker", "revision": "2" * 40}},
        "request": {"mode": "hybrid", "rerank": "auto", "max_chars": 2400,
                    "deep": True, "format": "json", "debug": True},
        "settings": settings,
        "settings_sha256": hashlib.sha256(encode(settings)).hexdigest(),
        "reference": dict(aggregate(ranks)), "numerators": aggregate_numerators(ranks),
        "policy": {"kind": "maximum-absolute-drop", "tolerance": 0.02},
        "measurement": {"complete_runs": 3, "identical_ranks": True,
                        "hardware_class": "invented fixture; no hardware measurement",
                        "gateway_version": "fixture-only", "search_p50_ms": 1,
                        "search_p95_ms": 2},
    }
    return questions_raw, encode(reference)
