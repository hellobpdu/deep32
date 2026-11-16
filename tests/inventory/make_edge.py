"""Writes the edge-case inputs for run_tests.py's edge, class, guard and argument tests.

What it does:
  Creates TESTS_DIR/edge with
    facts-*.json   one machine's facts written in awkward forms: UTF-8 with a byte-order mark,
                   UTF-16 with a byte-order mark (what Windows PowerShell 5.1 writes with ">"),
                   CRLF line ends, raw UTF-8 text, numbers beyond 32 bits, a fractional thread
                   count, ports as strings; and shapes that must be refused (empty, not an
                   object, no "facts" object, wrong case, trailing text)
    class-*.json   the same machine with one card, processor or model name changed, and the
                   class the verdict must give it
    matrix-mini*.json  a tiny valid matrix, plain, with a byte-order mark and with CRLF
    guard-*.json   gaming-mode state files in every awkward form, with the answer expected
    dir with spaces/facts copy.json
  Everything is derived from fixtures/win-pro-rtx2080ti.json (a made-up machine), so it stays
  deterministic.

Usage:
  python3 -I make_edge.py TESTS_DIR   (normally: this folder)

Exit codes:
  0  written
  1  an error (Python prints the reason)
"""
import copy
import json
import os
import sys

T = sys.argv[1]
OUT = os.path.join(T, "edge")
BASE = json.load(open(os.path.join(T, "fixtures", "win-pro-rtx2080ti.json"), encoding="ascii"))["facts"]


def put(name, data):
    p = os.path.join(OUT, name)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    if isinstance(data, str):
        data = data.encode("ascii")
    with open(p, "wb") as fh:
        fh.write(data)


def doc(facts):
    return json.dumps({"facts": facts}, sort_keys=True)


def changed(path, value):
    f = copy.deepcopy(BASE)
    node = f
    parts = path.split(".")
    for k in parts[:-1]:
        node = node[int(k)] if isinstance(node, list) else node[k]
    last = parts[-1]
    if isinstance(node, list):
        node[int(last)] = value
    else:
        node[last] = value
    return f


plain = doc(BASE)
# accepted forms: both programs must give exit 0 and the same verdict
put("facts-plain.json", plain)
put("facts-bom8.json", b"\xef\xbb\xbf" + plain.encode("ascii"))
put("facts-utf16.json", b"\xff\xfe" + plain.encode("utf-16-le"))
put("facts-crlf.json", json.dumps({"facts": BASE}, indent=2, sort_keys=True).replace("\n", "\r\n"))
uni = changed("gpus.0.name", "NVIDIA GeForce RTX 2080 Ti\u2122 \u00e9dition")
uni["hw"]["model"] = "Mod\u00e8le \"quoted\" back\\slash \u00a8"
put("facts-unicode.json", json.dumps({"facts": uni}, ensure_ascii=False, sort_keys=True).encode("utf-8"))
big = changed("cpu.threads", 5000000000)
big["mem"]["installed_gb"] = 3000000000
put("facts-huge-numbers.json", doc(big))
put("facts-fraction-threads.json", doc(changed("cpu.threads", 12.9)))
put("facts-ports-strings.json", doc(changed("ports.busy", ["7452", "8443"])))
put("facts-gpus-object.json", doc(changed("gpus", BASE["gpus"][0])))
put(os.path.join("dir with spaces", "facts copy.json"), plain)
# refused shapes: both programs must give exit 1
put("bad-empty.json", "")
put("bad-whitespace.json", "  \r\n ")
put("bad-facts-array.json", json.dumps({"facts": []}))
put("bad-facts-string.json", json.dumps({"facts": "x"}))
put("bad-facts-null.json", json.dumps({"facts": None}))
put("bad-root-array.json", json.dumps([{"facts": BASE}], sort_keys=True))
put("bad-trailing-text.json", plain + " xyz")
put("bad-wrong-case.json", json.dumps({"Facts": BASE}, sort_keys=True))

# classes: the verdict must give each name its class (null = no class / no profile)
CLASSES = [
    ("gpu-rtx2050", "gpus.0.name", "NVIDIA GeForce RTX 2050", {"gpu": "rtx30"}),
    ("gpu-rtx2080ti", "gpus.0.name", "NVIDIA GeForce RTX 2080 Ti", {"gpu": "rtx20"}),
    ("gpu-rtx4000-sff-ada", "gpus.0.name", "NVIDIA RTX 4000 SFF Ada Generation", {"gpu": "rtx40"}),
    ("gpu-rtx2000e-ada", "gpus.0.name", "NVIDIA RTX 2000E Ada Generation", {"gpu": "rtx40"}),
    ("gpu-rtx-pro-blackwell", "gpus.0.name", "NVIDIA RTX PRO 6000 Blackwell Workstation Edition", {"gpu": "rtx50"}),
    ("gpu-gtx1660super", "gpus.0.name", "NVIDIA GeForce GTX 1660 SUPER", {"gpu": "gtx16"}),
    ("gpu-gtx680", "gpus.0.name", "NVIDIA GeForce GTX 680", {"gpu": "legacy"}),
    ("gpu-titan-black", "gpus.0.name", "NVIDIA GeForce GTX TITAN Black", {"gpu": "legacy"}),
    ("gpu-h20", "gpus.0.name", "NVIDIA H20", {"gpu": "hopper"}),
    ("gpu-l4", "gpus.0.name", "NVIDIA L4", {"gpu": "ada-dc"}),
    ("gpu-a10g", "gpus.0.name", "NVIDIA A10G", {"gpu": "ampere-dc86"}),
    ("gpu-v100", "gpus.0.name", "Tesla V100-PCIE-16GB", {"gpu": "legacy"}),
    ("cpu-i3-n305", "cpu.model", "Intel(R) Core(TM) i3-N305", {"cpu": "low-power"}),
    ("cpu-n100", "cpu.model", "Intel(R) N100", {"cpu": "low-power"}),
    ("cpu-i7-9700k", "cpu.model", "Intel(R) Core(TM) i7-9700K CPU @ 3.60GHz", {"cpu": "modern"}),
    ("hw-latitude-5430", "hw.model", "Latitude 5430", {"hw_profile": True}),
    ("hw-latitude-5430-rugged", "hw.model", "Latitude 5430 Rugged", {"hw_profile": False}),
]
for name, path, value, expect in CLASSES:
    f = changed(path, value)
    if path == "gpus.0.name":
        f["gpus"][0]["cc"] = None  # the class must come from the name alone
    if path == "hw.model":
        f["os"]["edition"] = "home"  # fewer candidates, so baremetal is among the three ranked when it matches
    put("class-%s.json" % name, json.dumps({"facts": f, "expect_class": expect}, sort_keys=True))

# a tiny valid matrix in three encodings
mini = {
    "schema": "deep32.matrix.v1", "matrix_version": "mini", "ports": [{"port": 7452, "use": "relay"}],
    "cpu_classes": [{"id": "any", "when": [["os.arch", "eq", "x64"]], "workstation": "yes", "forge": "yes"}],
    "gpu_classes": [], "gpu_mem_tiers": [], "hw_profiles": [], "warnings": [],
    "sizing": {"host_keeps": {"threads": 2, "mem_gb": 4},
               "tiers": [{"tier": 0, "min_threads": 0, "min_mem_gb": 0, "ws": {"threads": [4, 4], "mem_gb": [8, 8]}, "forge": None}],
               "ws_alone_min": {"threads": 4, "mem_gb": 8}, "forge_alone_min": {"threads": 2, "mem_gb": 12}},
    "requirements": {"relay-port": {"kind": "fix", "when": [["port.7452", "eq", False]], "need": "Port 7452 free"}},
    "profiles": [{"id": "mini", "name": "Mini", "target": "supported", "status": "planned", "rank": 1,
                  "match": [["os.family", "eq", "windows"]], "requires": ["relay-port"], "size": "ws",
                  "parts": {"workstation": "x", "forge": "y", "relay": "z"}}],
}
mtext = json.dumps(mini, indent=1, sort_keys=True)
put("matrix-mini.json", mtext)
put("matrix-mini-bom.json", b"\xef\xbb\xbf" + mtext.encode("ascii"))
put("matrix-mini-crlf.json", mtext.replace("\n", "\r\n"))

# gaming-mode state files and the answer each must give (clear = the bench may run)
GUARDS = [
    ("guard-off.json", b'{"mode": "off"}', "clear"),
    ("guard-on.json", b'{"mode": "on"}', "gaming-mode"),
    ("guard-on-upper.json", b'{"mode": "ON"}', "gaming-mode"),
    ("guard-on-ps-crlf.json", b'{\r\n    "mode":  "on",\r\n    "by":  "manual"\r\n}', "gaming-mode"),
    ("guard-on-bom8.json", b'\xef\xbb\xbf{"mode": "on"}', "gaming-mode"),
    ("guard-on-utf16.json", b"\xff\xfe" + '{"mode": "on"}'.encode("utf-16-le"), "gaming-mode"),
    ("guard-on-nested-off.json", b'{"mode": "on", "ready": {"mode": "off"}}', "gaming-mode"),
    ("guard-off-nested-on.json", b'{"ready": {"mode": "on"}, "mode": "off"}', "clear"),
    ("guard-empty.json", b"", "gaming-state-unreadable"),
    ("guard-object-no-mode.json", b"{}", "gaming-state-unreadable"),
    ("guard-mode-null.json", b'{"mode": null}', "gaming-state-unreadable"),
    ("guard-mode-bool.json", b'{"mode": true}', "gaming-state-unreadable"),
    ("guard-array.json", b'[{"mode": "on"}]', "gaming-state-unreadable"),
    ("guard-broken.json", b"{ this is not json", "gaming-state-unreadable"),
]
for name, data, _ in GUARDS:
    put(name, data)
put("guards.json", json.dumps({name: want for name, _, want in GUARDS}, indent=1, sort_keys=True))
print("edge inputs written to " + OUT)
