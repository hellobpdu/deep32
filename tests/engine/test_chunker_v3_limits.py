import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import itertools
import unittest

from rag_core.chunker import Unit, build_drafts, estimate_tokens

_ids = itertools.count(1)

OSPF = ("IP Routing", "OSPF")
V3 = dict(policy="v3", target_tokens=100, hard_tokens=150, overlap_tokens=20, min_tokens=40,
          atomic_hard_tokens=300)
V2 = dict(policy="v2", target_tokens=100, hard_tokens=150, overlap_tokens=20, atomic_hard_tokens=300)


def unit(text, path=OSPF, kind="prose", atomic=False):
    return Unit(kind=kind, text=text, page=1, block_id=f"p0001-b{next(_ids):04d}",
                heading_path=tuple(path), atomic=atomic)


def words(n, tag):
    return " ".join(f"{tag}{i}" for i in range(n))


# 25 lines x 10 tokens: above hard_tokens (150), within atomic_hard_tokens (300)
CODE_LINES = [f"Router# show ip ospf database line {i} ! ok" for i in range(25)]
CODE = "\n".join(CODE_LINES)


class HardCapTests(unittest.TestCase):
    """root-v3 (chunker v3) had 35 chunks above hard_tokens: 34 were one code block of 950-1,800
    tokens (under atomic_hard_tokens, which v3 still applied) and one was a 4,031-token run without
    whitespace that the word splitter could not cut."""

    def test_v3_splits_an_atomic_unit_larger_than_hard_tokens_at_line_boundaries(self):
        self.assertTrue(150 < estimate_tokens(CODE) <= 300)
        drafts = build_drafts([unit(words(30, "p")), unit(CODE, kind="code", atomic=True),
                               unit(words(30, "q"))], **V3)
        self.assertLessEqual(max(d.tokens for d in drafts), 150)
        joined = "\n".join(d.text for d in drafts)
        for line in CODE_LINES:
            self.assertIn(line, joined)

    def test_v2_still_keeps_the_atomic_unit_whole(self):
        drafts = build_drafts([unit(words(30, "p")), unit(CODE, kind="code", atomic=True)], **V2)
        self.assertIn(CODE, [d.text for d in drafts])

    def test_a_run_without_whitespace_is_cut_to_the_cap(self):
        blob = "=" * 700                                  # 700 tokens, no whitespace
        for params, cap in ((V3, 150), (V2, 300)):
            with self.subTest(policy=params["policy"]):
                drafts = build_drafts([unit(blob, kind="code", atomic=True)], **params)
                self.assertLessEqual(max(d.tokens for d in drafts), cap)
                self.assertEqual("".join(d.text for d in drafts), blob)
        drafts = build_drafts([unit("Divider: " + "─" * 400)], **{**V2, "overlap_tokens": 0})
        self.assertLessEqual(max(d.tokens for d in drafts), 150)
        self.assertEqual("".join(d.text for d in drafts).count("─"), 400)

    def test_no_v3_draft_exceeds_hard_tokens(self):
        # the post-pass compared prev + draft without the synthetic heading it adds (152-153 > 150)
        for s, b, c in itertools.product((5, 10, 20, 35), range(90, 151, 2), (0, 30, 120)):
            units = [unit(words(s, "s"), OSPF + ("Small",)), unit(words(b, "b"), OSPF + ("Big",))]
            if c:
                units.append(unit(words(c, "c"), OSPF + ("Big",)))
            drafts = build_drafts(units, **V3)
            self.assertLessEqual(max(d.tokens for d in drafts), 150, (s, b, c))
            joined = " ".join(d.text for d in drafts).split()
            for u in units:
                self.assertTrue(set(u.text.split()) <= set(joined), (s, b, c))


if __name__ == "__main__":
    unittest.main()
