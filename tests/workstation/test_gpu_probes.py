"""The Forge identity probe file: the Workstation's copy, the model-bridge image's copy, and the long inputs.

GPU_EXT_PROBE_ALWAYS reads the file's "long" object; record_gpu_probes.py fills its recorded values on a Forge.
"""
import json
import math
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
WORKSTATION = ROOT / "src/workstation/ship/u4/etc/d32/gpu-probes/rerank.json"
IMAGE = ROOT / "src/model-bridge/gpu-probes.json"


class ProbeFileTests(unittest.TestCase):
    def test_probe_file_twins_are_identical(self):
        self.assertEqual(WORKSTATION.read_bytes(), IMAGE.read_bytes())

    def test_long_probe_inputs(self):
        raw = WORKSTATION.read_bytes()
        data = json.loads(raw)
        # the form the recorder writes: one-space indent, ASCII, LF, a final newline
        self.assertEqual((json.dumps(data, indent=1, ensure_ascii=True) + "\n").encode("ascii"), raw)
        rerank, embed = data["long"]["rerank"], data["long"]["embed"]
        documents = rerank["documents"]
        self.assertEqual(len(documents), 15)
        self.assertTrue(all(isinstance(d, str) and 0 < len(d) <= 1600 for d in documents))
        self.assertTrue(isinstance(rerank["query"], str) and rerank["query"])
        self.assertTrue(isinstance(embed["input"], str) and embed["input"])
        self.assertEqual(rerank["model"], data["model"])
        self.assertEqual(embed["model"], data["embed"]["model"])
        self.assertEqual(rerank["tolerance"], 0.005)
        self.assertEqual(embed["min_cosine"], 0.999)
        if "scores" in rerank:
            self.assertEqual(sorted(rerank["order"]), list(range(15)))
            self.assertEqual(len(rerank["scores"]), 15)
            self.assertTrue(all(isinstance(s, (int, float)) and math.isfinite(s) for s in rerank["scores"]))
        if "vector" in embed:
            self.assertEqual(len(embed["vector"]), 1024)
            self.assertTrue(all(isinstance(v, (int, float)) and math.isfinite(v) for v in embed["vector"]))


if __name__ == "__main__":
    unittest.main()
