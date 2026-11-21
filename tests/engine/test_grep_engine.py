"""retrieval.json "grep": {"regex_engine": true} (reader.EngineScan, api_v2.grep_settings; default off): grep's
patterns run on the regex module with concurrent=True, so a literal scan no longer holds the GIL (stdlib re did;
a regex grep never did) and a case-insensitive literal is found 6-44x sooner. The matches are the default grep's,
in the same order and within the same budget:
the scan order (scan_may_run_out, scan_order) and the budget checks are the same, and a case-insensitive literal
with an i or I is searched by stdlib re in a document holding U+0130 or U+0131, the only characters the two
engines fold differently. Without the setting nothing changes. The tiny root of tests/v2_root.py; no network."""
import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import inspect
import json
import re
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import regex as rx
from fastapi.testclient import TestClient

from rag_core import api, api_v2
from rag_core import reader as reader_module
from rag_core.reader import DOTTED_I, EngineScan, RootReader
from rag_core.structure import index_root

import test_api_v2
import test_grep_order
from test_api_v2 import AUTH, RootClient
from v2_root import ISE31_COMPAT, add_document, build_root, line_of

DOTTED = "\n".join([
    "# Turkish interface notes",
    "\u0130nterface GigabitEthernet1 description uplink",   # stdlib re matches U+0130 to I: "INTERFACE" finds it
    "\u0131p address 10.0.0.1 255.255.255.0",               # and U+0131 to i: "ip address" finds it
    "interface Loopback0",
    "a STR\u0130NG and a str\u0131ng",
    "",
])
FOLDING = "\n".join([
    "# Unicode folding notes",
    "\u017ftatic route through a long s",                   # U+017F: both engines match it to s
    "a 5 \u212aB buffer, Kelvin sign",                      # U+212A: both to k
    "Stra\u00dfe and STRASSE, na\u00efve NA\u00cfVE, \u00b5s and \u03bcs",
    "\u03a9mega and \u03c9mega, \u03a3\u0399\u0393\u039c\u0391 \u03c3\u03af\u03b3\u03bc\u03b1 \u03c2",
    "",
])
MANY = "\n".join(["# Neighbor log"] + [f"OSPF neighbor 10.0.0.{n} changed state to FULL" for n in range(1, 121)]
                 + [""])

LITERALS = [
    "interface", "INTERFACE", "Interface", "ip address", "\u0131p", "\u0130nterface", "i", "I", "string",
    "STR\u0130NG", "static", "s", "kb", "K", "ss", "strasse", "stra\u00dfe", "na\u00efve", "\u00b5s", "\u03bcs",
    "\u03c9mega", "\u03c3\u03af\u03b3\u03bc\u03b1", "\u03c2", "Type-7", "TYPE-7 LSAs should", "NSSA border router",
    "show ip ospf", "OSPF neighbor", "[Page 2]", "zzz not there", ".", "(", "a.b", "10.0.0.1",
    "changed state to FULL", "-", " ", "\\", "RADIUS DTLS server",
]
REGEXES = [
    r"translate\s+type7\s+\[?always", r"interface\s+\w+", "[\u0130\u0131]", r"Type-?7\s+LSAs?", r"\bip\b",
    r"(?i)ospf", r"neighbor 10\.0\.0\.1\d", r"\u03c3\w+",
]


def add_fixture_documents(paths):
    add_document(paths, "ise31-compat", "cisco", ISE31_COMPAT, "Compatibility, Release 3.1")
    add_document(paths, "tr-dotted", "cisco", DOTTED, "Turkish notes")
    add_document(paths, "zz-folding", "cisco", FOLDING, "Folding notes")
    add_document(paths, "zz-many", "cisco", MANY, "Neighbor log")


class EngineParityTests(unittest.TestCase):
    """RootReader.grep with regex_engine against without it, on documents with every character the engines
    could fold differently."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.paths = build_root(Path(cls.tmp.name))
        add_fixture_documents(cls.paths)
        index_root(cls.paths)
        cls.reader = RootReader(cls.paths)
        cls.docs = list(cls.reader.docs.values())

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def both(self, pattern, **kw):
        args = dict(regex=False, docs=self.docs, max_matches=200, ignore_case=True, budget_seconds=30)
        args.update(kw)
        return self.reader.grep(pattern, **args), self.reader.grep(pattern, **args, regex_engine=True)

    def test_every_pattern_finds_the_same_lines_in_the_same_order(self):
        scopes = {"all": self.docs, "reversed": self.docs[::-1],
                  "two": [self.reader.docs["tr-dotted"], self.reader.docs["rfc3101"]]}
        compared = 0
        for regex, patterns in ((False, LITERALS), (True, REGEXES)):
            for pattern in patterns:
                for ignore_case in (True, False):
                    for scope, docs in scopes.items():
                        for max_matches in (1, 3, 50, 200):
                            with self.subTest(pattern=pattern, regex=regex, ignore_case=ignore_case, scope=scope,
                                              max_matches=max_matches):
                                old, new = self.both(pattern, regex=regex, ignore_case=ignore_case, docs=docs,
                                                     max_matches=max_matches)
                                self.assertEqual(new, old)
                                compared += 1
        self.assertEqual(compared, (len(LITERALS) + len(REGEXES)) * 2 * 3 * 4)
        # the fixture does exercise the matches it is about
        found = {p: {m["doc_key"] for m in self.both(p)[0][0]} for p in ("interface", "ip address", "static", "kb")}
        self.assertIn("tr-dotted", found["interface"])
        self.assertIn("tr-dotted", found["ip address"])
        self.assertIn("zz-folding", found["static"])
        self.assertIn("zz-folding", found["kb"])

    def test_the_dotted_and_dotless_i_are_matched_as_stdlib_re_matches_them(self):
        # the one difference between the engines (every code point against every ASCII character): under
        # IGNORECASE stdlib re matches I to U+0130 and i to U+0131, the regex module does not
        self.assertEqual(DOTTED_I, ("\u0130", "\u0131"))
        for pattern, text in (("INTERFACE", "\u0130nterface"), ("ip", "\u0131p")):
            self.assertIsNotNone(re.search(pattern, text, re.IGNORECASE))
            self.assertIsNone(rx.compile(pattern, rx.IGNORECASE, cache_pattern=False).search(text))
        for pattern, needle in (("INTERFACE", "\u0130nterface Gigabit"), ("ip address", "\u0131p address"),
                                ("STRING", "a STR\u0130NG"), ("interface", "\u0130nterface Gigabit")):
            with self.subTest(pattern=pattern):
                old, new = self.both(pattern, docs=[self.reader.docs["tr-dotted"]])
                self.assertEqual(new, old)
                self.assertIn(line_of(DOTTED, needle), [m["line"] for m in new[0]])
        # the module alone would have missed them
        fast = rx.compile("INTERFACE", rx.IGNORECASE, cache_pattern=False)
        self.assertIsNone(fast.search(self.reader.file(self.reader.docs["tr-dotted"]).text.split("\n")[1]))
        # case-sensitive, the engines agree (the regex module searches it there too)
        old, new = self.both("interface", ignore_case=False, docs=[self.reader.docs["tr-dotted"]])
        self.assertEqual(new, old)
        self.assertEqual([m["line"] for m in new[0]],
                         [line_of(DOTTED, "Turkish interface"), line_of(DOTTED, "interface Loopback0")])

    def test_stdlib_re_is_used_only_where_the_engines_may_differ(self):
        dotted = self.reader.file(self.reader.docs["tr-dotted"])
        plain = self.reader.file(self.reader.docs["rfc3101"])
        self.assertEqual((dotted.dotted_i, plain.dotted_i), (True, False))
        re_iterator = type(re.compile("x").finditer(""))

        def engine_of(pattern, source, **kw):
            it = EngineScan(pattern, regex=kw.get("regex", False), ignore_case=kw.get("ignore_case", True)) \
                .finditer(source, 10.0)
            return "re" if isinstance(it, re_iterator) else "regex"

        self.assertEqual(engine_of("interface", dotted), "re")         # an i, a document holding U+0130
        self.assertEqual(engine_of("interface", plain), "regex")
        self.assertEqual(engine_of("BGP", dotted), "regex")            # no i or I: U+0130/U+0131 match nothing
        self.assertEqual(engine_of("interface", dotted, ignore_case=False), "regex")
        self.assertEqual(engine_of("na\u00efve", plain), "re")         # non-ASCII and case-insensitive: everywhere
        self.assertEqual(engine_of("na\u00efve", plain, ignore_case=False), "regex")
        self.assertEqual(engine_of(r"interface\s+\w+", dotted, regex=True), "regex")    # a regex: as before
        # U+017F and U+212A are no reason for stdlib re: both engines fold them alike
        self.assertEqual(self.reader.file(self.reader.docs["zz-folding"]).dotted_i, False)

    def test_max_matches_and_the_matches_cut_to_a_window_are_the_same(self):
        for max_matches in (1, 2, 7, 119, 120, 121, 200):
            with self.subTest(max_matches=max_matches):
                old, new = self.both("changed state to full", max_matches=max_matches)
                self.assertEqual(new, old)
                self.assertEqual((len(new[0]), new[1]), (min(max_matches, 120), max_matches < 120))
        long = "x " * 400 + "needle here " + "y " * 400
        with tempfile.TemporaryDirectory() as d:
            paths = build_root(Path(d))
            add_document(paths, "long-line", "cisco", "# Long\n" + long + "\n", None)
            index_root(paths)
            reader = RootReader(paths)
            docs = list(reader.docs.values())
            args = dict(regex=False, docs=docs, max_matches=50, ignore_case=True, budget_seconds=30)
            old, new = reader.grep("NEEDLE", **args), reader.grep("NEEDLE", **args, regex_engine=True)
        self.assertEqual(new, old)
        self.assertIn("needle here", new[0][0]["text"])
        self.assertLess(len(new[0][0]["text"]), len(long))          # cut around the match


class FoldTableTests(unittest.TestCase):
    """DOTTED_I was measured on one Python and one regex module, and EngineScan sends only what it names to stdlib
    re. The release gate runs this on deep32-engine's own venv, so a Python or regex upgrade whose Unicode tables fold
    another character to an ASCII one differently (a regex bump can come with transformers) fails here instead of
    changing the lines regex_engine finds. Every code point stands on a line of its own: a match is within one."""

    @classmethod
    def setUpClass(cls):
        cls.points = [chr(c) for c in range(sys.maxunicode + 1) if not 0xD800 <= c <= 0xDFFF]
        cls.text = "\n".join(cls.points)
        cls.versions = f"regex {rx.__version__} on Python {sys.version.split()[0]}"

    def test_every_ascii_character_matches_the_same_code_points_but_for_dotted_i(self):
        differ = set()
        for char in map(chr, range(1, 128)):
            if char == "\n":
                continue
            for ignore_case in (True, False):
                old = re.compile(re.escape(char), re.IGNORECASE if ignore_case else 0)
                new = rx.compile(rx.escape(char), rx.IGNORECASE if ignore_case else 0, cache_pattern=False)
                spans = ({m.span() for m in old.finditer(self.text)},
                         {m.span() for m in new.finditer(self.text, concurrent=True)})
                differ |= {(char, ignore_case, self.text[start:end]) for start, end in spans[0] ^ spans[1]}
        # stdlib re's matches, not the module's: what EngineScan hands to stdlib re in a document holding them
        self.assertEqual(differ, {("I", True, DOTTED_I[0]), ("i", True, DOTTED_I[1])}, self.versions)

    def test_no_ascii_literal_matches_a_character_that_folds_to_several(self):
        # both fold one character to one (the module folds fully only with FULLCASE): the characters Unicode folds
        # to several (U+00DF to ss, U+FB03 to ffi, U+FB06 to st) match none of their ASCII folds
        several = [c for c in self.points if max(len(c.casefold()), len(c.lower()), len(c.upper())) > 1]
        text = "\n".join(several)
        folds = {g for c in several for f in (c.casefold(), c.lower(), c.upper())
                 for g in (f, f.lower(), f.upper(), f.title()) if len(g) > 1 and g.isascii()}
        self.assertTrue({"ss", "SS", "ffi", "st"} <= folds, sorted(folds))
        for fold in sorted(folds):
            for ignore_case in (True, False):
                with self.subTest(fold=fold, ignore_case=ignore_case):
                    old = re.compile(re.escape(fold), re.IGNORECASE if ignore_case else 0)
                    new = rx.compile(rx.escape(fold), rx.IGNORECASE if ignore_case else 0, cache_pattern=False)
                    self.assertEqual([m.span() for m in new.finditer(text, concurrent=True)],
                                     [m.span() for m in old.finditer(text)], self.versions)

    def test_every_code_point_escaped_by_the_module_matches_itself(self):
        # a case-sensitive literal with any character runs on the module (EngineScan): its escape must hold
        body = "".join(self.points)
        found = rx.compile(rx.escape(body), 0, cache_pattern=False).fullmatch(body, concurrent=True)
        self.assertIsNotNone(found, self.versions)


class EngineBudgetTests(unittest.TestCase):
    """The budget and the scan order are the default grep's: on a clock that moves at every look, the two scans
    stop at the same document and match, and a literal that may not reach every document visits them in
    scan_order's order."""

    @classmethod
    def setUpClass(cls):
        cls.enterClassContext(mock.patch.object(reader_module, "SCAN_CHARS_PER_SECOND", 0))     # scan_order runs
        cls.tmp = tempfile.TemporaryDirectory()
        cls.paths = build_root(Path(cls.tmp.name))
        add_fixture_documents(cls.paths)
        index_root(cls.paths)
        cls.reader = RootReader(cls.paths)
        cls.docs = list(cls.reader.docs.values())

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def grep(self, pattern, engine, *, step=0.0, per_file=0.0, budget=3.0, regex=False, max_matches=50):
        """grep on a clock that moves `step` at every look and `per_file` at every document read; (matches,
        truncated, the documents read, the documents scan_order was asked to order)."""
        clock = SimpleNamespace(now=0.0)
        visited, ordered = [], []
        real_file, real_order = RootReader.file, RootReader.scan_order

        def monotonic():
            clock.now += step
            return clock.now

        def file(reader, doc):
            clock.now += per_file
            visited.append(doc.doc_key)
            return real_file(reader, doc)

        def scan_order(reader, pattern_, docs, seconds):
            ordered.append(([d.doc_key for d in docs], seconds))
            return real_order(reader, pattern_, docs, seconds)

        with mock.patch.object(RootReader, "file", file), mock.patch.object(RootReader, "scan_order", scan_order), \
                mock.patch.object(reader_module, "time", SimpleNamespace(monotonic=monotonic)):
            matches, truncated = self.reader.grep(pattern, regex=regex, docs=self.docs, max_matches=max_matches,
                                                  ignore_case=True, budget_seconds=budget,
                                                  **({"regex_engine": True} if engine else {}))
        return matches, truncated, visited, ordered

    def test_a_budget_that_reaches_one_document_reads_the_same_one_first(self):
        # the audit's false "no matches", as test_grep_order: scan_order puts ise31-compat first
        for pattern in ("RADIUS DTLS server", "Validate ExtendedKeyUsage", "interface"):
            with self.subTest(pattern=pattern):
                old = self.grep(pattern, False, per_file=1.0, budget=1.5)
                new = self.grep(pattern, True, per_file=1.0, budget=1.5)
                self.assertEqual(new, old)
                self.assertTrue(new[1])
        self.assertEqual(self.grep("RADIUS DTLS server", True, per_file=1.0, budget=1.5)[2][0], "ise31-compat")

    def test_a_clock_that_moves_at_every_look_stops_both_scans_at_the_same_match(self):
        for pattern in ("ospf neighbor", "Type-7", "interface", "zzz not there", "e"):
            for step in (0.05, 0.2, 0.7):
                with self.subTest(pattern=pattern, step=step):
                    old = self.grep(pattern, False, step=step)
                    new = self.grep(pattern, True, step=step)
                    self.assertEqual(new, old)
        self.assertTrue(self.grep("ospf neighbor", True, step=0.05, max_matches=200)[1])     # the budget did end it

    def test_scan_order_is_asked_alike_and_a_regex_is_not_ordered(self):
        for pattern in ("NSSA border router", "RADIUS DTLS server", "ospf"):
            with self.subTest(pattern=pattern):
                old, new = self.grep(pattern, False), self.grep(pattern, True)
                self.assertEqual(new, old)
                self.assertEqual(len(new[3]), 1)
                self.assertEqual(new[3][0][1], reader_module.SCAN_ORDER_SECONDS)
        self.assertEqual(self.grep(r"RADIUS\s+DTLS", True, regex=True)[3], [])

    def test_a_regex_keeps_its_timeout(self):
        # the fuzzy pattern of test_api_v2.test_grep_regex_is_bounded_in_time, on the engine
        started = time.monotonic()
        matches, truncated = self.reader.grep("(?:neighbor information use the show ip){e<=10}", regex=True,
                                              docs=self.docs, max_matches=50, ignore_case=True, budget_seconds=0.2,
                                              regex_engine=True)
        self.assertLess(time.monotonic() - started, 2)
        self.assertTrue(truncated)


class GilTests(unittest.TestCase):
    """What the engine is for. A thread beside a scan: stdlib re (a literal grep without the setting) keeps it
    waiting for the whole scan; the regex module matching a str does not (the module's default, which a regex grep
    already had; EngineScan asks for it with concurrent=True), so a literal on the engine no longer does either."""

    @staticmethod
    def text(chars):
        line = "abcdefghij" * 100 + "\n"                # none of the patterns in it
        return SimpleNamespace(text=line * (chars // len(line)), dotted_i=False)

    @staticmethod
    def longest_pause(scan):
        """The longest a busy Python thread beside scan() went without running, and how long scan() took."""
        stop = threading.Event()
        pause = [0.0]

        def side():
            last = time.perf_counter()
            while not stop.is_set():
                now = time.perf_counter()
                pause[0] = max(pause[0], now - last)
                last = now

        thread = threading.Thread(target=side)
        thread.start()
        time.sleep(0.05)
        pause[0] = 0.0
        started = time.perf_counter()
        scan()
        took = time.perf_counter() - started
        time.sleep(0.05)                                # the side thread sees the end of a pause, then stops
        stop.set()
        thread.join()
        return pause[0], took

    def test_a_literal_on_the_engine_no_longer_keeps_the_threads_beside_it_waiting(self):
        source = self.text(40_000_000)
        pattern = "zqxjv-not-there"
        held, took = self.longest_pause(
            lambda: list(re.compile(re.escape(pattern), re.IGNORECASE).finditer(source.text)))
        if took < 0.1:
            self.skipTest(f"stdlib re read 40 M characters in {took:.3f} s")
        self.assertGreater(held, took / 2)              # stdlib re held the GIL for the scan
        free, took_engine = self.longest_pause(
            lambda: list(EngineScan(pattern, regex=False, ignore_case=True).finditer(source, 0)))
        self.assertLess(took_engine, took / 2)           # 6-44x sooner where measured
        self.assertLess(free, held / 4, (free, held, took_engine, took))

    def test_a_regex_runs_without_the_gil_on_the_engine_as_it_did_before(self):
        source = self.text(40_000_000)
        pattern = "(?:x|y)zq"           # no literal prefix: the module reads every character
        compiled = rx.compile(pattern, rx.IGNORECASE, cache_pattern=False)
        held, took = self.longest_pause(lambda: list(compiled.finditer(source.text, concurrent=False)))
        if took < 0.05 or held < took / 2:
            self.skipTest(f"cannot see the GIL held here ({took:.3f} s, {held:.3f} s)")
        engine, _ = self.longest_pause(
            lambda: list(EngineScan(pattern, regex=True, ignore_case=True).finditer(source, 30.0)))
        default, _ = self.longest_pause(        # RootReader.grep without the setting: the module's default
            lambda: list(reader_module.compile_regex(pattern, True).finditer(source.text, timeout=30.0)))
        self.assertLess(engine, held / 4, (engine, held))
        self.assertLess(default, held / 4, (default, held))


def grep_tests():
    """(TestCase class, test names) of the endpoint tests that call /v2/grep."""
    out = []
    for cls in (test_api_v2.ApiV2Tests, test_grep_order.GrepEndpointTests):
        names = [n for n in unittest.TestLoader().getTestCaseNames(cls)
                 if "/v2/grep" in inspect.getsource(getattr(cls, n))]
        out.append((cls, names))
    return out


class EndpointTests(unittest.TestCase):
    """/v2/grep with the setting answers as without it; without it, the engine is never touched."""

    def run_tests(self, retrieval):
        answers, calls = [], []
        post, grep = TestClient.post, RootReader.grep

        def recording(client, url, *args, **kwargs):
            response = post(client, url, *args, **kwargs)
            if url == "/v2/grep":
                answers.append((kwargs.get("json"), response.status_code, response.json()))
            return response

        def spy(reader, pattern, **kwargs):
            calls.append(kwargs.get("regex_engine"))
            return grep(reader, pattern, **kwargs)

        suite = unittest.TestSuite(cls(name) for cls, names in grep_tests() for name in names)
        result = unittest.TestResult()
        with mock.patch.dict(test_api_v2.CONFIG, retrieval), mock.patch.object(TestClient, "post", recording), \
                mock.patch.object(RootReader, "grep", spy):
            suite.run(result)
        self.assertTrue(result.wasSuccessful(), [str(f[1]) for f in result.failures + result.errors])
        self.assertEqual(result.testsRun, suite.countTestCases())
        return answers, calls

    def test_the_answers_are_the_same_with_the_engine(self):
        self.assertGreaterEqual(sum(len(names) for _, names in grep_tests()), 6)
        before, calls = self.run_tests({})
        self.assertEqual(set(calls), {None})                    # grep() called as it always was
        after, calls = self.run_tests({"grep": {"regex_engine": True}})
        self.assertEqual(set(calls), {True})
        self.assertGreater(len(before), 15)
        self.assertEqual(after, before)
        explicit, calls = self.run_tests({"grep": {"regex_engine": False}})
        self.assertEqual((explicit, set(calls)), (before, {None}))

    def test_without_the_setting_the_engine_is_never_made(self):
        never = mock.Mock(side_effect=AssertionError("EngineScan made without the setting"))
        with mock.patch.object(EngineScan, "__init__", never):
            answers, _ = self.run_tests({})
        never.assert_not_called()
        self.assertTrue(answers)


class SettingsTests(unittest.TestCase):

    def test_grep_settings(self):
        self.assertEqual(api_v2.grep_settings({}), api_v2.GrepSettings(regex_engine=False))
        self.assertEqual(api_v2.grep_settings({"grep": {}}), api_v2.GrepSettings())
        self.assertEqual(api_v2.grep_settings({"grep": {"regex_engine": False}}).regex_engine, False)
        self.assertEqual(api_v2.grep_settings({"grep": {"regex_engine": True}}).regex_engine, True)
        for bad in ({"grep": True}, {"grep": []}, {"grep": {"regex_engine": "yes"}}, {"grep": {"regex_engine": 1}},
                    {"grep": {"regex_engine": None}}, {"grep": {"regex_engin": True}}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                api_v2.grep_settings(bad)
        repo = json.loads((Path(api.SERVING_CONFIG_PATH).parent / "retrieval.json").read_text(encoding="utf-8"))
        self.assertNotIn("grep", repo)                  # the repo default is off

    def test_without_the_regex_module_the_setting_is_refused_and_only_the_setting(self):
        with mock.patch.dict(sys.modules, {"regex": None}):
            with self.assertRaisesRegex(ValueError, "regex module is not installed"):
                api_v2.grep_settings({"grep": {"regex_engine": True}})
            self.assertFalse(api_v2.grep_settings({"grep": {"regex_engine": False}}).regex_engine)
            self.assertFalse(api_v2.grep_settings({}).regex_engine)


class StartTests(unittest.TestCase):
    """A value deep32-engine cannot take fails the start, not a request."""

    @staticmethod
    def start(retrieval):
        """Start deep32-engine on a tiny root with these retrieval settings and stop it: the ValueError its start
        raised, or None."""
        made = []
        real_init = RootClient.__init__

        def init(root, **kw):
            made.append(root)
            real_init(root, **kw)

        try:
            with mock.patch.object(RootClient, "__init__", init):
                RootClient(retrieval=retrieval).close()
        except ValueError as exc:
            for root in made:                   # what RootClient started before its client failed
                root.env.stop()
                root.tmp.cleanup()
            return exc
        return None

    def test_a_bad_value_or_a_missing_module_fails_the_start(self):
        for retrieval, why in (({"grep": {"regex_engine": "yes"}}, "must be true or false"),
                               ({"grep": {"regex_engin": True}}, "must be an object of")):
            with self.subTest(retrieval=retrieval):
                self.assertIn(why, str(self.start(retrieval)))
        with mock.patch.dict(sys.modules, {"regex": None}):
            self.assertIn("regex module is not installed", str(self.start({"grep": {"regex_engine": True}})))
            self.assertIsNone(self.start({}))           # without the setting it starts as before
        self.assertIsNone(self.start({"grep": {"regex_engine": True}}))


if __name__ == "__main__":
    unittest.main()
