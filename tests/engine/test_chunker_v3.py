import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import os
import unittest

from rag_core.chunker import (
    Unit,
    build_drafts,
    chunker_policy,
)


def unit(text, path, kind="prose", atomic=False, n=[0]):  # noqa: B006 (a deliberate counter shared by every call)
    n[0] += 1
    return Unit(
        kind=kind,
        text=text,
        page=1,
        block_id=f"p0001-b{n[0]:04d}",
        heading_path=tuple(path),
        atomic=atomic,
    )


def words(n, tag):
    return " ".join(f"{tag}{i}" for i in range(n))


V3 = dict(policy="v3", target_tokens=100, hard_tokens=150, overlap_tokens=20, min_tokens=40)


class ContextMergeChunkerTests(unittest.TestCase):

    def test_cli_stays_with_its_explanation(self):
        path = ["IP Routing", "Configuring BFD"]
        drafts = build_drafts([
            unit("Enables privileged EXEC mode.", path),
            unit("Device> enable", path, kind="cli-config", atomic=True),
            unit("Enters global configuration mode.", path),
            unit("Device# configure terminal", path, kind="cli-config", atomic=True),
        ], **V3)
        self.assertEqual(len(drafts), 1)
        self.assertIn("Enables privileged EXEC mode.", drafts[0].text)
        self.assertIn("Device> enable", drafts[0].text)
        self.assertIn("Device# configure terminal", drafts[0].text)

    def test_v2_default_still_isolates_atomic_units(self):
        path = ["IP Routing", "Configuring BFD"]
        units = [
            unit("Enables privileged EXEC mode.", path),
            unit("Device> enable", path, kind="cli-config", atomic=True),
        ]
        drafts = build_drafts(units, target_tokens=100, hard_tokens=150, overlap_tokens=20)
        self.assertTrue(any(d.atomic and d.text == "Device> enable" for d in drafts))

    def test_small_sibling_sections_merge_under_parent_with_headings(self):
        drafts = build_drafts([
            unit("OSPF intro sentence.", ["OSPF", "Information About OSPF"]),
            unit("Stub areas restrict LSA types.", ["OSPF", "Restrictions for OSPF"]),
            unit("NSSA allows type 7 LSAs.", ["OSPF", "NSSA"]),
        ], **V3)
        self.assertEqual(len(drafts), 1)
        self.assertEqual(drafts[0].heading_path, ("OSPF",))
        self.assertIn("## Restrictions for OSPF", drafts[0].text)
        self.assertIn("## NSSA", drafts[0].text)

    def test_large_sections_keep_their_boundaries(self):
        drafts = build_drafts([
            unit(words(60, "a"), ["OSPF", "Areas"]),
            unit(words(60, "b"), ["OSPF", "Timers"]),
        ], **V3)
        self.assertEqual([d.heading_path for d in drafts], [("OSPF", "Areas"), ("OSPF", "Timers")])

    def test_top_level_sections_never_merge(self):
        drafts = build_drafts([
            unit("Short OSPF note.", ["OSPF"]),
            unit("Short BGP note.", ["BGP"]),
        ], **V3)
        self.assertEqual(len(drafts), 2)

    def test_no_overlap_only_drafts(self):
        path_a, path_b = ["OSPF", "Areas"], ["OSPF", "Timers"]
        units = [unit(words(45, f"a{i}_"), path_a) for i in range(4)]
        units.append(unit(words(60, "b"), path_b))
        drafts = build_drafts(units, **V3)
        texts = [d.text for d in drafts]
        for t in texts:
            self.assertFalse(
                sum(t in other for other in texts) > 1,
                "a draft is wholly contained in another (overlap-only draft)",
            )
        self.assertTrue(any("b0" in t for t in texts))

    def test_every_source_unit_is_kept(self):
        units = [
            unit("Alpha intro.", ["A", "one"]),
            unit("show ip ospf neighbor", ["A", "one"], kind="cli-config", atomic=True),
            unit(words(70, "x"), ["A", "two"]),
            unit("Beta tail.", ["A", "three"]),
            unit(words(30, "y"), ["B"]),
        ]
        joined = "\n".join(d.text for d in build_drafts(units, **V3))
        for u in units:
            self.assertIn(u.text, joined)

    def test_policy_selection(self):
        old = os.environ.pop("RAG_CHUNKER_POLICY", None)
        try:
            self.assertEqual(chunker_policy(), "v2")
            os.environ["RAG_CHUNKER_POLICY"] = "v3"
            self.assertEqual(chunker_policy(), "v3")
            with self.assertRaises(ValueError):
                chunker_policy("v9")
        finally:
            os.environ.pop("RAG_CHUNKER_POLICY", None)
            if old is not None:
                os.environ["RAG_CHUNKER_POLICY"] = old


if __name__ == "__main__":
    unittest.main()
