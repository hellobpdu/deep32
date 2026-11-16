#!/usr/bin/env python3
"""c6.py: reads contract C6 status lines out of a serial console log and says, per stage, pass, fail or not seen.

What
  Contract C6 (src/docs/contracts/C6-console-status.md): "D32S/1 <seq> <node> key=value ... [msg=<text>]", ASCII,
  at most 512 bytes; a reader ignores every other line, keeps the highest seq for each boot and treats a new boot as
  a restart. Stages in order: install, firstboot, keys, signin, tailnet, content, import, pairing, ready.
  A stage passes when a line says state=done for it, or when a later stage of the same boot was reached; it fails
  on state=failed (with its error code and msg). A stage waiting for a person (signin, pairing) counts as reached
  when its line says state=waiting and --waiting-ok names it.
  Before transform 901 (T-0113) the medium writes no C6 line; --legacy reads the old markers instead: the
  installer's "CONGRATULATIONS!" line and the halt after the install (install), the site hooks' "D32: " lines and
  no "D32: fail" (firstboot), a "login:" prompt (ready).
Usage
  python -I -B c6.py LOG [LOG ...] [--need install,firstboot,...] [--waiting-ok signin,pairing] [--legacy] [--json OUT]
Exit
  0 every needed stage passed; 1 a needed stage failed or was not seen; 2 bad usage or an unreadable log.
"""
import argparse
import json
import re
import sys

STAGES = ("install", "firstboot", "keys", "signin", "tailnet", "content", "import", "pairing", "ready")
LINE = re.compile(r"D32S/1 ([0-9]+) ([a-z]+) (.*)$")


def parse_line(text):
    """(seq, node, {key: value}) of a C6 line, or None."""
    m = LINE.search(text)
    if not m or len(text.encode("ascii", "replace")) > 600:
        return None
    seq, node, rest = int(m.group(1)), m.group(2), m.group(3)
    kv = {}
    msg = None
    if " msg=" in " " + rest:
        i = (" " + rest).index(" msg=")
        msg = (" " + rest)[i + 5:]
        rest = (" " + rest)[:i].strip()
    for tok in rest.split():
        if "=" not in tok:
            return None
        k, v = tok.split("=", 1)
        kv[k] = v
    if msg is not None:
        kv["msg"] = msg
    return seq, node, kv


def read_lines(paths):
    for p in paths:
        with open(p, "rb") as f:
            for raw in f.read().decode("ascii", "replace").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
                yield raw


def evaluate(lines, need, waiting_ok=(), legacy=False):
    """{'stages': {stage: {'result': pass|fail|waiting|not-seen, ...}}, 'boots': [...], 'pass': bool}."""
    boots = []          # [(boot id, node, {stage: last kv})] in order of appearance
    current = {}
    legacy_seen = {"install": False, "install_fail": False, "firstboot": False, "firstboot_fail": None,
                   "ready": False}
    for text in lines:
        if legacy:
            if "CONGRATULATIONS!" in text and "install" in text.lower():
                legacy_seen["install"] = True
            if "D32: fail" in text:
                legacy_seen["firstboot_fail"] = text.strip()[:200]
            elif "D32: " in text:
                legacy_seen["firstboot"] = True
            if re.search(r"\blogin: *$", text):
                legacy_seen["ready"] = True
            continue
        parsed = parse_line(text)
        if not parsed:
            continue
        seq, node, kv = parsed
        boot = kv.get("boot", "-")
        if boot not in current:
            current[boot] = {"node": node, "seq": 0, "stages": {}}
            boots.append(boot)
        b = current[boot]
        if seq < b["seq"]:
            continue
        b["seq"] = seq
        st = kv.get("stage")
        if st in STAGES:
            b["stages"][st] = kv
    out = {}
    if legacy:
        out["install"] = {"result": "pass" if legacy_seen["install"] else "not-seen"}
        fb = legacy_seen["firstboot_fail"]
        out["firstboot"] = {"result": "fail", "msg": fb} if fb else {
            "result": "pass" if legacy_seen["firstboot"] else "not-seen"}
        out["ready"] = {"result": "pass" if legacy_seen["ready"] else "not-seen"}
        for s in STAGES:
            out.setdefault(s, {"result": "not-seen"})
        ok = all(out[s]["result"] == "pass" for s in need)
        return {"stages": out, "boots": [], "pass": ok, "legacy": True}
    last = current[boots[-1]] if boots else {"stages": {}}
    reached = [s for s in STAGES if s in last["stages"]]
    furthest = max((STAGES.index(s) for s in reached), default=-1)
    for s in STAGES:
        kv = last["stages"].get(s)
        if kv is None:
            # a stage this node has no part in is skipped (C6): it counts as passed only if a later stage was reached
            out[s] = {"result": "skipped" if STAGES.index(s) < furthest else "not-seen"}
            continue
        state = kv.get("state", "")
        if state == "failed":
            out[s] = {"result": "fail", "error": kv.get("error", ""), "msg": kv.get("msg", "")}
        elif state == "done" or STAGES.index(s) < furthest:
            out[s] = {"result": "pass"}
        elif state == "waiting" and s in waiting_ok:
            out[s] = {"result": "waiting", "detail": {k: v for k, v in kv.items() if k in ("expires", "pair_with")}}
        else:
            out[s] = {"result": state or "not-seen"}
    ok = all(out[s]["result"] in ("pass", "skipped") or (out[s]["result"] == "waiting" and s in waiting_ok)
             for s in need)
    return {"stages": out, "boots": [{"boot": b, "node": current[b]["node"], "seq": current[b]["seq"]} for b in boots],
            "pass": ok and bool(boots), "legacy": False}


def main(argv):
    ap = argparse.ArgumentParser(prog="c6.py")
    ap.add_argument("logs", nargs="+")
    ap.add_argument("--need", default="install,firstboot,keys")
    ap.add_argument("--waiting-ok", default="")
    ap.add_argument("--legacy", action="store_true")
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    need = [s for s in a.need.split(",") if s]
    bad = [s for s in need if s not in STAGES]
    if bad:
        print("c6: unknown stage(s) %s" % bad, file=sys.stderr)
        return 2
    try:
        res = evaluate(list(read_lines(a.logs)), need, [s for s in a.waiting_ok.split(",") if s], a.legacy)
    except OSError as e:
        print("c6: %s" % e, file=sys.stderr)
        return 2
    # the sign-in address and pairing code are short-lived secrets (C6 "Secrets"): never written to the result
    for s in res["stages"].values():
        s.pop("signin", None)
        s.pop("pair", None)
    res["need"] = need
    if a.json:
        with open(a.json, "w", encoding="utf-8", newline="\n") as w:
            json.dump(res, w, indent=1, sort_keys=True)
            w.write("\n")
    for s in STAGES:
        print("%-10s %s" % (s, res["stages"][s]["result"]))
    print("C6 %s (needed: %s)" % ("PASS" if res["pass"] else "FAIL", ",".join(need)))
    return 0 if res["pass"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
