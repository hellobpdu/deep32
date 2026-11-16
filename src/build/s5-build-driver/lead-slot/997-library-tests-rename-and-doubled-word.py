"""Carries the Encarta to Deep32 Library rename into the Library's tests and fixes the doubled word it left (release specification section 12, other companies' names and marks).

LEAD transform for the lead's slot 990-999 (disc 1 v0 lab build, 2026-11-15: stage 50's 'gmake test' failed in 13
of 26 suites, among them notices compared against "... in a later build of Encarta 26.", LIBRARY.INI read back as
[Encarta], and encarta: URIs offered to a program that now takes library:).

What it changes
  1. The tests. Transform 603 renamed the program and moved library/tests/ but left the tests' text as it was,
     so they compare the renamed program with the old strings. The same rules as 603, in the same order
     (RULES below is 603's list verbatim), are applied to the test sources, then the doubled word of part 2.
     Each file's count of each rule is fixed (measured 2026-11-15 on the tree after 101 to 996). The fixture
     data under tests/fixtures/ (.art, .jsonl, .svg, .pdf, .sqlite) are hashed and are not touched.
  2. The program. 603's rule "Encarta 26" -> "Deep32 Library" met "Encarta 26 Library" and its rule
     "Encarta" -> "Library" met "Encarta Library text", leaving "Deep32 Library Library" and "Library Library
     text". Both reach the reader: every copied citation from Library text ended "Deep32 Library Library: not
     verified evidence." (cite.h CITE_TRAILER) and the glossary card said "Library Library text (not
     evidence)." (article_features.c). They become "Deep32 Library" and "Deep32 Library text", in the program
     and in its comments.
Usage
  apply(tree_dir): tree_dir is the build tree after transforms 101 to 996. Returns the changed files.
Exit
  Raises when a target file is missing, a rule's count differs, or a doubled word or an Encarta name is left in
  a changed file: never a silent skip.
"""
import os
import re

RULES = [
    ("Encarta 26's", "Encarta[ -]'?26's", "Deep32 Library's"),
    ('Encarta 26', "Encarta[ -]'?26(?![0-9])", 'Deep32 Library'),
    ("ENCARTA '26", "ENCARTA '?26(?![0-9])", 'DEEP32 LIBRARY'),
    ("Encarta 9x's", "Encarta '?9[5-9](?:/9[6-9])*'s", "the period design's"),
    ('Encarta 9x', "Encarta '?9[5-9](?:/9[6-9])*(?![0-9])", 'the period design'),
    ('encarta9x', 'encarta(9[5-9]+)', 'period\\1'),
    ('ENCARTA9x', 'ENCARTA(9[5-9])', 'PERIOD\\1'),
    ('Encarta26', 'Encarta26', 'Deep32Library'),
    ('ENCARTA26', 'ENCARTA26', 'DEEP32LIBRARY'),
    ('encarta26', 'encarta26', 'd32library'),
    ('encarta-ask', 'encarta-ask', 'd32library-ask'),
    ('encarta-categories', 'encarta-categories', 'library-categories'),
    ('action Encarta', "'Encarta'", "'Deep32Library'"),
    ('EncartaX', 'Encarta(?=[A-Z])', 'Deep32Library'),
    ('Encarta:', '"Encarta:"', '"Library:"'),
    ("Encarta's", "Encarta's", "the Library's"),
    ('ENCARTA', 'ENCARTA', 'LIBRARY'),
    ('Encarta', 'Encarta', 'Library'),
    ('encarta', 'encarta', 'library'),
    ('the the', '([Tt])he the period design', '\\1he period design'),
]

FIX = [
    ("doubled", "Deep32 Library Library", "Deep32 Library"),
    ("doubled text", "Library Library text", "Deep32 Library text"),
]

TESTS = {
    'library/tests/dummy-room.sh': {'Encarta 26': 1, 'encarta26': 3},
    'library/tests/fixtures/canvas/canvasdemo.scr': {'encarta26': 1},
    'library/tests/fixtures/input/fieldtest.scr': {"Encarta's": 1},
    'library/tests/test_canvas.c': {'Encarta 26': 3},
    'library/tests/test_chrome.c': {"Encarta's": 2, 'Encarta': 1},
    'library/tests/test_core.c': {'encarta26': 1, 'encarta-ask': 5, 'ENCARTA': 6, 'Encarta': 5},
    'library/tests/test_image.c': {'Encarta 26': 1, 'doubled': 1},
    'library/tests/test_input.c': {'Encarta 26': 12, 'doubled': 6},
    'library/tests/test_layout.c': {"Encarta 9x's": 1},
    'library/tests/test_lib.c': {'encarta26': 3},
    'library/tests/test_openat.c': {'encarta26': 11, 'ENCARTA': 2, 'Encarta': 2, 'encarta': 116},
    'library/tests/test_places.c': {'ENCARTA': 1, 'encarta': 2},
    'library/tests/test_registry.c': {'Encarta 26': 1, 'encarta26': 1},
    'library/tests/test_settings.c': {'Encarta 26': 1, 'encarta26': 1, 'ENCARTA': 2, 'Encarta': 31, 'encarta': 12},
    'library/tests/test_sharp.c': {'ENCARTA': 1},
    'library/tests/test_sound.c': {'Encarta 9x': 1},
    'library/tests/test_title.c': {'Encarta': 3},
    'library/tests/test_unit.c': {'Encarta 26': 1, 'encarta26': 3, "Encarta's": 1},
    'library/tests/test_wordproc.c': {'Encarta 26': 2, 'ENCARTA': 1, 'Encarta': 15},
}
PROGRAM = {
    'library/src/app/article_features.c': {'doubled text': 1},
    'library/src/app/cite.c': {'doubled': 1},
    'library/src/app/cite.h': {'doubled': 2, 'doubled text': 1},
    'library/src/app/edit.h': {'doubled text': 1},
    'library/src/app/kind_edit.c': {'doubled text': 1},
}


def _apply_rules(tree_dir, table, rules):
    changed = []
    for rel in sorted(table):
        path = os.path.join(tree_dir, *rel.split("/"))
        if not os.path.isfile(path):
            raise FileNotFoundError("997: missing " + rel)
        with open(path, "rb") as f:
            text = f.read().decode("utf-8")
        for name, pat, rep in rules:
            want = table[rel].get(name, 0)
            got = len(re.findall(pat, text))
            if got != want:
                raise ValueError("997: %s: rule %r expected %d, found %d" % (rel, name, want, got))
            if want:
                text = re.sub(pat, rep, text)
        if re.search(r"Library Library|LIBRARY LIBRARY|[Ee]ncarta|ENCARTA", text):
            raise ValueError("997: %s: a doubled word or an Encarta name is left" % rel)
        with open(path, "wb") as f:
            f.write(text.encode("utf-8"))
        changed.append(rel)
    return changed


def apply(tree_dir):
    return sorted(_apply_rules(tree_dir, TESTS, RULES + FIX) + _apply_rules(tree_dir, PROGRAM, FIX))
