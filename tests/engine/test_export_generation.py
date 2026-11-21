"""scripts/export_generation.py on a small synthetic root (rag_core.db's schema with an opt-in FTS split, the
structure tables, kb.sqlite, sources with their manifest, the overlay, a build report, retrieval traces, an empty
WAL and an -shm as a served root has them) and a stand-in Qdrant over HTTP that serves the vectors as Qdrant does
(shortest float32 text), plus three crafted values whose double lies exactly on a float32 midpoint. The stand-in
for age copies the tar through, so the tests read what export wrote. Checks: the SQLite files and every other root
file come back byte for byte; the vectors in point-id order and bit-exact; points.bin's records and flags; the
manifest's hashes; refusals (fingerprint, generation, not served, a WAL holding frames) before anything is
written; no write to the source root; Qdrant asked only GET /, GET the collection and POST scroll; unpack's path
guard; clean. Standard library only (rag_core where it is importable, to prove the definitions agree), so it runs
on a plain Windows Python as well as in the engine's own test run."""
import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import contextlib
import gzip
import hashlib
import importlib.util
import io
import json
import math
import os
import random
import shutil
import sqlite3
import struct
import subprocess
import sys
import tarfile
import tempfile
import threading
import unittest
import uuid
from array import array
from fractions import Fraction
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = engine_target.REPO
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from rag_core.db import SCHEMA, split_optin_fts  # noqa: E402  (stdlib-only module)

GENERATION = "t1-20261105"
COLLECTION = f"rag_dense_{GENERATION}"
DIM = 8
PAGE = 4
RECIPIENT = "age1" + "q" * 58
COPY = [sys.executable, "-I", "-c", "import shutil, sys; shutil.copyfileobj(sys.stdin.buffer, sys.stdout.buffer)"]
STRUCTURE_DDL = """
CREATE TABLE doc_sources (doc_id TEXT PRIMARY KEY, doc_key TEXT NOT NULL UNIQUE, doc_type TEXT NOT NULL,
    rel_path TEXT NOT NULL, sha256 TEXT NOT NULL, n_lines INTEGER NOT NULL, title TEXT, release TEXT, platform TEXT,
    source_url TEXT, structure_version TEXT NOT NULL, indexed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE doc_sections (section_id TEXT PRIMARY KEY, doc_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
    level INTEGER NOT NULL, anchor TEXT NOT NULL, heading TEXT, parent_section_id TEXT, line_start INTEGER NOT NULL,
    line_end INTEGER NOT NULL, n_chars INTEGER NOT NULL, UNIQUE (doc_id, ordinal));
CREATE TABLE chunk_spans (chunk_id TEXT PRIMARY KEY, doc_id TEXT NOT NULL, line_start INTEGER, line_end INTEGER,
    section_id TEXT, covered_json TEXT NOT NULL DEFAULT '[]');
"""


def load_module():
    spec = importlib.util.spec_from_file_location("export_generation", REPO / "scripts" / "export_generation.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


eg = load_module()


# ---------------------------------------------------------------- exact float32 references


def f32_bits(x: float) -> int:
    return struct.unpack("<I", struct.pack("<f", x))[0]


def f32_from_bits(b: int) -> float:
    return struct.unpack("<f", struct.pack("<I", b))[0]


def ref_f32_bits(text: str) -> int:
    """The float32 nearest to the decimal text, ties to the even significand: by exact comparison of the three
    float32 around the double's cast (independent of exact_f32's midpoint method)."""
    exact = Fraction(text)
    cast = f32_bits(float(text))
    best = None
    for b in (cast - 1, cast, cast + 1):
        if not 0 <= b <= 0xFFFFFFFF:
            continue
        v = f32_from_bits(b)
        if math.isnan(v) or math.isinf(v):
            continue
        key = (abs(exact - Fraction(v)), b & 1, b)
        best = key if best is None or key < best else best
    return best[2]


def shortest_f32_text(v: float) -> str:
    """Qdrant writes a float32 with serde_json's shortest round-trip text; this finds it the slow way."""
    for digits in range(1, 10):
        text = f"{v:.{digits}g}"
        if ref_f32_bits(text) == f32_bits(v):
            return text
    raise AssertionError(v)


def exact_decimal(fr: Fraction) -> str:
    """The terminating decimal expansion of a fraction whose denominator is 2^a 5^b."""
    den, a, b = fr.denominator, 0, 0
    while den % 2 == 0:
        den, a = den // 2, a + 1
    while den % 5 == 0:
        den, b = den // 5, b + 1
    assert den == 1
    k = max(a, b)
    digits = str(abs(fr.numerator) * 10 ** k // fr.denominator).rjust(k + 1, "0")
    text = digits[:-k] + "." + digits[-k:] if k else digits
    return ("-" if fr < 0 else "") + text


def midpoint_texts() -> list[tuple[str, int, int]]:
    """(text, the right float32 bits, the bits the plain double-to-float32 cast gives) for three texts whose
    correctly rounded double is exactly a float32 midpoint: just above one (lower neighbour even: the cast picks
    it, wrongly), exactly on one (the cast's tie to even is right), just below one (lower neighbour odd: the cast
    picks the even upper one, wrongly)."""
    out = []
    for base, offset_sign, want_odd_low in ((0.0312345, 1, False), (0.0456789, 0, False), (-0.0234567, -1, True)):
        low = f32_bits(abs(base))
        if bool(low & 1) != want_odd_low:
            low += 1
        lo, hi = f32_from_bits(low), f32_from_bits(low + 1)
        mid = (Fraction(lo) + Fraction(hi)) / 2
        scale = Fraction(1, 10 ** (len(exact_decimal(mid)) + 10))
        value = mid + offset_sign * scale
        right = low + 1 if offset_sign > 0 else low
        sign = -1 if base < 0 else 1
        text = exact_decimal(sign * value)
        assert Fraction(float(text)) == sign * mid, "the double must land on the midpoint"
        cast = f32_bits(float(text))
        right_bits = right | (0x80000000 if sign < 0 else 0)
        assert ref_f32_bits(text) == right_bits
        out.append((text, right_bits, cast))
    return out


# ---------------------------------------------------------------- the stand-in Qdrant


class FakeQdrant:
    """GET /, GET /collections/NAME, POST /collections/NAME/points/scroll (ascending ids, next_page_offset = the
    next id), every request logged; anything else answers 405 and is logged too."""

    def __init__(self, points: list[dict], dim: int) -> None:
        self.points = sorted(points, key=lambda p: uuid.UUID(p["id"]).int)
        self.dim = dim
        self.log: list[tuple[str, str]] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, code: int, body: bytes) -> None:
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                fake.log.append(("GET", self.path))
                if self.path == "/":
                    self._send(200, b'{"title":"qdrant - vector search engine","version":"1.19.1","commit":"x"}')
                elif self.path == f"/collections/{COLLECTION}":
                    info = {"result": {"status": "green", "optimizer_status": "ok", "points_count": len(fake.points),
                                       "segments_count": 1,
                                       "config": {"params": {"vectors": {"size": fake.dim, "distance": "Cosine"}},
                                                  "quantization_config": None}},
                            "status": "ok", "time": 0.0001}
                    self._send(200, json.dumps(info).encode())
                else:
                    self._send(404, b'{"status":{"error":"not found"}}')

            def do_POST(self):
                fake.log.append(("POST", self.path))
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path != f"/collections/{COLLECTION}/points/scroll":
                    self._send(405, b"{}")
                    return
                ids = [p["id"] for p in fake.points]
                start = ids.index(body["offset"]) if body.get("offset") is not None else 0
                chunk = fake.points[start:start + body["limit"]]
                nxt = ids[start + body["limit"]] if start + body["limit"] < len(ids) else None
                parts = []
                for p in chunk:
                    payload = {k: p["payload"][k] for k in body["with_payload"] if k in p["payload"]}
                    parts.append('{"id":"%s","version":3,"payload":%s,"vector":[%s]}'
                                 % (p["id"], json.dumps(payload), ",".join(p["texts"])))
                text = '{"result":{"points":[%s],"next_page_offset":%s},"status":"ok","time":0.000123}' % (
                    ",".join(parts), json.dumps(nxt))
                self._send(200, text.encode())

            def do_PUT(self):
                fake.log.append(("PUT", self.path))
                self._send(405, b"{}")

            def do_DELETE(self):
                fake.log.append(("DELETE", self.path))
                self._send(405, b"{}")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


# ---------------------------------------------------------------- the synthetic root


def build_root(base: Path, qdrant_url: str | None) -> dict:
    """A served-looking root under base/rag-data/GENERATION and the points of its collection."""
    root = base / "rag-data" / GENERATION
    for d in ("sqlite", "kb", "sources/rfc", "sources/system", "config", "logs/retrieval", "inbox", "pylate"):
        (root / d).mkdir(parents=True, exist_ok=True)
    rng = random.Random(20261105)
    docs = []
    for i, (doc_type, key) in enumerate((("rfc", "rfc1001"), ("rfc", "rfc2002"), ("system", "sys-notes"))):
        text = "".join(f"line {n} of {key}: OSPF area {rng.randint(0, 99)}\n" for n in range(20 + i))
        rel = f"{doc_type}/{key}.txt"
        (root / "sources" / rel).write_bytes(text.encode())
        sha = hashlib.sha256(text.encode()).hexdigest()
        docs.append({"doc_key": key, "title": key.upper(), "doc_type": doc_type, "path": rel, "sha256": sha,
                     "bytes": len(text), "doc_id": f"doc_{sha}", "text": text})
    (root / "sources" / "manifest.json").write_text(
        json.dumps([{k: d[k] for k in ("doc_key", "title", "doc_type", "path", "sha256", "bytes")} for d in docs]))

    db = root / "sqlite" / "rag.db"
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    conn.executescript(STRUCTURE_DDL)
    chunks = []
    for d in docs:
        conn.execute("INSERT INTO documents (doc_id, sha256, source_name, original_filename, status) "
                     "VALUES (?, ?, ?, ?, 'chunked')", (d["doc_id"], d["sha256"], d["doc_key"], d["path"]))
        conn.execute("INSERT INTO doc_sources (doc_id, doc_key, doc_type, rel_path, sha256, n_lines, "
                     "structure_version) VALUES (?, ?, ?, ?, ?, ?, 's1')",
                     (d["doc_id"], d["doc_key"], d["doc_type"], d["path"], d["sha256"], d["text"].count("\n")))
        for ordinal in range(4):
            text = f"chunk {ordinal} of {d['doc_key']} about OSPF NSSA and BGP"
            cid = "chk_" + hashlib.sha256(f"{d['sha256']}\0{ordinal}\0{text}".encode()).hexdigest()
            conn.execute("INSERT INTO chunks (chunk_id, chunk_sha256, doc_id, ordinal, text, embedding_text, "
                         "token_count, char_count) VALUES (?, ?, ?, ?, ?, ?, 9, ?)",
                         (cid, hashlib.sha256(text.encode()).hexdigest(), d["doc_id"], ordinal, text, text,
                          len(text)))
            conn.execute("INSERT INTO doc_sections (section_id, doc_id, ordinal, level, anchor, line_start, "
                         "line_end, n_chars) VALUES (?, ?, ?, 1, 'a', 1, 2, 10)",
                         (f"sec_{cid[4:20]}", d["doc_id"], ordinal))
            conn.execute("INSERT INTO chunk_spans (chunk_id, doc_id) VALUES (?, ?)", (cid, d["doc_id"]))
            chunks.append((cid, d["doc_id"]))
    # one chunk not dense-indexed; every other chunk is
    for n, (cid, _) in enumerate(chunks):
        conn.execute("INSERT INTO index_state (chunk_id, lexical_indexed, dense_indexed) VALUES (?, 1, ?)",
                     (cid, 0 if n == 5 else 1))
    conn.commit()
    split_optin_fts(conn, [docs[2]["doc_id"]])
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    for leftover in ("rag.db-wal", "rag.db-shm"):
        with contextlib.suppress(FileNotFoundError):
            (root / "sqlite" / leftover).unlink()
    # as a served root has them: an empty WAL and SQLite's shared-memory index
    (root / "sqlite" / "rag.db-wal").write_bytes(b"")
    (root / "sqlite" / "rag.db-shm").write_bytes(bytes(range(256)) * 128)

    kb = sqlite3.connect(root / "kb" / "kb.sqlite")
    kb.execute("CREATE TABLE cli (id INTEGER PRIMARY KEY, command TEXT)")
    kb.executemany("INSERT INTO cli (command) VALUES (?)", [(f"show ip ospf {n}",) for n in range(50)])
    kb.execute("CREATE TABLE glossary (term TEXT)")
    kb.execute("INSERT INTO glossary VALUES ('LSA')")
    kb.commit()
    kb.close()

    fingerprint = hashlib.sha256("\n".join(sorted(d["sha256"] for d in docs)).encode()).hexdigest()[:16]
    (root / "config" / "serving.json").write_text(json.dumps({"generation": GENERATION, "max_concurrent_searches": 2}))
    overlay = {"collection": COLLECTION, "vector_size": DIM}
    if qdrant_url:
        overlay["qdrant_url"] = qdrant_url
    (root / "config" / "dense.json").write_text(json.dumps(overlay))
    (root / "config" / "retrieval.json").write_text(json.dumps({"fold_identical": True}))
    (root / "BUILD_REPORT.json").write_text(json.dumps({"generation": GENERATION, "derived": True,
                                                        "corpus": {"fingerprint": fingerprint}}))
    (root / "logs" / "retrieval" / "trace-1.json").write_text('{"query": "not exported"}')
    if hasattr(os, "symlink"):
        with contextlib.suppress(OSError):
            os.symlink("sources/manifest.json", root / "manifest-link.json")

    # the collection: a point per chunk, one stray point whose chunk rag.db lacks, three midpoint values
    points = []
    for cid, did in chunks + [("chk_" + "e" * 64, docs[0]["doc_id"])]:
        values = [f32_from_bits(f32_bits(rng.uniform(-0.5, 0.5))) for _ in range(DIM)]
        texts = [shortest_f32_text(v) for v in values]
        points.append({"id": str(uuid.uuid5(uuid.NAMESPACE_URL, eg.POINT_NAME_PREFIX + cid)),
                       "payload": {"chunk_id": cid, "doc_id": did}, "texts": texts})
    crafted = midpoint_texts()
    for n, (text, _, _) in enumerate(crafted):
        points[2 + n]["texts"][3] = text
    return {"root": root, "fingerprint": fingerprint, "docs": docs, "chunks": chunks, "points": points,
            "crafted": crafted}


def tree_state(root: Path) -> dict:
    """Every path under root with its kind, size, mtime and content hash."""
    state = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            path = Path(dirpath) / name
            st = os.lstat(path)
            if os.path.islink(path):
                state[str(path)] = ("link", os.readlink(path))
            elif path.is_dir():
                state[str(path)] = ("dir",)
            else:
                state[str(path)] = ("file", st.st_size, st.st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest())
    return state


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ExportGenerationTest(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="export-gen-"))
        self.fake = None
        made = build_root(self.tmp, None)
        self.fake = FakeQdrant(made["points"], DIM)
        (made["root"] / "config" / "dense.json").write_text(
            json.dumps({"collection": COLLECTION, "vector_size": DIM, "qdrant_url": self.fake.url}))
        self.made = made
        self.root = made["root"]
        self.out = self.tmp / "out"
        self.env = {"RAG_ROOT": str(self.root)}

    def tearDown(self):
        if self.fake:
            self.fake.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def args(self, mode: str = "export", **over) -> list[str]:
        values = {"root": str(self.root), "expect-generation": GENERATION,
                  "expect-fingerprint": self.made["fingerprint"], "expect-docs": "3", "expect-chunks": "12",
                  "expect-points": str(len(self.made["points"])), "recipient": RECIPIENT, "out-dir": str(self.out),
                  "page-size": str(PAGE), "pause-ms": "0"}
        values.update({k.replace("_", "-"): v for k, v in over.items()})
        return [mode] + [f"--{k}={v}" for k, v in values.items() if v is not None]

    def export(self, **over) -> int:
        with contextlib.redirect_stdout(io.StringIO()) as out:
            rc = eg.main(self.args(**over), env=self.env, encrypt_argv=COPY)
        self.last_output = out.getvalue()
        return rc

    def exported(self) -> tuple[Path, dict]:
        """Run export, unpack its output into vm/; return the generation directory and the sidecar."""
        before = tree_state(self.root)
        self.assertEqual(self.export(), 0, self.last_output if hasattr(self, "last_output") else "")
        self.assertEqual(tree_state(self.root), before, "export wrote into the source root")
        side = json.loads((self.out / f"export-{self.made['fingerprint']}.tar.age.json").read_text())
        blob = self.out / f"export-{self.made['fingerprint']}.tar.age"
        vm = self.tmp / "vm"
        with contextlib.redirect_stdout(io.StringIO()) as out:
            rc = eg.main(["unpack", f"--into={vm}", f"--input={blob}", f"--expect-generation={GENERATION}",
                          f"--expect-tar-sha256={side['tar_sha256']}"])
        self.assertEqual(rc, 0, out.getvalue())
        return vm / GENERATION, side

    # ------------------------------------------------------------ the round trip

    def test_round_trip_byte_identical(self):
        gen, side = self.exported()
        for rel in ("sqlite/rag.db", "kb/kb.sqlite", "sources/rfc/rfc1001.txt", "sources/manifest.json",
                    "config/dense.json", "BUILD_REPORT.json"):
            self.assertEqual((gen / "root" / rel).read_bytes(), (self.root / rel).read_bytes(), rel)
        self.assertTrue((gen / "root" / "inbox").is_dir() and (gen / "root" / "pylate").is_dir())
        for left_out in ("logs", "sqlite/rag.db-wal", "sqlite/rag.db-shm"):
            self.assertFalse((gen / "root" / left_out).exists(), left_out)
        manifest = json.loads((gen / "MANIFEST.json").read_text())
        excluded = {e["path"]: e for e in manifest["excluded"]}
        self.assertEqual(excluded["logs/"]["files"], 1)
        self.assertEqual(excluded["sqlite/rag.db-wal"]["bytes"], 0)
        self.assertEqual(excluded["sqlite/rag.db-shm"]["bytes"], 256 * 128)
        self.assertEqual(manifest["generation"], GENERATION)
        self.assertEqual(manifest["corpus_fingerprint"], self.made["fingerprint"])
        self.assertTrue(manifest["served"])
        self.assertEqual(manifest["counts"]["rag_db"]["n_docs"], 3)
        self.assertEqual(manifest["counts"]["rag_db"]["bm25_tables"], {"chunk_fts": 8, "chunk_fts_optin": 4})
        self.assertEqual(manifest["counts"]["kb"], {"cli": 50, "glossary": 1})
        self.assertIn("sqlite_compile_options", manifest["versions"])
        self.assertEqual(sorted(manifest["exporter"]["repo_config"]),
                         sorted(p.name for p in (REPO / "config").glob("*.json")))
        with contextlib.redirect_stdout(io.StringIO()) as out:
            rc = eg.main(["verify", f"--dir={gen}", "--parser=python", f"--expect-fingerprint={self.made['fingerprint']}",
                          "--expect-docs=3", "--expect-chunks=12", f"--expect-points={len(self.made['points'])}"])
        self.assertEqual(rc, 0, out.getvalue())
        self.assertIn("FTS5 integrity-check chunk_fts_optin: ok", out.getvalue())
        self.assertIn("the same file that exported", out.getvalue())

    def test_vectors_in_id_order_and_bit_exact(self):
        gen, _ = self.exported()
        ordered = sorted(self.made["points"], key=lambda p: uuid.UUID(p["id"]).int)
        want = b"".join(struct.pack("<I", ref_f32_bits(t)) for p in ordered for t in p["texts"])
        got = (gen / "dense" / "vectors.f32").read_bytes()
        self.assertEqual(len(got), len(ordered) * DIM * 4)
        self.assertEqual(got, want)
        naive = b"".join(struct.pack("<f", float(t)) for p in ordered for t in p["texts"])
        self.assertNotEqual(naive, want, "the crafted midpoints must defeat a plain cast")
        export = json.loads((gen / "dense" / "EXPORT.json").read_text())
        self.assertEqual(export["parse"]["midpoints"], 3)
        self.assertEqual(export["parse"]["corrected"], 2)
        self.assertEqual(export["points_count"], len(ordered))
        self.assertEqual(len(export["scroll"]["pages"]), math.ceil(len(ordered) / PAGE))
        # each page as the stand-in sent it
        first = gzip.decompress((gen / export["scroll"]["pages"][0]["path"]).read_bytes())
        self.assertIn(b'"version":3', first)
        # points.bin, record by record
        blob = (gen / "dense" / "points.bin").read_bytes()
        self.assertEqual(len(blob), len(ordered) * 96)
        stray = "chk_" + "e" * 64
        not_indexed = self.made["chunks"][5][0]
        for n, p in enumerate(ordered):
            uid, cbytes, dbytes, flags = struct.unpack("<16s32s32sB15x", blob[n * 96:(n + 1) * 96])
            self.assertEqual(str(uuid.UUID(bytes=uid)), p["id"])
            self.assertEqual("chk_" + cbytes.hex(), p["payload"]["chunk_id"])
            self.assertEqual("doc_" + dbytes.hex(), p["payload"]["doc_id"])
            cid = p["payload"]["chunk_id"]
            want_flags = 1 | 2 | 16
            if cid != stray:
                want_flags |= 4 | 32 | (0 if cid == not_indexed else 8)
            self.assertEqual(flags, want_flags, cid)

    def test_manifest_hashes(self):
        gen, side = self.exported()
        blob = self.out / f"export-{self.made['fingerprint']}.tar.age"
        self.assertEqual(side["age_sha256"], sha(blob))
        self.assertEqual(side["tar_sha256"], sha(blob))         # the stand-in for age copies the tar through
        self.assertEqual(side["age_bytes"], blob.stat().st_size)
        manifest_bytes = (gen / "MANIFEST.json").read_bytes()
        self.assertEqual(side["manifest_sha256"], hashlib.sha256(manifest_bytes).hexdigest())
        manifest = json.loads(manifest_bytes)
        files = [e for e in manifest["files"] if e["kind"] == "file"]
        self.assertGreater(len(files), 20)
        for e in files:
            self.assertEqual(sha(gen / e["path"]), e["sha256"], e["path"])
            self.assertEqual((gen / e["path"]).stat().st_size, e["size"], e["path"])
            if e["path"].startswith("root/"):
                self.assertEqual(sha(self.root / e["path"][5:]), e["sha256"], e["path"])
        with tarfile.open(blob) as tar:
            names = tar.getnames()
        self.assertEqual(names[-1], f"{GENERATION}/MANIFEST.json")
        self.assertEqual(side["members"], len(names))
        self.assertTrue(all(n == GENERATION or n.startswith(GENERATION + "/") for n in names))

    # ------------------------------------------------------------ refusals and read-only

    def assertRefusedClean(self, rc: int) -> None:
        self.assertEqual(rc, 2)
        self.assertFalse(self.out.exists(), "a refused export must not create its out-dir")

    def test_refuses_wrong_fingerprint(self):
        before = tree_state(self.root)
        self.assertRefusedClean(self.export(expect_fingerprint="0123456789abcdef"))
        self.assertEqual(tree_state(self.root), before)

    def test_refuses_wrong_generation_and_unserved_root(self):
        self.assertRefusedClean(self.export(expect_generation="t2-20261105"))
        self.env = {"RAG_ROOT": str(self.tmp / "rag-data" / "other")}
        self.assertRefusedClean(self.export())

    def test_refuses_a_wal_with_frames(self):
        (self.root / "sqlite" / "rag.db-wal").write_bytes(b"\x37\x7f\x06\x82" + bytes(28))
        self.assertRefusedClean(self.export())
        with contextlib.redirect_stdout(io.StringIO()) as out:
            rc = eg.main(self.args("check"), env=self.env, encrypt_argv=COPY)
        self.assertEqual(rc, 2)
        self.assertIn("the WAL holds 32 bytes", out.getvalue())

    def test_refuses_out_dir_inside_root_or_repo(self):
        for inside in (self.root / "exports", REPO / "exports-test"):
            self.out = inside
            self.assertRefusedClean(self.export())

    def test_check_is_ready_and_writes_nothing(self):
        before = tree_state(self.root)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            rc = eg.main(self.args("check"), env=self.env, encrypt_argv=COPY)
        self.assertEqual(rc, 0, out.getvalue())
        self.assertIn("check: READY", out.getvalue())
        self.assertFalse(self.out.exists())
        self.assertEqual(tree_state(self.root), before)

    def test_check_needs_a_working_age(self):
        # no stand-in: the real age must be installed and must accept the recipient (this one has a bad checksum)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            rc = eg.main(self.args("check"), env=self.env)
        self.assertEqual(rc, 2, out.getvalue())
        self.assertRegex(out.getvalue(), r"age is not installed|age refuses the recipient")
        self.assertFalse(self.out.exists())

    def test_qdrant_reads_only(self):
        self.exported()
        allowed = {("GET", "/"), ("GET", f"/collections/{COLLECTION}"),
                   ("POST", f"/collections/{COLLECTION}/points/scroll")}
        self.assertTrue(set(self.fake.log) <= allowed, self.fake.log)
        reader = eg.QdrantReader(self.fake.url, COLLECTION)
        for method, path in (("DELETE", f"/collections/{COLLECTION}"), ("PUT", f"/collections/{COLLECTION}/points"),
                             ("POST", f"/collections/{COLLECTION}/points/delete"), ("GET", "/collections")):
            with self.assertRaises(PermissionError):
                reader.request(method, path)
        self.assertTrue(set(self.fake.log) <= allowed)

    def test_refuses_a_change_during_the_export(self):
        real = eg.export_root

        def touch_then_export(archive, plan):
            path = self.root / "sources" / "rfc" / "rfc2002.txt"
            path.write_bytes(path.read_bytes() + b"x")
            return real(archive, plan)

        eg.export_root = touch_then_export
        try:
            with contextlib.redirect_stdout(io.StringIO()) as out:
                rc = eg.main(self.args(), env=self.env, encrypt_argv=COPY)
        finally:
            eg.export_root = real
        self.assertEqual(rc, 1, out.getvalue())
        self.assertEqual([p.name for p in self.out.iterdir()], [], "a failed export must leave nothing behind")

    # ------------------------------------------------------------ pieces

    def test_exact_f32_against_reference(self):
        rng = random.Random(7)
        texts = [f"{rng.uniform(-1, 1):.{rng.randint(1, 17)}g}" for _ in range(3000)]
        texts += [t for t, _, _ in midpoint_texts()] + ["0.0", "-0.0", "1.0", "1e-45", "3.4028235e38"]
        values, mids, fixed = eg.exact_f32(array("d", map(float, texts)), lambda: texts)
        self.assertEqual([f32_bits(v) for v in values], [ref_f32_bits(t) for t in texts])
        self.assertGreaterEqual(mids, 3)
        self.assertEqual(fixed, 2)
        self.assertEqual(eg.python_parser(texts), b"".join(struct.pack("<I", ref_f32_bits(t)) for t in texts))

    def test_strtof_parser_agrees(self):
        parse = eg.strtof_parser()
        if parse is None:
            self.skipTest("no libc strtof here")
        texts = [t for t, _, _ in midpoint_texts()] + ["0.1", "-0.25", "1e-40", "0.0312345"]
        self.assertEqual(parse(texts), b"".join(struct.pack("<I", ref_f32_bits(t)) for t in texts))

    def test_definitions_agree_with_rag_core(self):
        from rag_core.corpus import corpus_fingerprint
        shas = [d["sha256"] for d in self.made["docs"]]
        self.assertEqual(eg.corpus_fingerprint(shas), corpus_fingerprint(shas))
        stats = eg.rag_db_stats(self.root / "sqlite" / "rag.db")
        self.assertEqual(stats["corpus_fingerprint"], self.made["fingerprint"])
        try:
            from rag_core.dense import point_id_for_chunk
            from rag_core.status import corpus_stats
        except ImportError as exc:      # a plain Python has no qdrant_client/fastapi
            self.skipTest(f"rag_core.dense/status not importable here: {exc}")
        cid = self.made["chunks"][0][0]
        self.assertEqual(str(uuid.uuid5(uuid.NAMESPACE_URL, eg.POINT_NAME_PREFIX + cid)), point_id_for_chunk(cid))
        copy = self.tmp / "copy"                # rag_core opens with mode=ro, which may write -shm: not the root
        copy.mkdir()
        shutil.copyfile(self.root / "sqlite" / "rag.db", copy / "rag.db")
        theirs = corpus_stats(copy / "rag.db")
        for key in ("n_docs", "n_chunks", "corpus_fingerprint", "structure", "bm25_tables"):
            self.assertEqual(stats[key], theirs[key], key)

    def test_unpack_refuses_unsafe_members(self):
        for bad in ("../evil.txt", "/abs/evil.txt", f"{GENERATION}/../../evil.txt"):
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tar:
                info = tarfile.TarInfo(bad)
                info.size = 4
                tar.addfile(info, io.BytesIO(b"evil"))
            vm = self.tmp / "vm-bad"
            with contextlib.redirect_stdout(io.StringIO()):
                rc = eg.main(["unpack", f"--into={vm}"], stdin=io.BytesIO(buf.getvalue()))
            self.assertEqual(rc, 1, bad)
            self.assertFalse((self.tmp / "evil.txt").exists())
            self.assertFalse(Path("/abs/evil.txt").exists())

    def test_clean_deletes_only_its_files(self):
        self.out.mkdir(mode=0o700)
        mine = ["export-0123456789abcdef.tar.age", "export-0123456789abcdef.tar.age.json",
                ".export-0123456789abcdef.tar.age.partial"]
        for name in mine + ["keep.txt"]:
            (self.out / name).write_bytes(b"x")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(eg.main(["clean", f"--out-dir={self.out}"], env=self.env), 0)
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), ["keep.txt"])
        (self.out / "keep.txt").unlink()
        (self.out / mine[0]).write_bytes(b"x")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(eg.main(["clean", f"--out-dir={self.out}"], env=self.env), 0)
        self.assertFalse(self.out.exists())

    @unittest.skipUnless(shutil.which("age") and shutil.which("age-keygen"), "age is not installed here")
    def test_real_age_round_trip(self):
        key = self.tmp / "key.txt"
        subprocess.run(["age-keygen", "-o", str(key)], check=True, capture_output=True)
        recipient = subprocess.run(["age-keygen", "-y", str(key)], check=True, capture_output=True,
                                   text=True).stdout.strip()
        with contextlib.redirect_stdout(io.StringIO()) as out:
            rc = eg.main(self.args(recipient=recipient), env=self.env)
        self.assertEqual(rc, 0, out.getvalue())
        blob = self.out / f"export-{self.made['fingerprint']}.tar.age"
        side = json.loads((self.out / f"export-{self.made['fingerprint']}.tar.age.json").read_text())
        self.assertNotEqual(side["age_sha256"], side["tar_sha256"])
        plain = subprocess.run(["age", "-d", "-i", str(key), str(blob)], check=True, capture_output=True).stdout
        self.assertEqual(hashlib.sha256(plain).hexdigest(), side["tar_sha256"])


if __name__ == "__main__":
    unittest.main()
