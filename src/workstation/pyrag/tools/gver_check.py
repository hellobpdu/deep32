"""G-ver (PYTHON-ON-OPENBSD 8.3, freeze part): a node venv's freeze against production's (H0) for the shipped set.

Usage: python -I gver_check.py PINS FREEZE H0
  PINS: locks/core.pins or locks/edge.pins (class column); FREEZE: the node venv's `pip list --format=freeze`;
  H0: production's freeze. Prints per shipped distribution: production version, node version, class; then what the
  node has beyond the pins (the edge's syspick packages). Exit 1 if any output-shaping package differs.
"""
import re
import sys


def norm(n):
    return re.sub(r"[-_.]+", "-", n).lower()


def read(path):
    out = {}
    for line in open(path, encoding="utf-8"):
        line = line.split("#", 1)[0].strip()
        if "==" in line:
            n, v = line.split()[0].split("==")
            out.setdefault(norm(n), v)
    return out


pins_path, freeze_path, h0_path = sys.argv[1:4]
classes = {}
for line in open(pins_path, encoding="utf-8"):
    line = line.split("#", 1)[0].split()
    if line and "==" in line[0]:
        classes[norm(line[0].split("==")[0])] = line[1]
node, prod = read(freeze_path), read(h0_path)
bad = 0
for name in sorted(set(classes) | set(node)):
    cls = classes.get(name, "not in pins")
    p, n = prod.get(name, "-"), node.get(name, "-")
    mark = "same" if p == n else "DIFF"
    if mark == "DIFF" and cls == "output-shaping":
        bad += 1
        mark = "DIFF (OUTPUT-SHAPING)"
    print(f"{name:28} prod {p:12} node {n:12} {cls:15} {mark}")
print(f"{len(classes)} pinned, {len(node)} in the node venv, output-shaping differences: {bad}")
sys.exit(1 if bad else 0)
