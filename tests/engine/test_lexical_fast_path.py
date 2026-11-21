"""BM25 fast path: rank in FTS5 alone (no per-match join), then read only the rows that made the cut; and drop
the query terms found in at least half of the rows. The hits, scores and order are those of the joined SQL it
replaces (pruning: up to near-ties within ~1e-4)."""
import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import gc
import hashlib
import os
import re
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from rag_core.db import connect, initialize
from rag_core.paths import RAGPaths
from rag_core.retrieval_types import QueryVariant
from rag_core.retrievers import lexical
from rag_core.retrievers.lexical import LexicalRetriever, without_high_df

WORDS = ["ospf", "nssa", "translator", "bgp", "reflector", "eigrp", "stub", "area", "lsa", "route"]


def build(root: Path, n_docs: int = 4, per_doc: int = 15) -> RAGPaths:
    """A root whose chunks all contain "the" and "is" (high df) and a spread of rarer words, with repeats so that
    scores differ and some tie."""
    paths = RAGPaths(root)
    initialize(paths)
    with connect(paths.sqlite / "rag.db") as conn:
        for d in range(n_docs):
            doc = f"doc{d}"
            conn.execute(
                "INSERT INTO documents(doc_id, sha256, source_name, original_filename, status) VALUES (?,?,?,?,?)",
                (doc, hashlib.sha256(doc.encode()).hexdigest(), doc, doc + ".txt", "CHUNKED"),
            )
            for i in range(per_doc):
                k = d * per_doc + i
                words = ["the", "is"] + [WORDS[j] for j in range(len(WORDS)) if (k * 7 + j * 3) % (j + 2) == 0]
                words += ["nssa"] * (k % 3) + ["the"] * (k % 5)
                text = " ".join(words) + f" chunk {k}"
                conn.execute(
                    "INSERT INTO chunks(chunk_id, chunk_sha256, doc_id, ordinal, heading_path_json, text, "
                    "embedding_text, token_count, char_count) VALUES (?,?,?,?,?,?,?,?,?)",
                    (f"c{k:03d}", hashlib.sha256(text.encode()).hexdigest(), doc, i, '["H"]', text, text,
                     len(words), len(text)),
                )
    return paths


def joined(paths, text, limit, only=""):
    """The SQL before the fast path: join and ORDER BY bm25 over every match."""
    with connect(paths.sqlite / "rag.db") as conn:
        rows = conn.execute(lexical.JOINED_SQL.format(only=only), (lexical.fts_query(text), limit)).fetchall()
    return [(r["chunk_id"], -float(r["raw_score"])) for r in rows]


def got(retriever, text, limit, doc_ids=None, exclude=None):
    return [(h.chunk_id, h.score)
            for h in retriever.search(QueryVariant("v", "primary", text), limit=limit, doc_ids=doc_ids,
                                      **({"exclude_doc_ids": exclude} if exclude is not None else {}))]


QUERIES = ("the ospf nssa translator", "what is the stub area", "bgp", "zzz nothing", "ip.ospf 10.0.0.0/8",
           "the is", "chunk 7", "NSSA OR-case \"quoted\"")


class RankOnlyTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.paths = build(Path(self.tmp.name) / "root")

    def tearDown(self):
        self.tmp.cleanup()

    def test_rank_only_path_is_identical_to_the_joined_sql(self):
        bm25 = LexicalRetriever(self.paths, options={"prune_high_df": False})
        for text in QUERIES:
            for limit in (1, 5, 50):
                self.assertEqual(got(bm25, text, limit), joined(self.paths, text, limit), (text, limit))

    def test_the_fast_path_reads_only_the_ranked_rows(self):
        bm25 = LexicalRetriever(self.paths, options={"prune_high_df": False})
        seen = []
        with connect(self.paths.sqlite / "rag.db") as conn:
            conn.set_trace_callback(seen.append)
            self.assertTrue(bm25.all_searchable(conn))
            rows = bm25._ranked_rows(conn, lexical.query_tokens("the ospf"), 5)
        self.assertEqual(len(rows), 5)
        self.assertFalse(any("JOIN documents" in sql for sql in seen), seen)

    def test_unsearchable_document_uses_the_joined_sql(self):
        with connect(self.paths.sqlite / "rag.db") as conn:
            conn.execute("UPDATE documents SET status = 'FAILED' WHERE doc_id = 'doc1'")
        bm25 = LexicalRetriever(self.paths)
        hits = bm25.search(QueryVariant("v", "primary", "the ospf"), limit=60)
        self.assertTrue(hits)
        self.assertNotIn("doc1", {h.doc_id for h in hits})
        self.assertEqual(got(bm25, "the ospf", 60), joined(self.paths, "the ospf", 60))

    def test_a_status_change_is_seen_by_a_running_retriever(self):
        bm25 = LexicalRetriever(self.paths)
        bm25.warm()
        self.assertEqual(len(got(bm25, "the", 60)), 60)
        with connect(self.paths.sqlite / "rag.db") as conn:
            conn.execute("UPDATE documents SET status = 'FAILED' WHERE doc_id = 'doc0'")
        self.assertEqual(got(bm25, "the", 60), joined(self.paths, "the", 60))
        self.assertEqual(len(got(bm25, "the", 60)), 45)

    def test_a_chunk_without_a_document_row_uses_the_joined_sql(self):
        with connect(self.paths.sqlite / "rag.db") as conn:
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.execute("DELETE FROM documents WHERE doc_id = 'doc2'")
        bm25 = LexicalRetriever(self.paths)
        with connect(self.paths.sqlite / "rag.db") as conn:
            self.assertFalse(bm25.all_searchable(conn))
        self.assertNotIn("doc2", {h.doc_id for h in bm25.search(QueryVariant("v", "primary", "the"), limit=60)})

    def test_an_index_row_without_a_chunk_falls_back_to_the_joined_sql(self):
        bm25 = LexicalRetriever(self.paths)
        with connect(self.paths.sqlite / "rag.db") as conn:
            # an FTS entry whose chunk row is gone (the delete trigger bypassed)
            conn.execute("DROP TRIGGER chunks_ad")
            conn.execute("DELETE FROM chunks WHERE chunk_id = 'c000'")
        self.assertEqual(got(bm25, "the", 60), joined(self.paths, "the", 60))
        self.assertNotIn("c000", [c for c, _ in got(bm25, "the", 60)])

    def test_doc_filter_still_restricts(self):
        bm25 = LexicalRetriever(self.paths)
        hits = bm25.search(QueryVariant("v", "primary", "the ospf"), limit=50, doc_ids=frozenset({"doc2"}))
        self.assertTrue(hits)
        self.assertEqual({h.doc_id for h in hits}, {"doc2"})
        self.assertEqual([h.rank for h in hits], list(range(1, len(hits) + 1)))
        self.assertEqual(got(bm25, "the ospf", 50, frozenset({"doc2"})),
                         joined(self.paths, "the ospf", 50, "AND chunks.doc_id IN ('doc2')"))
        self.assertEqual(bm25.search(QueryVariant("v", "primary", "the"), limit=5, doc_ids=frozenset()), [])

    def test_warm_on_a_root_without_a_database_creates_nothing(self):
        root = Path(self.tmp.name) / "empty"
        LexicalRetriever(RAGPaths(root)).warm()
        self.assertFalse(root.exists())


class ExclusionTests(unittest.TestCase):
    """exclude_doc_ids (API v2's opt-in doc types): the rank-only fast path with the excluded documents' rowids left
    out, the same hits as the joined SQL with NOT IN."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.paths = build(Path(self.tmp.name) / "root")

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_exclusion_is_the_joined_sql_with_not_in(self):
        bm25 = LexicalRetriever(self.paths, options={"prune_high_df": False})
        for exclude, only in ((frozenset({"doc1"}), "AND chunks.doc_id NOT IN ('doc1')"),
                              (frozenset({"doc3", "doc0"}), "AND chunks.doc_id NOT IN ('doc0', 'doc3')")):
            for text in QUERIES:
                for limit in (1, 5, 50):
                    hits = got(bm25, text, limit, exclude=exclude)
                    self.assertEqual(hits, joined(self.paths, text, limit, only), (text, limit, exclude))
            self.assertTrue(got(bm25, "the ospf", 50, exclude=exclude))
        # with pruning on, as the default retriever ranks, for queries whose pruned form has too few rows ("ospf" and
        # "nssa" are high-df here too) or nothing left: the full query answers, excluding (the pruned answer taken:
        # test_the_pruned_answer_leaves_the_documents_out_too)
        pruned = LexicalRetriever(self.paths)
        for text in ("the ospf area", "what is the route", "The OSPF NSSA"):
            a = got(pruned, text, 10, exclude=frozenset({"doc1"}))
            b = joined(self.paths, text, 10, "AND chunks.doc_id NOT IN ('doc1')")
            self.assertEqual(sorted((round(s, 4), c) for c, s in a), sorted((round(s, 4), c) for c, s in b))

    def test_the_exclusion_keeps_the_fast_path_and_reads_no_chunk_row_of_an_excluded_document(self):
        bm25 = LexicalRetriever(self.paths, options={"prune_high_df": False})
        seen = []
        with connect(self.paths.sqlite / "rag.db") as conn:
            conn.set_trace_callback(seen.append)
            rows = bm25._ranked_rows(conn, lexical.query_tokens("the ospf"), 50, frozenset({"doc1", "doc2"}))
            conn.set_trace_callback(None)
            # the excluded documents' rowids come from the (doc_id, ordinal) index alone
            plan = [r[3] for r in conn.execute(
                "EXPLAIN QUERY PLAN " + lexical.EXCLUDING_RANK_SQL.format(marks="?,?"), ('"the"', "doc1", "doc2", 5))]
        self.assertEqual(len(rows), 30)                     # every chunk of doc0 and doc3
        self.assertEqual({row["doc_id"] for row, _ in rows}, {"doc0", "doc3"})
        self.assertFalse(any("JOIN documents" in sql for sql in seen), seen)
        self.assertTrue(any("COVERING INDEX idx_chunks_doc_ordinal" in step for step in plan), plan)
        self.assertFalse(any(step.startswith("SCAN chunks") for step in plan), plan)
        # the rows read are those ranked: rowids of doc0 and doc3 only (doc_d holds rowids 15d+1 .. 15d+15)
        read = [sql for sql in seen if "WHERE rowid IN (" in sql]
        self.assertEqual(len(read), 1)
        ids = [int(n) for n in re.findall(r"\d+", read[0].split("WHERE rowid IN (")[1])]
        self.assertTrue(ids and all(1 <= n <= 15 or 46 <= n <= 60 for n in ids), ids)

    def test_the_pruned_answer_leaves_the_documents_out_too(self):
        # "the translator" prunes to "translator", which has rows enough: that answer is taken (no full query), as on
        # a production root most of the time. Chunks of doc1 rank in its top 10; excluded, none does
        pruned = LexicalRetriever(self.paths)
        doc1 = {f"c{k:03d}" for k in range(15, 30)}
        with mock.patch.object(lexical, "match_expression", wraps=lexical.match_expression) as spy:
            everything = got(pruned, "the translator", 10)
            hits = got(pruned, "the translator", 10, exclude=frozenset({"doc1"}))
        self.assertEqual([c.args[0] for c in spy.call_args_list], [["translator"], ["translator"]])
        self.assertTrue(doc1 & {c for c, _ in everything}, everything)
        self.assertEqual(len(hits), 10)
        self.assertFalse(doc1 & {c for c, _ in hits}, hits)
        b = joined(self.paths, "the translator", 10, "AND chunks.doc_id NOT IN ('doc1')")
        self.assertEqual(sorted((round(s, 4), c) for c, s in hits), sorted((round(s, 4), c) for c, s in b))

    def test_too_few_rows_after_the_exclusion_falls_back_to_the_full_query(self):
        # "7" is in one row, of doc0: left out, the pruned query finds nothing, and the full query pads the list
        # with "the" rows of the other documents, exactly as without pruning
        pruned, full = LexicalRetriever(self.paths), LexicalRetriever(self.paths, options={"prune_high_df": False})
        exclude = frozenset({"doc0"})
        with mock.patch.object(lexical, "match_expression", wraps=lexical.match_expression) as spy:
            hits = got(pruned, "the 7", 20, exclude=exclude)
        self.assertEqual([c.args[0] for c in spy.call_args_list], [["7"], ["the", "7"]])
        self.assertEqual(len(hits), 20)
        self.assertEqual(hits, got(full, "the 7", 20, exclude=exclude))
        self.assertEqual(hits, joined(self.paths, "the 7", 20, "AND chunks.doc_id NOT IN ('doc0')"))
        self.assertTrue(got(pruned, "the 7", 20))                      # not left out, "7" ranks first
        self.assertEqual(got(pruned, "the 7", 20)[0][0], "c007")

    def test_unsearchable_documents_use_the_joined_sql_with_not_in(self):
        with connect(self.paths.sqlite / "rag.db") as conn:
            conn.execute("UPDATE documents SET status = 'FAILED' WHERE doc_id = 'doc1'")
        bm25 = LexicalRetriever(self.paths)
        hits = got(bm25, "the ospf", 60, exclude=frozenset({"doc2"}))
        self.assertEqual(hits, joined(self.paths, "the ospf", 60, "AND chunks.doc_id NOT IN ('doc2')"))
        self.assertEqual(len(hits), 30)

    def test_with_doc_ids_the_exclusion_narrows_them(self):
        bm25 = LexicalRetriever(self.paths)
        both = got(bm25, "the ospf", 50, frozenset({"doc1", "doc2"}), exclude=frozenset({"doc2", "doc3"}))
        self.assertEqual(both, got(bm25, "the ospf", 50, frozenset({"doc1"})))
        self.assertEqual(got(bm25, "the", 5, frozenset({"doc2"}), exclude=frozenset({"doc2"})), [])
        # nothing to leave out is no exclusion
        self.assertEqual(got(bm25, "the ospf", 50, exclude=frozenset()), got(bm25, "the ospf", 50))



class HighDfPruningTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.paths = build(Path(self.tmp.name) / "root")

    def tearDown(self):
        self.tmp.cleanup()

    def assertSameHits(self, a, b):
        """Same hits with the same scores to 1e-4; hits closer than that (the dropped terms add ~1e-6 each) may
        swap places."""
        self.assertEqual(len(a), len(b))
        self.assertEqual(sorted((round(s, 4), c) for c, s in a), sorted((round(s, 4), c) for c, s in b))
        for (_, s1), (_, s2) in zip(a, b):
            self.assertAlmostEqual(s1, s2, places=4)

    def test_high_df_terms_are_the_ones_in_half_the_rows(self):
        bm25 = LexicalRetriever(self.paths)
        with connect(self.paths.sqlite / "rag.db") as conn:
            high = bm25.high_df_terms(conn)
            n = conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
            for term in ("the", "is", "chunk", "nssa", "reflector", "7"):
                rows = conn.execute("SELECT count(*) FROM chunk_fts WHERE chunk_fts MATCH ?",
                                    (f'"{term}"',)).fetchone()[0]
                self.assertEqual(term in high, 2 * rows >= n, (term, rows, n))
        self.assertIn("the", high)
        self.assertNotIn("reflector", high)
        self.assertEqual(without_high_df(["The", "reflector", "is", "ip.ospf", "IS-IS", "the-is"], high),
                         ["reflector", "ip.ospf", "IS-IS", "the-is"])
        self.assertEqual(without_high_df(["the", "is"], high), [])

    def test_pruning_keeps_the_ranking(self):
        pruned, full = LexicalRetriever(self.paths), LexicalRetriever(self.paths, options={"prune_high_df": False})
        for text in ("the ospf area", "what is the route", "is the lsa stub", "The OSPF NSSA", "bgp the"):
            for limit in (3, 10):
                self.assertSameHits(got(pruned, text, limit), got(full, text, limit))
                self.assertSameHits(got(pruned, text, limit), joined(self.paths, text, limit))

    def test_the_pruned_query_is_the_one_ranked(self):
        bm25 = LexicalRetriever(self.paths)
        with mock.patch.object(lexical, "match_expression", wraps=lexical.match_expression) as spy:
            got(bm25, "the reflector area", 3)
        self.assertEqual(spy.call_args_list[0].args[0], ["reflector", "area"])
        self.assertEqual(len(spy.call_args_list), 1)

    def test_too_few_rows_without_high_df_terms_falls_back_to_the_full_query(self):
        pruned, full = LexicalRetriever(self.paths), LexicalRetriever(self.paths, options={"prune_high_df": False})
        rare = "the 7"              # "7" matches one row: the full query pads the list with "the" rows
        self.assertEqual(len(got(full, rare, 20)), 20)
        self.assertEqual(got(pruned, rare, 20), got(full, rare, 20))
        only_high = "the is"        # nothing left after pruning: the full query runs
        self.assertEqual(got(pruned, only_high, 5), got(full, only_high, 5))

    def test_disabled_pruning_is_the_joined_sql_exactly(self):
        bm25 = LexicalRetriever(self.paths, options={"prune_high_df": False})
        for text in QUERIES:
            self.assertEqual(got(bm25, text, 10), joined(self.paths, text, 10), text)

    def test_the_term_set_follows_the_index(self):
        bm25 = LexicalRetriever(self.paths)
        bm25.warm()
        with connect(self.paths.sqlite / "rag.db") as conn:
            self.assertNotIn("reflector", bm25.high_df_terms(conn))
            for k in range(200):
                text = f"reflector filler {k}"
                conn.execute(
                    "INSERT INTO chunks(chunk_id, chunk_sha256, doc_id, ordinal, heading_path_json, text, "
                    "embedding_text, token_count, char_count) VALUES (?,?,?,?,?,?,?,?,?)",
                    (f"x{k:03d}", hashlib.sha256(text.encode()).hexdigest(), "doc0", 100 + k, "[]", text, text, 3,
                     len(text)),
                )
        with connect(self.paths.sqlite / "rag.db") as conn:
            self.assertIn("reflector", bm25.high_df_terms(conn))

    def test_an_index_without_vocab_support_ranks_the_full_query(self):
        bm25 = LexicalRetriever(self.paths)
        with mock.patch.object(lexical, "read_high_df_terms", side_effect=RuntimeError("no fts5vocab")):
            self.assertEqual(got(bm25, "the ospf", 10), joined(self.paths, "the ospf", 10))


class IndexFactsLifetimeTests(unittest.TestCase):
    """The per-index facts live as long as the index version. SQLite deletes the WAL when the last connection
    closes (CPython closes one when it is collected) and the next connection makes a new, empty one: that is not
    a new version. A committed write, a checkpoint and a replaced file are."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.paths = build(Path(self.tmp.name) / "root")
        self.db = self.paths.sqlite / "rag.db"
        self.wal = Path(f"{self.db}-wal")
        gc.collect()                    # the build's connections: closed, checkpointed, no WAL left

    def computed(self, bm25):
        """The names bm25.facts computes, in order."""
        names = []
        real = bm25.facts.get
        bm25.facts.get = lambda name, compute: real(name, lambda: (names.append(name), compute())[1])
        return names

    def add_reflector_rows(self, conn, n=200):
        """Enough rows with "reflector" to put it in half of the rows (a new high-df term, a bigger file)."""
        for k in range(n):
            text = f"reflector filler {k}"
            conn.execute(
                "INSERT INTO chunks(chunk_id, chunk_sha256, doc_id, ordinal, heading_path_json, text, "
                "embedding_text, token_count, char_count) VALUES (?,?,?,?,?,?,?,?,?)",
                (f"x{k:03d}", hashlib.sha256(text.encode()).hexdigest(), "doc0", 100 + k, "[]", text, text, 3,
                 len(text)),
            )
        conn.commit()

    def test_searches_with_every_connection_closed_between_them_compute_the_facts_once(self):
        bm25 = LexicalRetriever(self.paths)
        names = self.computed(bm25)
        bm25.warm()
        for _ in range(5):
            gc.collect()                # every rag.db connection closed: SQLite deleted the WAL
            self.assertFalse(self.wal.exists() and self.wal.stat().st_size)
            time.sleep(0.02)            # so the search's new, empty WAL gets a later mtime
            self.assertTrue(got(bm25, "the ospf area", 5))
        self.assertEqual(names, ["all_searchable", "split", "high_df"])

    def test_a_committed_write_is_seen_while_the_writer_is_open(self):
        bm25 = LexicalRetriever(self.paths)
        names = self.computed(bm25)
        bm25.warm()
        writer = connect(self.db)
        self.addCleanup(writer.close)
        self.add_reflector_rows(writer)
        self.assertGreater(self.wal.stat().st_size, 0)                 # the write is in the WAL only
        with connect(self.db) as conn:
            self.assertIn("reflector", bm25.high_df_terms(conn))
        self.assertEqual(names.count("high_df"), 2)

    def test_a_write_checkpointed_into_the_database_is_seen(self):
        bm25 = LexicalRetriever(self.paths)
        names = self.computed(bm25)
        bm25.warm()
        writer = connect(self.db)
        self.add_reflector_rows(writer)
        writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")             # into rag.db; the WAL left empty
        self.assertEqual(self.wal.stat().st_size, 0)
        writer.close()
        with connect(self.db) as conn:
            self.assertIn("reflector", bm25.high_df_terms(conn))
        self.assertEqual(names.count("high_df"), 2)

    def test_a_database_file_replaced_by_one_of_the_same_size_and_mtime_is_a_new_version(self):
        bm25 = LexicalRetriever(self.paths)
        names = self.computed(bm25)
        bm25.warm()
        gc.collect()
        copy = self.db.with_name("rag.db.new")
        shutil.copy2(self.db, copy)                                     # same bytes, size and mtime
        os.replace(copy, self.db)
        got(bm25, "the ospf area", 5)
        self.assertEqual(names.count("high_df"), 2)


class WarmTests(unittest.TestCase):

    def test_the_orchestrator_warms_what_can_be_warmed_and_survives_a_failure(self):
        from rag_core.orchestrator import RetrievalOrchestrator

        with tempfile.TemporaryDirectory() as tmp:
            paths = build(Path(tmp) / "root", n_docs=1, per_doc=4)
            bm25 = LexicalRetriever(paths)

            class Broken:
                name = "dense"

                def warm(self):
                    raise RuntimeError("no warm-up for you")

            orch = RetrievalOrchestrator.__new__(RetrievalOrchestrator)
            orch.retrievers = {"bm25": bm25, "dense": Broken(), "colbert": object()}
            with self.assertLogs("rag_core.orchestrator", "WARNING") as logs:
                orch.warm()
            self.assertIn("dense warm-up failed", logs.output[0])
            self.assertEqual(set(bm25.facts._values), {"all_searchable", "split", "high_df"})


if __name__ == "__main__":
    unittest.main()
