#!/usr/bin/env python3
"""Run deep32-gateway's own tests against both copies of the gateway in this repository.

    python3 -B tests/gateway/run_tests.py                       both copies
    python3 -B tests/gateway/run_tests.py --target gateway      src/gateway only
    python3 -B tests/gateway/run_tests.py test_tools test_deep  these modules only

Run it from the root of the repository with a Python 3.12 or newer that has the gateway's packages
(tests/gateway/requirements-dev.txt). Each test module runs in a process of its own, with DEEP32_GATEWAY_TARGET set
(gateway_target.py). With --skip-missing, a Python without the packages prints one line and exits 0 instead of
failing.

Exit status: 0 when every module passed, 1 when any failed or timed out, 2 for bad arguments or missing packages.
"""
import argparse
import concurrent.futures
import importlib.util
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
TARGETS = ("gateway", "builder")
PACKAGES = ("anyio", "httpx", "mcp", "mcp_types", "pydantic", "starlette")


def run_module(target, module, timeout):
    env = dict(os.environ, DEEP32_GATEWAY_TARGET=target, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
    started = time.monotonic()
    try:
        done = subprocess.run([sys.executable, "-B", "-m", "unittest", f"tests/gateway/{module}.py"], cwd=ROOT,
                              env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=timeout)
        output, code = done.stdout + done.stderr, done.returncode
    except subprocess.TimeoutExpired:
        output, code = f"timed out after {timeout} s", None
    ran = re.search(r"^Ran (\d+) tests?", output, re.M)
    skipped = re.search(r"skipped=(\d+)", output)
    return dict(target=target, module=module, code=code, output=output, seconds=time.monotonic() - started,
                ran=int(ran.group(1)) if ran else 0, skipped=int(skipped.group(1)) if skipped else 0)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("modules", nargs="*", metavar="MODULE", help="test modules to run (default: all)")
    parser.add_argument("--target", action="append", choices=TARGETS, help="gateway copy (default: both)")
    parser.add_argument("--jobs", type=int, default=4, help="modules run at once (default 4)")
    parser.add_argument("--timeout", type=int, default=900, help="seconds allowed per module (default 900)")
    parser.add_argument("--skip-missing", action="store_true", help="exit 0 when the packages are missing")
    args = parser.parse_args(argv)
    missing = [name for name in PACKAGES if importlib.util.find_spec(name) is None]
    if missing:
        print(f"tests/gateway: needs {', '.join(missing)} (pip install -r tests/gateway/requirements-dev.txt)")
        return 0 if args.skip_missing else 2
    known = sorted(path.stem for path in HERE.glob("test_*.py"))
    wanted = [name.removesuffix(".py") for name in args.modules] or known
    unknown = sorted(set(wanted) - set(known))
    if unknown:
        parser.error(f"no such test module: {', '.join(unknown)}")
    plan = [(target, module) for target in (args.target or TARGETS) for module in wanted]
    with concurrent.futures.ThreadPoolExecutor(max(1, args.jobs)) as pool:
        results = list(pool.map(lambda item: run_module(*item, args.timeout), plan))
    failed = [r for r in results if r["code"] != 0]
    for r in results:
        status = "ok" if r["code"] == 0 else ("TIMEOUT" if r["code"] is None else "FAILED")
        print(f"{r['target']:8s} {r['module']:34s} {status:7s} {r['ran']:4d} tests, {r['skipped']:3d} skipped, "
              f"{r['seconds']:6.1f} s")
    for r in failed:
        print(f"\n===== {r['target']} {r['module']}\n{r['output'][-6000:]}")
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed of {len(results)} module runs; "
          f"{sum(r['ran'] for r in results)} tests, {sum(r['skipped'] for r in results)} skipped")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
