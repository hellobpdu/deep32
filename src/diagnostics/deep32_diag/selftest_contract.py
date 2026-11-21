"""Pure, bounded validation and delivered-passage scoring for selftest.

``load_bundle`` authenticates no files: its caller must bind the exact input
bytes to the installed signed release before using the returned bundle.  The
Python-only ``fixture_harness`` argument permits invented data in tests; it is
not a command-line option.  ``first_rank`` takes an already validated hit list.
``aggregate`` returns JSON-serialisable metric values carrying exact fractions;
``passes`` returns one boolean per metric using the signed 1/50 drop policy.
No function in this module performs I/O or calls a service.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from fractions import Fraction
import hashlib
import json
import math
import re
from itertools import islice
from types import MappingProxyType


QUESTIONS_MAX_BYTES = 1024 * 1024
REFERENCE_MAX_BYTES = 64 * 1024
METRICS = ("hit_at_1", "hit_at_3", "hit_at_8", "mrr_at_10")
REQUEST = MappingProxyType({"mode": "hybrid", "rerank": "auto",
                           "max_chars": 2400, "deep": True,
                           "format": "json", "debug": True})
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z", re.ASCII)
_SHA256 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_REVISION = re.compile(r"[0-9a-f]{40,64}\Z", re.ASCII)
_LATTICE = 2520  # lcm(1, ..., 10), so every allowed reciprocal rank is exact.


class ContractError(ValueError):
    """The bounded public bundle does not satisfy the selftest contract."""


def _refuse():
    raise ContractError("Invalid selftest bundle.") from None


class Metrics(dict):
    """Immutable JSON-compatible metrics with exact fractions for comparison."""

    __slots__ = ("_exact",)

    def __init__(self, exact):
        dict.__init__(self, ((key, float(exact[key])) for key in METRICS))
        object.__setattr__(self, "_exact", MappingProxyType(dict(exact)))

    @property
    def exact(self):
        return self._exact

    def _immutable(self, *args, **kwargs):
        raise TypeError("Selftest metrics are immutable.")

    __setattr__ = __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = __ior__ = _immutable


def _freeze(value):
    if isinstance(value, Metrics):
        return value
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _plain(value):
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


def canonical_bytes(value):
    """Return the design's canonical settings JSON, with no trailing newline."""
    try:
        return json.dumps(_plain(value), sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError):
        _refuse()


@dataclass(frozen=True, slots=True)
class Bundle:
    questions: tuple
    reference: Mapping
    questions_sha256: str

    @property
    def bundle_id(self):
        return self.reference["bundle_id"]

    @property
    def release(self):
        return self.reference["release"]

    @property
    def fixture_only(self):
        return self.reference["fixture_only"]


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _refuse()
        result[key] = value
    return result


def _number(text):
    value = float(text)
    if not math.isfinite(value):
        _refuse()
    return value


def _constant(_):
    _refuse()


def _bounded_tree(value, depth=0, budget=None):
    if budget is None:
        budget = [20000]
    budget[0] -= 1
    if depth > 32 or budget[0] < 0:
        _refuse()
    if isinstance(value, str):
        # Decoded strings must be Unicode scalar values, never lone surrogates.
        if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
            _refuse()
    elif isinstance(value, dict):
        for key, item in value.items():
            _bounded_tree(key, depth + 1, budget)
            _bounded_tree(item, depth + 1, budget)
    elif isinstance(value, list):
        for item in value:
            _bounded_tree(item, depth + 1, budget)
    elif type(value) is float and not math.isfinite(value):
        _refuse()


def _decode(raw, limit):
    if type(raw) is not bytes or not 0 < len(raw) <= limit:
        _refuse()
    try:
        value = json.loads(raw.decode("utf-8", errors="strict"),
                           object_pairs_hook=_pairs, parse_float=_number,
                           parse_constant=_constant)
        _bounded_tree(value)
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        _refuse()
    return value


def _object(value, keys):
    if type(value) is not dict or set(value) != set(keys):
        _refuse()


def _string(value, maximum, minimum=1, pattern=None):
    if type(value) is not str or not minimum <= len(value) <= maximum:
        _refuse()
    if pattern is not None and pattern.fullmatch(value) is None:
        _refuse()


def _identifier(value):
    _string(value, 96, pattern=_IDENTIFIER)


def _integer(value, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        _refuse()


def _numeric(value, minimum, maximum=None):
    if type(value) not in (int, float):
        _refuse()
    if type(value) is float and not math.isfinite(value):
        _refuse()
    if value < minimum or (maximum is not None and value > maximum):
        _refuse()


def _questions(document):
    _object(document, ("schema_version", "bundle_id", "release", "fixture_only", "questions"))
    if type(document["schema_version"]) is not int or document["schema_version"] != 1:
        _refuse()
    _identifier(document["bundle_id"])
    _identifier(document["release"])
    if type(document["fixture_only"]) is not bool:
        _refuse()
    questions = document["questions"]
    if type(questions) is not list or not 1 <= len(questions) <= 64:
        _refuse()
    ids = set()
    for question in questions:
        _object(question, ("id", "text", "collection", "labels"))
        _identifier(question["id"])
        if question["id"] in ids:
            _refuse()
        ids.add(question["id"])
        _string(question["text"], 2000)
        if question["collection"] is not None:
            _string(question["collection"], 64)
        labels = question["labels"]
        if type(labels) is not list or not 1 <= len(labels) <= 32:
            _refuse()
        identities = set()
        for label in labels:
            _object(label, ("doc_key", "exact_span"))
            _string(label["doc_key"], 256)
            _string(label["exact_span"], 4000)
            identity = (label["doc_key"], label["exact_span"])
            if identity in identities:
                _refuse()
            identities.add(identity)


def _possible_sum(h1, h3, h8, count, units):
    # A bit at position s represents a possible reciprocal-rank sum in units
    # of 1/2520.  There are at most 64 questions, so the bitset stays bounded.
    possible = 1 << (h1 * _LATTICE)
    for amount, choices in ((h3 - h1, (1260, 840)),
                            (h8 - h3, (630, 504, 420, 360, 315)),
                            (count - h8, (280, 252, 0))):
        for _ in range(amount):
            updated = 0
            for shift in choices:
                updated |= possible << shift
            possible = updated
    return bool((possible >> units) & 1)


def _reference(document, questions, questions_hash):
    _object(document, ("schema_version", "bundle_id", "release", "fixture_only",
                       "questions_sha256", "question_count", "generation",
                       "corpus_fingerprint", "models", "request", "settings",
                       "settings_sha256", "reference", "numerators", "policy", "measurement"))
    if type(document["schema_version"]) is not int or document["schema_version"] != 1:
        _refuse()
    for key in ("bundle_id", "release"):
        _identifier(document[key])
        if document[key] != questions[key]:
            _refuse()
    if type(document["fixture_only"]) is not bool or document["fixture_only"] != questions["fixture_only"]:
        _refuse()
    _integer(document["question_count"], 1, 64)
    count = document["question_count"]
    if count != len(questions["questions"]):
        _refuse()
    for key in ("questions_sha256", "corpus_fingerprint", "settings_sha256"):
        _string(document[key], 64, 64, _SHA256)
    if document["questions_sha256"] != questions_hash:
        _refuse()
    _string(document["generation"], 128)
    _object(document["models"], ("embedding", "reranker"))
    for model in document["models"].values():
        _object(model, ("model", "revision"))
        _string(model["model"], 256)
        _string(model["revision"], 64, 40, _REVISION)
    _object(document["request"], REQUEST)
    for key, expected in REQUEST.items():
        value = document["request"][key]
        if type(value) is not type(expected) or value != expected:
            _refuse()
    if type(document["settings"]) is not dict:
        _refuse()
    if hashlib.sha256(canonical_bytes(document["settings"])).hexdigest() != document["settings_sha256"]:
        _refuse()
    _object(document["policy"], ("kind", "tolerance"))
    if document["policy"]["kind"] != "maximum-absolute-drop":
        _refuse()
    _numeric(document["policy"]["tolerance"], 0.02, 0.02)
    measurement = document["measurement"]
    _object(measurement, ("complete_runs", "identical_ranks", "hardware_class",
                          "gateway_version", "search_p50_ms", "search_p95_ms"))
    if type(measurement["complete_runs"]) is not int or measurement["complete_runs"] != 3:
        _refuse()
    if measurement["identical_ranks"] is not True:
        _refuse()
    _string(measurement["hardware_class"], 256)
    _string(measurement["gateway_version"], 64)
    _numeric(measurement["search_p50_ms"], 0)
    _numeric(measurement["search_p95_ms"], measurement["search_p50_ms"])
    numerators = document["numerators"]
    _object(numerators, ("hit_at_1", "hit_at_3", "hit_at_8", "reciprocal_rank_sum_at_10"))
    for key in METRICS[:3]:
        _integer(numerators[key], 0, count)
    h1, h3, h8 = (numerators[key] for key in METRICS[:3])
    if not h1 <= h3 <= h8:
        _refuse()
    total = numerators["reciprocal_rank_sum_at_10"]
    _numeric(total, 0, count)
    units = round(Fraction(str(total)) * _LATTICE)
    exact_sum = Fraction(units, _LATTICE)
    if float(exact_sum) != total or not _possible_sum(h1, h3, h8, count, units):
        _refuse()
    _object(document["reference"], METRICS)
    exact = {key: Fraction(numerators[key], count) for key in METRICS[:3]}
    exact["mrr_at_10"] = exact_sum / count
    for key in METRICS:
        _numeric(document["reference"][key], 0, 1)
        if float(exact[key]) != document["reference"][key]:
            _refuse()
    document["reference"] = Metrics(exact)


def load_bundle(questions_bytes, reference_bytes, *, fixture_harness=False):
    """Validate exact byte bindings and semantics; never provide trust itself."""
    if type(fixture_harness) is not bool:
        _refuse()
    questions = _decode(questions_bytes, QUESTIONS_MAX_BYTES)
    reference = _decode(reference_bytes, REFERENCE_MAX_BYTES)
    _questions(questions)
    digest = hashlib.sha256(questions_bytes).hexdigest()
    _reference(reference, questions, digest)
    if questions["fixture_only"] and not fixture_harness:
        raise ContractError("Fixture selftest bundles require the test harness.")
    return Bundle(_freeze(questions["questions"]), _freeze(reference), digest)


def first_rank(question, hits):
    """Return the first literal doc_key/span rank among the first ten hits.

    The runner validates transport, ordering, hit fields and health before this
    pure operation.  Aliases and additional hit fields confer no relevance.
    """
    labels = question["labels"]
    for rank, hit in enumerate(islice(hits, 10), 1):
        if any(hit["doc_key"] == label["doc_key"] and
               label["exact_span"] in hit["text"] for label in labels):
            return rank
    return None


def _ranks(ranks):
    values = tuple(islice(ranks, 65))
    if not 1 <= len(values) <= 64:
        _refuse()
    if any(rank is not None and (type(rank) is not int or not 1 <= rank <= 10)
           for rank in values):
        _refuse()
    return values


def aggregate_numerators(ranks):
    """Return the design's hit counts and exact-sum converted JSON number."""
    values = _ranks(ranks)
    result = {f"hit_at_{k}": sum(rank is not None and rank <= k for rank in values)
              for k in (1, 3, 8)}
    result["reciprocal_rank_sum_at_10"] = float(sum((Fraction(1, rank) for rank in values
                                                   if rank is not None), Fraction()))
    return result


def aggregate(ranks):
    """Return unrounded, exact-backed metrics for 1..64 completed questions."""
    values = _ranks(ranks)
    count = len(values)
    exact = {f"hit_at_{k}": Fraction(sum(rank is not None and rank <= k for rank in values), count)
             for k in (1, 3, 8)}
    exact["mrr_at_10"] = sum((Fraction(1, rank) for rank in values if rank is not None), Fraction()) / count
    return Metrics(exact)


def _metric_fraction(values, key):
    if isinstance(values, Metrics):
        return values.exact[key]
    value = values[key]
    if isinstance(value, Fraction):
        if not 0 <= value <= 1:
            _refuse()
        return value
    _numeric(value, 0, 1)
    return Fraction(str(value))


def passes(measured, reference_metrics, tolerance=0.02):
    """Compare independently with exact fractions and the fixed signed drop.

    Use ``aggregate`` and ``bundle.reference['reference']`` to retain rational
    values. Plain mappings are treated as their supplied decimal numbers.
    """
    if type(tolerance) not in (int, float, Fraction):
        _refuse()
    if type(tolerance) is float and not math.isfinite(tolerance):
        _refuse()
    if Fraction(str(tolerance)) != Fraction(1, 50):
        _refuse()
    if not isinstance(measured, Mapping) or not isinstance(reference_metrics, Mapping):
        _refuse()
    if set(measured) != set(METRICS) or set(reference_metrics) != set(METRICS):
        _refuse()
    return {key: _metric_fraction(measured, key) + Fraction(1, 50) >=
                 _metric_fraction(reference_metrics, key) for key in METRICS}
