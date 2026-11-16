"""Check proposed T42 boolean keys against a reviewed native defaults dump."""

import argparse
from pathlib import Path
import re


MAX_DEFAULTS = 32 * 1024 * 1024
KEY = re.compile(r"[a-z][a-z0-9_.-]*")
PREF = re.compile(r"[ \t]*#?[ \t]*([a-z][a-z0-9_.-]*)[ \t]*:[ \t]*(.*)")
POLICY = Path(__file__).with_name("preferences.review.tsv")


def load_policy(path):
    lines = path.read_text(encoding="ascii").splitlines()
    if not lines or lines[0] != "key\tvalue\tarchive_token":
        raise ValueError("invalid preference review header")
    desired = {}
    for line in lines[1:]:
        parts = line.split("\t")
        if len(parts) != 3:
            raise ValueError("invalid preference review row")
        key, value, token = parts
        if not KEY.fullmatch(key) or value != "FALSE" or not KEY.fullmatch(token) or key in desired:
            raise ValueError("invalid or repeated preference review key")
        desired[key] = value
    if not desired:
        raise ValueError("empty preference review")
    return desired


def verify(defaults, desired):
    if "\0" in defaults:
        raise ValueError("defaults contain NUL")
    seen = set()
    for line in defaults.splitlines():
        match = PREF.fullmatch(line)
        if not match or match[1] not in desired:
            continue
        key, value = match.groups()
        if key in seen:
            raise ValueError(f"duplicate native preference {key}")
        if value.strip() not in {"TRUE", "FALSE"}:
            raise ValueError(f"nonboolean native preference {key}")
        seen.add(key)
    missing = desired.keys() - seen
    if missing:
        raise ValueError("native defaults missing " + ", ".join(sorted(missing)))
    header = "# T42 draft. The package and isolation gates require separate approval.\n"
    return header + "".join(f"{key}: {value}\n" for key, value in desired.items())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("defaults", type=Path, help="reviewed local native tshark -G defaultprefs output")
    args = parser.parse_args()
    try:
        with args.defaults.open("rb") as source:
            data = source.read(MAX_DEFAULTS + 1)
        if len(data) > MAX_DEFAULTS:
            raise ValueError("defaults exceed the 32 MiB review limit")
        result = verify(data.decode("utf-8"), load_policy(POLICY))
    except (OSError, ValueError) as exc:
        parser.exit(65, f"T42 preference review refused. {exc}\n")
    print(result, end="")


if __name__ == "__main__":
    main()
