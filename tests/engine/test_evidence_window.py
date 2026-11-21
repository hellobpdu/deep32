"""Evidence windows prepared from the lines near the passage (reader.near_window) are byte-identical to the
windows prepared from the whole range (the code before), on random sources with blank runs, RFC page furniture,
long lines and sections of every size. SourceFiles load outside the reader lock; preload reads them all."""
import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import random
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from rag_core import reader as reader_mod
from rag_core.reader import Doc, RootReader, Sec, SourceFile, best_line, cut_line

WORDS = ("ospf nssa translator type-7 lsa p-bit forwarding address area border router bgp route reflector "
         "cluster originator eigrp stub feasible successor the of and is to").split()
FOOTER = "Murphy                      Standards Track                     [Page {n}]"
HEADER = "RFC 3101                    OSPF NSSA Option                January 2003"


def old_window(pairs, centre, max_chars, terms):
    """reader.window before the change, verbatim."""
    lo = hi = centre
    text = pairs[centre][1]
    if len(text) > max_chars:
        return cut_line(text, terms, max_chars), pairs[centre][0], pairs[centre][0]
    size = len(text)
    grew = True
    while grew:
        grew = False
        for side in (1, -1):
            k = hi + 1 if side == 1 else lo - 1
            if 0 <= k < len(pairs) and size + 1 + len(pairs[k][1]) <= max_chars:
                size += 1 + len(pairs[k][1])
                hi, lo = (k, lo) if side == 1 else (hi, k)
                grew = True
    while lo < hi and not pairs[lo][1]:
        lo += 1
    while hi > lo and not pairs[hi][1]:
        hi -= 1
    body = "\n".join(t for _, t in pairs[lo:hi + 1])
    return body, pairs[lo][0], pairs[hi][0]


def old_evidence(source, doc, span, terms, max_chars):
    """RootReader.evidence before the change, verbatim (with old_window)."""
    pairs = source.shown(*span)
    best = best_line(pairs, terms)
    centre_line = pairs[best][0] if best is not None else span[0]
    sec = doc.section_at(centre_line)
    lo = span[0] if best is None else max(
        centre_line - max_chars, min(span[0], sec.line_start if sec else span[0])
    )
    hi = min(centre_line + max_chars, max(span[1], sec.line_end if sec else span[1]))
    around = source.shown(lo, hi)
    if not around:
        return {"text": "", "section": sec, "local": doc.local(*span)}
    centre = next((i for i, (line, _) in enumerate(around) if line >= centre_line), 0)
    text, first, last = old_window(around, centre, max_chars, terms)
    return {"text": text, "section": sec, "local": doc.local(first, last)}


def random_source(rng: random.Random, n_lines: int, rfc: bool) -> list[str]:
    lines, page = [], 1
    while len(lines) < n_lines:
        r = rng.random()
        if rfc and r < 0.02:
            lines += [FOOTER.format(n=page), "\f" + HEADER, "", ""]
            page += 1
        elif r < 0.25:
            lines += [""] * rng.choice((1, 1, 2, 3, 6))
        elif r < 0.28:
            lines.append(" ".join(rng.choice(WORDS) for _ in range(rng.randint(60, 700))))    # a long line
        elif r < 0.30:
            lines.append("   \t  ")
        else:
            lines.append("   " + " ".join(rng.choice(WORDS) for _ in range(rng.randint(1, 14))))
    return lines[:n_lines]


def make_doc(rng: random.Random, path: Path, n_lines: int, rfc: bool) -> Doc:
    doc = Doc("d1", "rfc3101" if rfc else "guide", "rfc" if rfc else "cisco", "T", None, None, None, path)
    starts = sorted({1, *rng.sample(range(2, n_lines), k=min(n_lines - 2, rng.choice((0, 1, 3, 12, 40))))})
    for i, start in enumerate(starts):
        end = (starts[i + 1] - 1) if i + 1 < len(starts) else n_lines
        sec = Sec(f"k#{i}", doc.doc_key, i, 1, str(i), f"H{i}", None, start, end, 0)
        doc.sections.append(sec)
        doc.starts.append(start)
    return doc


def bare_reader() -> RootReader:
    r = RootReader.__new__(RootReader)
    r._files, r._lock = {}, threading.Lock()
    return r


class NearWindowTests(unittest.TestCase):

    def test_windows_are_byte_identical_to_the_whole_range(self):
        rng = random.Random(20260928)
        near_used = checked = 0
        real_near = reader_mod.near_window

        def spy(*args):
            nonlocal near_used
            out = real_near(*args)
            near_used += out is not None
            return out

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(reader_mod, "near_window", side_effect=spy):
            for case in range(60):
                rfc = case % 2 == 0
                n_lines = rng.choice((30, 200, 1500, 6000))
                path = Path(tmp) / f"s{case}.txt"
                path.write_text("\n".join(random_source(rng, n_lines, rfc)) + rng.choice(("", "\n")),
                                encoding="utf-8")
                doc = make_doc(rng, path, n_lines, rfc)
                rd = bare_reader()
                source = rd.file(doc)
                for _ in range(40):
                    a = rng.randint(1, n_lines)
                    b = min(n_lines, a + rng.choice((0, 3, 20, 80)))
                    terms = rng.sample(WORDS, k=rng.choice((0, 1, 3)))
                    max_chars = rng.choice((200, 300, 1200, 2400, 4000))
                    got = rd.evidence(doc, (a, b), terms, max_chars)
                    want = old_evidence(source, doc, (a, b), terms, max_chars)
                    self.assertEqual(got, want, (case, a, b, terms, max_chars))
                    checked += 1
        self.assertEqual(checked, 2400)
        self.assertGreater(near_used, checked // 4)       # small sources need no near path

    def test_a_window_that_reaches_the_cut_widens_or_falls_back(self):
        # one section of 2,000 short lines: a 4000-char window of 2-char lines spans ~1,300 lines, past NEAR_LINES
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "short.txt"
            path.write_text("\n".join("ab" for _ in range(2000)), encoding="utf-8")
            doc = Doc("d", "short", "cisco", "T", None, None, None, path)
            doc.sections.append(Sec("short#1", "short", 0, 1, "1", "H", None, 1, 2000, 0))
            doc.starts.append(1)
            rd = bare_reader()
            got = rd.evidence(doc, (1000, 1000), ["ab"], 4000)
            self.assertEqual(got, old_evidence(rd.file(doc), doc, (1000, 1000), ["ab"], 4000))
            self.assertGreater(got["text"].count("\n"), 2 * reader_mod.NEAR_LINES)


class SourceFileLoadingTests(unittest.TestCase):

    def test_a_first_touch_does_not_hold_the_reader_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.txt"
            path.write_text("one\ntwo\n", encoding="utf-8")
            doc = Doc("d", "a", "cisco", "T", None, None, None, path)
            rd = bare_reader()
            seen = []

            class Probe(SourceFile):
                def __init__(self, *args):
                    seen.append(rd._lock.locked())
                    super().__init__(*args)

            with mock.patch.object(reader_mod, "SourceFile", Probe):
                first = rd.file(doc)
            self.assertEqual(seen, [False])
            self.assertIs(rd.file(doc), first)

    def test_preload_reads_every_source_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            rd = bare_reader()
            rd.docs = {}
            for key in ("a", "b", "c"):
                path = Path(tmp) / f"{key}.txt"
                path.write_text(f"{key}\n", encoding="utf-8")
                rd.docs[key] = Doc(key, key, "cisco", "T", None, None, None, path)
            rd.docs["gone"] = Doc("g", "gone", "cisco", "T", None, None, None, Path(tmp) / "missing.txt")
            rd.file(rd.docs["a"])
            self.assertEqual(rd.preload(), 2)
            self.assertEqual(sorted(rd._files), ["a", "b", "c"])
            stop = threading.Event()
            stop.set()
            rd._files.clear()
            self.assertEqual(rd.preload(stop=stop), 0)


if __name__ == "__main__":
    unittest.main()
