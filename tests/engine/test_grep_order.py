"""reader.RootReader.grep: a literal scan that may not reach every document within its budget (scan_may_run_out)
visits first the documents whose BM25 index (FTS5) holds the pattern's words (scan_order), so a phrase that exists
is found before the scan budget runs out; the matches come back in the order of the documents asked for. The
ise31-compat phrases are the audit's false "no matches"."""
import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import dataclasses
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from rag_core import reader as reader_module
from rag_core.reader import RootReader, SourceFile
from rag_core.structure import index_root

from test_api_v2 import AUTH, RootClient
from v2_root import ISE31_COMPAT, add_document, build_root, line_of


class ScanOrderTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # this root's documents are read within any budget: grep orders them as it orders the corpus,
        # which may not be (scan_may_run_out; ScanReachTests)
        cls.enterClassContext(mock.patch.object(reader_module, "SCAN_CHARS_PER_SECOND", 0))
        cls.tmp = tempfile.TemporaryDirectory()
        cls.paths = build_root(Path(cls.tmp.name))
        add_document(cls.paths, "ise31-compat", "cisco", ISE31_COMPAT, "Compatibility, Release 3.1")
        index_root(cls.paths)
        cls.reader = RootReader(cls.paths)
        cls.docs = list(cls.reader.docs.values())          # doc_key order, as an unscoped /v2/grep scans

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def keys(self, pattern, docs=None):
        return [d.doc_key for d in self.reader.scan_order(pattern, docs or self.docs)]

    def grep_with_one_document(self, pattern, regex=False):
        """grep with a clock that moves a second each time a document is read: a budget of 1.5 s scans one."""
        clock = SimpleNamespace(now=0.0)
        visited = []
        real_file = RootReader.file

        def file(reader, doc):
            clock.now += 1.0
            visited.append(doc.doc_key)
            return real_file(reader, doc)

        with mock.patch.object(RootReader, "file", file), \
                mock.patch.object(reader_module, "time", SimpleNamespace(monotonic=lambda: clock.now)):
            matches, truncated = self.reader.grep(pattern, regex=regex, docs=self.docs, max_matches=50,
                                                  ignore_case=True, budget_seconds=1.5)
        return matches, truncated, visited

    def test_phrase_then_every_word_then_the_rest_each_in_the_order_given(self):
        self.assertEqual([d.doc_key for d in self.docs],
                         ["c9300-1718-rtng", "c9300-179-rtng", "ise31-compat", "rfc1587", "rfc3101", "rfc4271"])
        # rfc1587 has "the NSSA border router"; a chunk of rfc3101 has the three words apart
        expected = ["rfc1587", "rfc3101", "c9300-1718-rtng", "c9300-179-rtng", "ise31-compat", "rfc4271"]
        self.assertEqual(self.keys("NSSA border router"), expected)
        self.assertEqual(self.keys("nssa  BORDER-router."), expected)        # words, case: as FTS5 cuts them
        # a pattern that ends inside a word: its last word is a prefix
        self.assertEqual(self.keys("NSSA border rout")[:2], ["rfc1587", "rfc3101"])
        self.assertEqual(self.keys("RADIUS DTLS server")[0], "ise31-compat")
        # in another order given, each group keeps it
        self.assertEqual(self.keys("NSSA border router", self.docs[::-1]),
                         ["rfc1587", "rfc3101", "rfc4271", "ise31-compat", "c9300-179-rtng", "c9300-1718-rtng"])

    def test_no_words_one_document_or_an_index_that_cannot_answer_keep_the_order(self):
        self.assertEqual(self.reader.scan_order(":: |", self.docs), self.docs)
        one = self.docs[2:3]
        self.assertIs(self.reader.scan_order("NSSA border router", one), one)
        with mock.patch.object(reader_module, "DOCS_MATCHING", "SELECT doc_id FROM no_such_table WHERE ?"):
            self.assertEqual(self.reader.scan_order("NSSA border router", self.docs), self.docs)

    def test_queries_that_run_out_of_time_are_interrupted_and_the_groups_found_stand(self):
        # a phrase of common words read 2-7 s of FTS5 pages from a cold cache, more than the grep budget
        ticks = SimpleNamespace(now=0.0)

        def monotonic():                       # a second passes at each look at the clock
            ticks.now += 1.0
            return ticks.now

        with mock.patch.object(reader_module, "PROGRESS_STEPS", 1), \
                mock.patch.object(reader_module, "time", SimpleNamespace(monotonic=monotonic)):
            self.assertIs(self.reader.scan_order("NSSA border router", self.docs, 0.5), self.docs)
            self.assertGreaterEqual(ticks.now, 2)           # SQLite did look at the clock, and stopped
        # the phrase query done, the every-word query interrupted: the phrase group stands
        real = reader_module.docs_matching
        calls = []

        def second_interrupted(conn, query):
            calls.append(query)
            if len(calls) == 2:
                raise sqlite3.OperationalError("interrupted")
            return real(conn, query)

        with mock.patch.object(reader_module, "docs_matching", second_interrupted):
            self.assertEqual(self.keys("NSSA border router"),
                             ["rfc1587", "c9300-1718-rtng", "c9300-179-rtng", "ise31-compat", "rfc3101", "rfc4271"])
        self.assertEqual(calls, ['"nssa border router" *', '"nssa" AND "border" AND "router" *'])

    def test_the_ordering_gets_at_most_a_quarter_of_the_budget(self):
        seconds = []
        real = RootReader.scan_order

        def spy(reader, pattern, docs, limit):
            seconds.append(limit)
            return real(reader, pattern, docs, limit)

        with mock.patch.object(RootReader, "scan_order", spy):
            for budget in (30, 3.0, 1.0):
                self.reader.grep("NSSA border router", regex=False, docs=self.docs, max_matches=50,
                                 ignore_case=True, budget_seconds=budget)
        self.assertEqual(seconds, [reader_module.SCAN_ORDER_SECONDS, reader_module.SCAN_ORDER_SECONDS, 0.25])
        self.assertEqual(reader_module.SCAN_ORDER_SECONDS, 0.5)
        # an ordering that runs out of time leaves the scan its budget: the phrase is still found
        with mock.patch.object(reader_module, "PROGRESS_STEPS", 1), \
                mock.patch.object(reader_module, "SCAN_ORDER_SECONDS", 0.0):
            matches, truncated = self.reader.grep("NSSA border router", regex=False, docs=self.docs,
                                                  max_matches=50, ignore_case=True, budget_seconds=30)
        self.assertEqual(([m["doc_key"] for m in matches], truncated), (["rfc1587"], False))

    def test_a_phrase_late_in_doc_key_order_is_found_within_a_budget_that_reaches_one_document(self):
        # the audit's false "no matches": unscoped, the budget ran out before the ise31 guides
        for pattern, needle in (("RADIUS DTLS server", "Cipher suite |"),
                                ("configured as a RADIUS DTLS server", "Cipher suite |"),
                                ("DTLS server supports only DTLS 1.2", "TLS 1.0 support |"),
                                ("Validate ExtendedKeyUsage", "Validate ExtendedKeyUsage")):
            with self.subTest(pattern=pattern):
                matches, truncated, visited = self.grep_with_one_document(pattern)
                self.assertEqual(visited[0], "ise31-compat")
                self.assertEqual([(m["doc_key"], m["line"]) for m in matches],
                                 [("ise31-compat", line_of(ISE31_COMPAT, needle))])
                self.assertTrue(truncated)                  # the other documents were not scanned
        # in doc_key order the same budget ends in the first guide with nothing, as it did
        with mock.patch.object(RootReader, "scan_order", lambda reader, pattern, docs, seconds: docs):
            matches, truncated, visited = self.grep_with_one_document("RADIUS DTLS server")
        self.assertEqual((matches, truncated, visited[0]), ([], True, "c9300-1718-rtng"))
        # a regex is scanned in the order given
        matches, truncated, visited = self.grep_with_one_document(r"(RADIUS DTLS server)", regex=True)
        self.assertEqual((matches, truncated, visited[0]), ([], True, "c9300-1718-rtng"))

    def test_a_complete_scan_answers_as_the_order_given_did(self):
        args = dict(regex=False, docs=self.docs, max_matches=200, ignore_case=True, budget_seconds=30)
        for pattern in ("Type-7", "NSSA border router", "show ip ospf", "LSAs", "zzz not there"):
            with self.subTest(pattern=pattern):
                got = self.reader.grep(pattern, **args)
                self.assertFalse(got[1])
                with mock.patch.object(RootReader, "scan_order", lambda reader, p, docs, seconds: docs):
                    self.assertEqual(got, self.reader.grep(pattern, **args))
    def test_the_matches_come_back_in_the_order_given_when_the_scan_order_differs(self):
        # "TLS server" stands as words in zzz-words, but only inside "DTLS server" in aaa-inside: the scan
        # visits zzz-words first, the answer lists aaa-inside first
        with tempfile.TemporaryDirectory() as d:
            paths = build_root(Path(d))
            add_document(paths, "aaa-inside", "cisco", "# Note\nThe DTLS server answers.\n", None)
            add_document(paths, "zzz-words", "cisco", "# Note\nThe TLS server answers.\n", None)
            index_root(paths)
            reader = RootReader(paths)
            docs = list(reader.docs.values())
            self.assertEqual(reader.scan_order("TLS server", docs)[0].doc_key, "zzz-words")
            matches, truncated = reader.grep("TLS server", regex=False, docs=docs, max_matches=50,
                                             ignore_case=True, budget_seconds=30)
        self.assertEqual([m["doc_key"] for m in matches], ["aaa-inside", "zzz-words"])
        self.assertFalse(truncated)


class ScanReachTests(unittest.TestCase):
    """A literal grep is ordered only when its documents may not all be read within the budget (scan_may_run_out):
    a scope of a few guides is read in the order given, as before scan_order, without its FTS5 queries (20-250 ms
    warm on the corpus-v5 texts for "the router" or "is a", up to SCAN_ORDER_SECONDS cold)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.paths = build_root(Path(cls.tmp.name))
        # a dash is three bytes on disk and one character of the text
        add_document(cls.paths, "zz-dash", "cisco", "# Note\nThe NSSA border router – a note.\n", None)
        index_root(cls.paths)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def grep(self, reader, pattern, docs):
        """grep's answer, and the documents it asked scan_order to order."""
        asked = []
        real = RootReader.scan_order

        def spy(reader_, pattern_, docs_, seconds):
            asked.append([d.doc_key for d in docs_])
            return real(reader_, pattern_, docs_, seconds)

        with mock.patch.object(RootReader, "scan_order", spy):
            got = reader.grep(pattern, regex=False, docs=docs, max_matches=50, ignore_case=True, budget_seconds=3.0)
        return got, asked

    def test_documents_read_within_the_budget_are_read_in_the_order_given(self):
        reader = RootReader(self.paths)
        docs = list(reader.docs.values())
        self.assertEqual(reader_module.SCAN_CHARS_PER_SECOND, 4_000_000)
        for scope in (docs, [reader.docs["rfc3101"], reader.docs["rfc1587"]], docs[::-1]):
            with self.subTest(scope=[d.doc_key for d in scope]):
                got, asked = self.grep(reader, "NSSA border router", scope)
                self.assertEqual(asked, [])
                self.assertTrue(got[0])
                # documents that may not all be read in time are ordered; a complete scan answers alike
                with mock.patch.object(reader_module, "SCAN_CHARS_PER_SECOND", 0):
                    ordered, asked = self.grep(reader, "NSSA border router", scope)
                self.assertEqual(asked, [[d.doc_key for d in scope]])
                self.assertEqual(ordered, got)

    def test_what_the_documents_hold_is_counted_from_their_files(self):
        reader = RootReader(self.paths)                 # no file read yet: their sizes on disk
        docs = list(reader.docs.values())
        size = sum(d.path.stat().st_size for d in docs)
        chars = sum(len(SourceFile(d.path, d.doc_type).text) for d in docs)
        self.assertLess(chars, size)
        for budget in (1.0, 2.0):
            with mock.patch.object(reader_module, "SCAN_CHARS_PER_SECOND", size / budget):
                self.assertFalse(reader.scan_may_run_out(docs, budget))
            with mock.patch.object(reader_module, "SCAN_CHARS_PER_SECOND", (size - 1) / budget):
                self.assertTrue(reader.scan_may_run_out(docs, budget))
        for doc in docs:
            reader.file(doc)                            # read: the characters of their texts
        with mock.patch.object(reader_module, "SCAN_CHARS_PER_SECOND", chars):
            self.assertFalse(reader.scan_may_run_out(docs, 1.0))
        with mock.patch.object(reader_module, "SCAN_CHARS_PER_SECOND", chars - 1):
            self.assertTrue(reader.scan_may_run_out(docs, 1.0))
        # a file that cannot be read counts nothing (its first use reports it)
        gone = dataclasses.replace(docs[0], doc_key="gone", path=docs[0].path.with_name("gone.txt"))
        with mock.patch.object(reader_module, "SCAN_CHARS_PER_SECOND", 0):
            self.assertFalse(reader.scan_may_run_out([gone], 1.0))


class GrepEndpointTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.enterClassContext(mock.patch.object(reader_module, "SCAN_CHARS_PER_SECOND", 0))   # as ScanOrderTests
        cls.root = RootClient()
        cls.c = cls.root.client

    @classmethod
    def tearDownClass(cls):
        cls.root.close()

    def test_a_literal_grep_scans_in_scan_order(self):
        calls = []
        real = RootReader.scan_order

        def spy(reader, pattern, docs, seconds):
            calls.append((pattern, [d.doc_key for d in docs], seconds))
            return real(reader, pattern, docs, seconds)

        with mock.patch.object(RootReader, "scan_order", spy):
            unscoped = self.c.post("/v2/grep", json={"pattern": "NSSA border router"}, headers=AUTH).json()
            scoped = self.c.post("/v2/grep", json={"pattern": "Type-7 LSAs", "docs": ["rfc3101", "rfc1587"]},
                                 headers=AUTH).json()
            regex = self.c.post("/v2/grep", json={"pattern": "Type-7 LSAs?", "regex": True}, headers=AUTH).json()
        # the ordering gets 0.5 s of the 3 s budget (GREP_BUDGET_SECONDS)
        self.assertEqual(calls, [("NSSA border router", ["c9300-1718-rtng", "c9300-179-rtng", "rfc1587", "rfc3101",
                                                         "rfc4271"], 0.5),
                                 ("Type-7 LSAs", ["rfc3101", "rfc1587"], 0.5)])
        self.assertEqual([m["doc_key"] for m in unscoped["matches"]], ["rfc1587"])
        self.assertEqual(unscoped["truncated"], False)
        # the documents asked for, in that order
        self.assertEqual(list(dict.fromkeys(m["doc_key"] for m in scoped["matches"])), ["rfc3101", "rfc1587"])
        self.assertTrue(regex["matches"])


if __name__ == "__main__":
    unittest.main()
