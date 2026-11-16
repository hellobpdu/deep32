"""Brings three Library tests up to the u14 program they test: art_media_pick's three arguments, the article, main and Settings tab orders and test_cisco_cli's arguments (release specification section 3, disc 1 builds and tests).

LEAD transform for the lead's slot 990-999 (disc 1 v0 lab build, 2026-11-15: stage 50 first stopped at
"tests/test_input.c:341:36: error: too few arguments to function call, expected 3, have 2"; after 997 and 998 'gmake test'
still failed in test_input (five tab-order checks), test_settings (four) and test_cisco_cli ("public fixture .: E_INPUT cannot read .").
All three were stale before any transform: no copy of these tests on build-controller knows the u14 screens.)

What it changes
  1. library/tests/test_input.c. src/app/kind_mediaframe.c defines art_media_pick(e26_app *a, const char *id, int show):
     show = 1 forces the 'article.enlarge' action even below view 2; the test runs views 2 and 3 and checks that picking
     a figure keeps the view and the scroll, so 0, the non-forcing value that matches the old two-argument call, is the
     faithful update (art_media_pick(&fixture, "second") -> art_media_pick(&fixture, "second", 0)). u14's screens (screens/article.scr, focus.order; golden source
     lanes/u14-encarta-final) added the section menu button "section-menu" after Views on the article screen and the
     Home button "home" at the end of the main screen's order. The test's expected orders gain them, and its Tab walk
     takes 16 steps instead of 15 so that it still ends back on Views (cap-views); the Shift+Tab checks that follow
     then start from Views as before. The program is unchanged: its order is the one the u15 laptop uses.
  2. library/tests/test_settings.c. u14's Settings screen (screens/settings.scr, prop tab-order) has the Appearance
     theme stop "settings-theme" between Word Processor and OK; the test's expected order gains it.
  3. library/tests/test_cisco_cli.c. 'gmake test' runs every test as "test_x <root> [taxonomy]" (Makefile, test
     target); test_cisco_cli read every argument as a public article fixture, so it failed on "." and on the
     taxonomy. Its fixtures now start at argv[3], after the two arguments every test receives.
Usage
  apply(tree_dir): tree_dir is the build tree after transforms 101 to 998. Returns the changed files.
Exit
  Raises when a target file or text is missing or appears a different number of times: never a silent skip.
"""
import os

EDITS = {
    "library/tests/test_input.c": [
        ('art_media_pick(&fixture, "second");', 'art_media_pick(&fixture, "second", 0);', 1),
        ('"cap-views section btn-related outline text ebar areabar catfield find goback features tools info sec-prev sec-next"',
         '"cap-views section-menu section btn-related outline text ebar areabar catfield find goback features tools info sec-prev sec-next"', 1),
        ('"ebar areabar catfield find goback features tools info"',
         '"ebar areabar catfield find goback features tools info home"', 1),
        ("for (i = 0; i < 15; i++) {", "for (i = 0; i < 16; i++) {", 1),
        ('"cap-views section btn-related outline text ebar areabar catfield find features tools info sec-prev sec-next cap-views"',
         '"cap-views section-menu section btn-related outline text ebar areabar catfield find features tools info sec-prev sec-next cap-views"', 1),
    ],
    "library/tests/test_settings.c": [
        ('\t\t"settings-ok", "settings-reset", "settings-close"',
         '\t\t"settings-theme", "settings-ok", "settings-reset", "settings-close"', 1),
    ],
    "library/tests/test_cisco_cli.c": [
        ("\tfor (i = 1; i < argc; i++) public_article(argv[i], &metrics);",
         "\t/* argv[1] is the tree's root and argv[2] the taxonomy, as every test gets them (gmake test); fixtures follow */\n"
         "\tfor (i = 3; i < argc; i++) public_article(argv[i], &metrics);", 1),
    ],
}


def apply(tree_dir):
    changed = []
    for rel in sorted(EDITS):
        path = os.path.join(tree_dir, *rel.split("/"))
        if not os.path.isfile(path):
            raise FileNotFoundError("999: missing " + rel)
        with open(path, "r", encoding="utf-8", newline="") as f:
            text = f.read()
        for old, new, count in EDITS[rel]:
            n = text.count(old)
            if n != count:
                raise ValueError("999: %s: expected %d occurrence(s) of %r, found %d" % (rel, count, old[:60], n))
            text = text.replace(old, new)
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        changed.append(rel)
    return changed
