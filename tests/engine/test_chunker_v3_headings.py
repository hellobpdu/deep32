import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import itertools
import unittest

from rag_core.chunker import CHUNKER_VERSIONS, Unit, build_drafts

_ids = itertools.count(1)

OSPF = ("IP Routing", "OSPF")
V3 = dict(policy="v3", target_tokens=100, hard_tokens=150, overlap_tokens=20, min_tokens=40)


def unit(text, path=OSPF, kind="prose"):
    return Unit(kind=kind, text=text, page=1, block_id=f"p0001-b{next(_ids):04d}", heading_path=tuple(path))


def words(n, tag):
    return " ".join(f"{tag}{i}" for i in range(n))


def headings(draft):
    return [u.text for u in draft.units if u.kind == "heading" and u.text.strip()]


def ends_with_heading(draft):
    units = [u for u in draft.units if u.text.strip()]
    return bool(units) and units[-1].kind == "heading"


class SyntheticHeadingTests(unittest.TestCase):
    """Audit probes on an earlier chunker: a small section absorbed the next one's heading and was then flushed
    (a micro-chunk ending in a dangling heading), and the post-pass added that heading a second time."""

    def test_audit_probe_has_no_duplicate_or_dangling_heading(self):
        drafts = build_drafts([unit(words(10, "s"), OSPF + ("Small",)), unit(words(95, "b"), OSPF + ("Big",)),
                               unit(words(30, "c"), OSPF + ("Big",))], **V3)
        for d in drafts:
            self.assertEqual(headings(d).count("### Big"), d.text.count("### Big"))
            self.assertLessEqual(d.text.count("### Big"), 1, d.text)
            self.assertFalse(ends_with_heading(d), d.text[-40:])

    def test_no_micro_chunk_ending_in_the_next_sections_heading(self):
        drafts = build_drafts([unit(words(10, "s"), OSPF + ("Small",)), unit(words(138, "b"), OSPF + ("Big",))],
                              **V3)
        self.assertFalse(any(d.text.endswith("## Big") for d in drafts), [d.text[-30:] for d in drafts])

    def test_sweep_no_dangling_or_repeated_headings(self):
        for s, b, c in itertools.product((5, 10, 20, 35), range(60, 151, 3), (0, 10, 30, 120)):
            units = [unit(words(s, "s"), OSPF + ("Small",)), unit(words(b, "b"), OSPF + ("Big",))]
            if c:
                units.append(unit(words(c, "c"), OSPF + ("Big",)))
            for d in build_drafts(units, **V3):
                self.assertFalse(ends_with_heading(d), (s, b, c))
                self.assertEqual(len(headings(d)), len(set(headings(d))), (s, b, c))

    def test_absorbing_a_small_section_still_keeps_its_heading(self):
        drafts = build_drafts([unit(words(10, "s"), OSPF + ("Small",)), unit(words(40, "b"), OSPF + ("Big",))],
                              **V3)
        self.assertEqual(len(drafts), 1)
        self.assertEqual(drafts[0].heading_path, OSPF)
        self.assertIn("### Big\n\nb0", drafts[0].text)

    def test_merging_a_small_tail_does_not_repeat_the_overlap(self):
        # the tail draft starts with an overlap excerpt of prev's last unit; merged back, it duplicated it
        drafts = build_drafts([unit(words(45, "a")), unit(words(45, "b")), unit(words(15, "c"))], **V3)
        for d in drafts:
            tokens = d.text.split()
            self.assertEqual(len(tokens), len(set(tokens)), d.text)
        self.assertEqual(" ".join(d.text for d in drafts).split().count("c0"), 1)

    def test_version_records_the_changed_output(self):
        self.assertEqual(CHUNKER_VERSIONS["v3"], "17c-v3.1")
        self.assertEqual(CHUNKER_VERSIONS["v2"], "17c-v2")


if __name__ == "__main__":
    unittest.main()
