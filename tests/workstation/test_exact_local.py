"""exact_local unit tests on synthetic exports (stdlib unittest), for src/workstation/node/exact_local.py in place:

  D32_EXACT_LIB=<the library> python3 -B -m unittest tests/workstation/test_exact_local.py

They need numpy and qdrant-client (tests/engine/requirements-dev.txt) and the C library built from
src/workstation/node/d32vec.c; without them they skip. On the Workstation the library is
/usr/local/d32/lib/libd32vec.so (src/workstation/node/build.sh builds it).

What they pin down: the three implementations of Qdrant's SSE order (C intrinsics, C scalar lanes, numpy emulation)
agree bit for bit with each other and with a one-operation-at-a-time scalar reference, including a tail dimension;
BLAS order does not; Qdrant's cosine_preprocess rules; the top-k tie rule; the filter, payload and argument
surface of query_points; the score chain; EXPORT.json checks; install().
"""
import hashlib
import json
import os
import sys
import tempfile
import types
import unittest
import uuid

NODE = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "src", "workstation",
                                    "node"))
sys.path.insert(0, NODE)
try:
    import numpy as np
    import exact_local as el
    from qdrant_client.http import models as rest
except ImportError as exc:
    np = el = rest = None
    MISSING = f"needs {exc.name} (pip install -r tests/engine/requirements-dev.txt)"
else:
    MISSING = None

LIB = os.environ.get("D32_EXACT_LIB", el.LIB_DEFAULT if el else "")
F = np.float32 if np else float
WINDOWS = os.name == "nt"   # Windows cannot remove a file numpy still has mapped: the clean-up leaves it
NEEDS_PACKAGES = unittest.skipIf(MISSING, MISSING or "")
NEEDS_LIB = unittest.skipUnless(el is not None and os.path.isfile(LIB), MISSING or (
    f"needs libd32vec built from src/workstation/node/d32vec.c (D32_EXACT_LIB, default {LIB})"))


def write_export(d, M, docs=None, flags=None, collection="col_test", shuffle_ids=False, bad_sha=False):
    """A deep32-dense/1 export of M; docs[r] = doc number or None; ids ascending unless shuffle_ids."""
    n, dim = M.shape
    os.makedirs(d, exist_ok=True)
    M.astype("<f4").tofile(os.path.join(d, "vectors.f32"))
    ids = sorted(uuid.UUID(int=(r * 7919 + 13) << 64 | r).bytes for r in range(n))
    if shuffle_ids:
        ids[0], ids[1] = ids[1], ids[0]
    recs = []
    for r in range(n):
        doc = docs[r] if docs is not None else r % 7
        fl = 1 | (2 if doc is not None else 0) | 4 | 8 | 16 | (32 if doc is not None else 0)
        if flags is not None:
            fl = flags[r]
        ch = hashlib.sha256(b"c%d" % r).digest() if fl & 1 else bytes(32)
        dc = hashlib.sha256(b"d%d" % doc).digest() if fl & 2 else bytes(32)
        recs.append(ids[r] + ch + dc + bytes([fl]) + bytes(15))
    with open(os.path.join(d, "points.bin"), "wb") as f:
        f.write(b"".join(recs))

    def sha(p):
        with open(os.path.join(d, p), "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()

    meta = {"format": el.FORMAT, "dtype": "float32", "byte_order": "little", "dim": dim, "points_count": n,
            "collection": collection, "generation": "test",
            "vectors": {"path": "dense/vectors.f32", "sha256": "0" * 64 if bad_sha else sha("vectors.f32")},
            "points": {"path": "dense/points.bin", "sha256": sha("points.bin")}}
    with open(os.path.join(d, "EXPORT.json"), "w") as f:
        json.dump(meta, f)
    return d


def doc_id(k):
    return "doc_" + hashlib.sha256(b"d%d" % k).hexdigest()


def chunk_id(r):
    return "chk_" + hashlib.sha256(b"c%d" % r).hexdigest()


def normalised(rng, n, dim):
    M = rng.standard_normal((n, dim), dtype=np.float32)
    M /= np.sqrt((M * M).sum(axis=1, dtype=np.float32))[:, None]
    return np.ascontiguousarray(M, np.float32)


def scalar_ref(x, y):
    """Qdrant's order, one f32 operation at a time (numpy f32 scalars)."""
    n = len(x)
    m = n - n % 16
    acc = [[F(0)] * 4 for _ in range(4)]
    for i in range(0, m, 16):
        for k in range(4):
            for l in range(4):
                j = i + 4 * k + l
                acc[k][l] = F(F(x[j] * y[j]) + acc[k][l])
    h = [F(F(a[0] + a[2]) + F(a[1] + a[3])) for a in acc]
    r = F(F(F(h[0] + h[1]) + h[2]) + h[3])
    for j in range(m, n):
        r = F(r + F(x[j] * y[j]))
    return r


def bits(a):
    return np.asarray(a, np.float32).view(np.uint32)


@NEEDS_LIB
class Kernels(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.k = el.CKernel(LIB)
        rng = np.random.default_rng(7)
        cls.M = normalised(rng, 3000, 1024)
        cls.Q = normalised(rng, 6, 1024)
        cls.M40 = normalised(rng, 500, 40)   # 2 SSE steps + an 8-float tail
        cls.Q40 = normalised(rng, 3, 40)

    def scan(self, M, q, lanes=False):
        out = np.empty(M.shape[0], np.float32)
        self.k.scan(M, np.ascontiguousarray(q), out, 0, M.shape[0], lanes=lanes)
        return out

    def test_sse_is_built(self):
        self.assertTrue(self.k.sse, "libd32vec was built without the SSE path")

    def test_three_implementations_bit_equal(self):
        for M, Q in ((self.M, self.Q), (self.M40, self.Q40)):
            for q in Q:
                c, lanes, emu = self.scan(M, q), self.scan(M, q, lanes=True), el.emulate_scores(M, q, chunk=256)
                self.assertEqual(int((bits(c) != bits(lanes)).sum()), 0)
                self.assertEqual(int((bits(c) != bits(emu)).sum()), 0)

    def test_scalar_reference(self):
        for M, q in ((self.M, self.Q[0]), (self.M40, self.Q40[0])):
            c = self.scan(M, q)
            for r in range(0, M.shape[0], max(1, M.shape[0] // 25)):
                self.assertEqual(bits(scalar_ref(M[r], q)), bits(c[r]), f"row {r}")

    def test_blas_order_differs(self):
        c = self.scan(self.M, self.Q[0])
        blas = (self.M @ self.Q[0]).astype(np.float32)
        self.assertGreater(int((bits(c) != bits(blas)).sum()), 0, "the order test would prove nothing")

    def test_threads_partition(self):
        out = np.empty(self.M.shape[0], np.float32)
        q = np.ascontiguousarray(self.Q[1])
        for a, b in ((0, 1000), (1000, 1001), (1001, 3000)):
            self.k.scan(self.M, q, out, a, b)
        self.assertEqual(int((bits(out) != bits(self.scan(self.M, q))).sum()), 0)


@NEEDS_LIB
class Preprocess(unittest.TestCase):
    def test_rules(self):
        k = el.CKernel(LIB)
        rng = np.random.default_rng(3)
        q = normalised(rng, 1, 1024)[0]
        self.assertIs(el.qdrant_cosine_preprocess(q, k.dot), q)          # |len2 - 1| <= 1e-6: unchanged
        z = np.zeros(1024, np.float32)
        self.assertIs(el.qdrant_cosine_preprocess(z, k.dot), z)          # len2 < f32::EPSILON: unchanged
        q2 = (q * F(3)).astype(np.float32)
        got = el.qdrant_cosine_preprocess(q2, k.dot)
        len2 = k.dot(q2, q2)
        want = np.array([F(x) / np.sqrt(len2, dtype=np.float32) for x in q2], np.float32)
        self.assertEqual(int((bits(got) != bits(want)).sum()), 0)
        self.assertEqual(got.dtype, np.float32)
        self.assertEqual(bits(el.qdrant_cosine_preprocess(q2, el.numpy_dot)).tobytes(), bits(got).tobytes())


@NEEDS_PACKAGES
class TopRows(unittest.TestCase):
    def test_ties_by_row_and_cut_inside_a_tie(self):
        s = np.array([0.5, 0.9, 0.7, 0.9, 0.7, 0.7, 0.1], np.float32)
        rows, vals = el.top_rows(s, None, 4)
        self.assertEqual(rows.tolist(), [1, 3, 2, 4])
        rows, _ = el.top_rows(s, None, 10)
        self.assertEqual(rows.tolist(), [1, 3, 2, 4, 5, 0, 6])
        mask = np.array([1, 0, 1, 1, 1, 0, 1], bool)
        rows, _ = el.top_rows(s, mask, 3)
        self.assertEqual(rows.tolist(), [3, 2, 4])
        self.assertEqual(el.top_rows(s, np.zeros(7, bool), 3)[0].tolist(), [])
        self.assertEqual(el.top_rows(s, None, 0)[0].tolist(), [])


@NEEDS_LIB
class Client(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=WINDOWS)
        rng = np.random.default_rng(11)
        cls.M = normalised(rng, 2000, 1024)
        cls.M[17] = cls.M[5]                       # an exact duplicate: a tie group
        docs = [r % 7 for r in range(2000)]
        docs[9] = None                             # a point without doc_id
        cls.dir = write_export(os.path.join(cls.tmp.name, "dense"), cls.M, docs=docs)
        cls.c = el.ExactClient(cls.dir, lib=LIB, threads=4)
        cls.c1 = el.ExactClient(cls.dir, lib=LIB, threads=1)
        cls.np = el.ExactClient(cls.dir, kernel="numpy")

    @classmethod
    def tearDownClass(cls):
        for c in (cls.c, cls.c1, cls.np):
            c.close()
        cls.tmp.cleanup()

    def q(self, client, row, **kw):
        kw.setdefault("limit", 50)
        kw.setdefault("with_payload", ["chunk_id"])
        kw.setdefault("with_vectors", False)
        return client.query_points(collection_name="col_test", query=[float(x) for x in self.M[row]], **kw)

    def test_shape_and_self_hit(self):
        res = self.q(self.c, 42)
        self.assertIsInstance(res, rest.QueryResponse)
        self.assertEqual(len(res.points), 50)
        p = res.points[0]
        self.assertIsInstance(p, rest.ScoredPoint)
        self.assertEqual(p.payload, {"chunk_id": chunk_id(42)})
        self.assertEqual(str(uuid.UUID(p.id)), p.id)
        scores = [x.score for x in res.points]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_tie_group_in_row_order(self):
        pts = self.q(self.c, 5).points
        self.assertEqual([x.payload["chunk_id"] for x in pts[:2]], [chunk_id(5), chunk_id(17)])
        self.assertEqual(pts[0].score, pts[1].score)

    def test_kernels_and_threads_agree(self):
        for row in (0, 5, 999, 1999):
            a, b, e = self.q(self.c, row), self.q(self.c1, row), self.q(self.np, row)
            self.assertEqual(a.model_dump(), b.model_dump())
            self.assertEqual(a.model_dump(), e.model_dump())

    def test_score_chain(self):
        q = self.c.prepare_query(self.M[3])
        s = self.c.scores(q)
        rows, vals = el.top_rows(s, None, 50)
        got = [p.score for p in self.q(self.c, 3).points]
        self.assertEqual(got, [float(str(v)) for v in vals])
        self.assertTrue(all(float(str(v)) == float(repr(float(str(v)))) for v in vals))

    def test_probe_shape(self):
        res = self.q(self.c, 7, limit=1, with_payload=False)
        self.assertEqual(len(res.points), 1)
        self.assertIsNone(res.points[0].payload)

    def test_filters(self):
        only = rest.Filter(must=[rest.FieldCondition(key="doc_id", match=rest.MatchAny(any=[doc_id(3), "doc_x"]))])
        pts = self.q(self.c, 10, query_filter=only, with_payload=["chunk_id", "doc_id"]).points
        self.assertEqual({p.payload["doc_id"] for p in pts}, {doc_id(3)})
        self.assertEqual(len(pts), 50)
        value = rest.Filter(must=rest.FieldCondition(key="doc_id", match=rest.MatchValue(value=doc_id(3))))
        self.assertEqual(self.q(self.c, 10, query_filter=value).model_dump(),
                         self.q(self.c, 10, query_filter=only).model_dump())
        excl = rest.Filter(must_not=[rest.FieldCondition(key="doc_id", match=rest.MatchAny(any=[doc_id(2)]))])
        pts = self.q(self.c, 9, query_filter=excl, limit=2000, with_payload=["chunk_id", "doc_id"]).points
        self.assertNotIn(doc_id(2), {p.payload.get("doc_id") for p in pts})
        self.assertIn(chunk_id(9), {p.payload["chunk_id"] for p in pts})        # no doc_id: must_not keeps it
        self.assertEqual([p.payload for p in pts if p.payload["chunk_id"] == chunk_id(9)], [{"chunk_id": chunk_id(9)}])
        pts = self.q(self.c, 9, query_filter=only, limit=2000).points
        self.assertNotIn(chunk_id(9), {p.payload["chunk_id"] for p in pts})     # ... and must drops it
        both = rest.Filter(must=only.must, must_not=excl.must_not)
        self.assertEqual(self.q(self.c, 10, query_filter=both).model_dump(),
                         self.q(self.c, 10, query_filter=only).model_dump())
        none = rest.Filter(must=[rest.FieldCondition(key="doc_id", match=rest.MatchAny(any=["doc_nothing"]))])
        self.assertEqual(self.q(self.c, 10, query_filter=none).points, [])

    def test_refusals(self):
        with self.assertRaises(ValueError):
            self.c.query_points(collection_name="other", query=[0.0] * 1024, limit=1, with_payload=False)
        with self.assertRaises(ValueError):
            self.c.query_points(collection_name="col_test", query=[0.1] * 3, limit=1, with_payload=False)
        with self.assertRaises(NotImplementedError):
            self.c.query_points(collection_name="col_test", query="text", limit=1, with_payload=False)
        for kw in ({"with_payload": True}, {"with_payload": ["text"]}, {"score_threshold": 0.5},
                   {"offset": 5}, {"using": "v"}, {"with_vectors": True}):
            with self.subTest(kw=kw), self.assertRaises(NotImplementedError):
                self.q(self.c, 1, **kw)
        should = rest.Filter(should=[rest.FieldCondition(key="doc_id", match=rest.MatchAny(any=[doc_id(1)]))])
        other = rest.Filter(must=[rest.FieldCondition(key="doc_type", match=rest.MatchValue(value="rfc"))])
        for flt in (should, other):
            with self.subTest(flt=flt), self.assertRaises(NotImplementedError):
                self.q(self.c, 1, query_filter=flt)
        self.q(self.c, 1, search_params=rest.SearchParams(exact=True), consistency=None, timeout=5)  # accepted
        with self.assertRaises(AttributeError):
            self.c.upsert  # noqa: B018 (the attribute access is what the test checks)


@NEEDS_LIB
class ExportChecks(unittest.TestCase):
    def test_bad_sha_and_order(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=WINDOWS) as t:
            M = normalised(np.random.default_rng(1), 20, 1024)
            with self.assertRaises(ValueError):
                el.ExactClient(write_export(os.path.join(t, "a"), M, bad_sha=True), lib=LIB)
            with self.assertRaises(ValueError):
                el.ExactClient(write_export(os.path.join(t, "b"), M, shuffle_ids=True), lib=LIB)
            el.ExactClient(write_export(os.path.join(t, "c"), M), lib=LIB).close()


@NEEDS_LIB
class Install(unittest.TestCase):
    def test_patch(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=WINDOWS) as t:
            d = write_export(os.path.join(t, "dense"), normalised(np.random.default_rng(2), 30, 1024))

            def original(config):
                return ("qdrant", config.qdrant_url)

            a = types.SimpleNamespace(make_qdrant_client=original)
            b = types.SimpleNamespace(make_qdrant_client=original)
            os.environ["D32_EXACT_LIB"] = LIB
            el.install(a, b)
            self.assertIs(a.make_qdrant_client, b.make_qdrant_client)
            cfg = types.SimpleNamespace(qdrant_url="file:" + d, collection="col_test")
            c1, c2 = a.make_qdrant_client(cfg), b.make_qdrant_client(cfg)
            self.assertIsInstance(c1, el.ExactClient)
            self.assertIs(c1, c2)
            self.assertEqual(a.make_qdrant_client(types.SimpleNamespace(qdrant_url="http://127.0.0.1:6333")),
                             ("qdrant", "http://127.0.0.1:6333"))
            el.install(a, b)  # twice: still wraps the original, not itself
            self.assertIs(a.make_qdrant_client.__wrapped__, original)
            c1.close()
            el._clients.clear()


if __name__ == "__main__":
    if "--lib" in sys.argv:
        i = sys.argv.index("--lib")
        LIB = sys.argv[i + 1]
        del sys.argv[i:i + 2]
    unittest.main(verbosity=2)
