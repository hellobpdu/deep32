#!/usr/bin/env python3
"""Public knowledge-base source helper."""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import ipaddress
import json
import re
from pathlib import Path

DIGEST = re.compile(r"^[a-z0-9][a-z0-9._/-]*(:[A-Za-z0-9._-]+)?@sha256:[0-9a-f]{64}$")
PLACEHOLDER = re.compile(r"^[a-z0-9][a-z0-9._/-]*(:[A-Za-z0-9._-]+)?@sha256:TO-PIN-[A-Z0-9-]+$")
PORT = re.compile(r"^127\.0\.0\.1:(\d{1,5}):(\d{1,5})$")
FORBIDDEN_PORTS = {8081, 8082}
NAMES = re.compile(r'\.ts\.net\b', re.I)
IPV4 = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})(?:/\d{1,2})?\b")
BANNED_NETS = []
BANNED_KEYS = ("network_mode", "privileged", "cap_add", "devices", "ipc", "pid")


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield str(k)
            yield from strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from strings(v)


def address_findings(text: str, where: str) -> list[str]:
    out = []
    if NAMES.search(text):
        out.append(f"{where}: names a production host or the tailnet")
    for m in IPV4.finditer(text):
        try:
            ip = ipaddress.ip_address(m.group(1))
        except ValueError:
            continue
        if not ip.is_loopback:
            out.append(f"{where}: address {ip} is not loopback")
    return out


def check_image(ref: str, where: str, strict: bool, findings: list, placeholders: list) -> None:
    if DIGEST.match(ref):
        return
    if PLACEHOLDER.match(ref) and not strict:
        placeholders.append(f"{where}: {ref}")
        return
    findings.append(f"{where}: image {ref!r} is not pinned by digest")


def check(compose_path: Path, strict: bool) -> dict:
    obj = json.loads(compose_path.read_bytes())
    ctx = compose_path.resolve().parent.parent
    findings, placeholders, dockerfiles = [], [], []
    findings += address_findings("\n".join(strings(obj)), compose_path.name)
    for name, svc in sorted((obj.get("services") or {}).items()):
        where = f"service {name}"
        for k in BANNED_KEYS:
            if k in svc:
                findings.append(f"{where}: {k} is not allowed")
        if "build" in svc:
            if svc.get("pull_policy") != "never":
                findings.append(f"{where}: a built image needs pull_policy never")
            df = ctx / svc["build"]["dockerfile"]
            dockerfiles.append(df)
            text = df.read_text(encoding="utf-8")
            froms = [line.split()[1] for line in text.splitlines() if line.strip().upper().startswith("FROM ")]
            if not froms:
                findings.append(f"{df.name}: no FROM line")
            for ref in froms:
                check_image(ref, df.name, strict, findings, placeholders)
            findings += address_findings(text, df.name)
        else:
            check_image(str(svc.get("image", "")), where, strict, findings, placeholders)
        for p in svc.get("ports") or []:
            m = PORT.match(str(p))
            if not m:
                findings.append(f"{where}: port {p!r} is not published on 127.0.0.1 only")
                continue
            if {int(m.group(1)), int(m.group(2))} & FORBIDDEN_PORTS:
                findings.append(f"{where}: port {p!r} uses 8081 or 8082")
        if "expose" in svc and any(int(str(x).split("/")[0]) in FORBIDDEN_PORTS for x in svc["expose"]):
            findings.append(f"{where}: exposes 8081 or 8082")
    for n, net in (obj.get("networks") or {}).items():
        if net.get("driver") not in (None, "bridge") or net.get("external"):
            findings.append(f"network {n}: only a local bridge is allowed")
    status = "pass" if not findings and (not strict or not placeholders) else "fail"
    if strict and placeholders:
        findings += [f"placeholder left: {p}" for p in placeholders]
    return {"format": "deep32-compose-check", "compose": str(compose_path), "strict": strict,
            "dockerfiles": [d.name for d in dockerfiles], "placeholders": placeholders, "findings": findings,
            "status": status}


def render(compose_path: Path, pins_path: Path, out: Path) -> dict:
    pins = {}
    for line in pins_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            k, _, v = line.partition("=")
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", v.strip()):
                raise ValueError(f"{pins_path}: {k} is not sha256:<64 hex>")
            pins[k.strip()] = v.strip()[len("sha256:"):]

    def fill(text: str) -> str:
        def sub(m):
            if m.group(1) not in pins:
                raise ValueError(f"no pin for {m.group(1)}")
            return "sha256:" + pins[m.group(1)]
        return re.sub(r"sha256:TO-PIN-([A-Z0-9]+)", sub, text)

    ctx = compose_path.resolve().parent.parent
    obj = json.loads(compose_path.read_bytes())
    rel = compose_path.resolve().relative_to(ctx)
    (out / rel).parent.mkdir(parents=True, exist_ok=True)
    (out / rel).write_text(fill(compose_path.read_text(encoding="utf-8")), encoding="utf-8", newline="\n")
    for svc in obj["services"].values():
        if "build" in svc:
            src = ctx / svc["build"]["dockerfile"]
            dst = out / svc["build"]["dockerfile"]
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(fill(src.read_text(encoding="utf-8")), encoding="utf-8", newline="\n")
    return check(out / rel, True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="check_compose.py", allow_abbrev=False)
    sub = ap.add_subparsers(dest="verb", required=True)
    c = sub.add_parser("check", allow_abbrev=False)
    c.add_argument("--compose", required=True)
    c.add_argument("--strict", action="store_true")
    c.add_argument("--json", action="store_true")
    r = sub.add_parser("render", allow_abbrev=False)
    r.add_argument("--compose", required=True)
    r.add_argument("--pins", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--json", action="store_true")
    try:
        a = ap.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    try:
        rec = check(Path(a.compose), a.strict) if a.verb == "check" else \
            render(Path(a.compose), Path(a.pins), Path(a.out))
    except (OSError, ValueError, KeyError, IndexError) as exc:
        print(f"check_compose: {exc}", file=sys.stderr)
        return 3
    if a.json:
        print(json.dumps(rec, sort_keys=True))
    else:
        for f in rec["findings"]:
            print("FINDING " + f)
        print(f"compose {rec['status']}: {len(rec['findings'])} findings, {len(rec['placeholders'])} placeholders")
    return 0 if rec["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
