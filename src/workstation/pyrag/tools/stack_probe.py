"""Thread stack probe: does deep C-level recursion overflow the stack (SIGSEGV) before CPython's own limits stop it?

OpenBSD's default pthread stack is far smaller than glibc's (8 MiB, from RLIMIT_STACK), and CPython 3.12 sets no
THREAD_STACK_SIZE for OpenBSD while its C recursion limit is 10000 (Include/cpython/pystate.h). uvicorn/anyio run
deep32-engine's sync endpoints in worker threads, so a deep recursion that raises RecursionError on Linux could crash the
whole process on OpenBSD.

Usage:
  python -I stack_probe.py report                    # default stack size, then a table of every probe x place
  python -I stack_probe.py run PROBE PLACE [STACK]   # one probe (used by report, in a child process)
PLACE: main | thread (default size) | sized (threading.stack_size(STACK), default 8 MiB, as the launcher sets).
A probe prints OK, or the exception name; a crash shows as the child's signal.
"""
import ctypes
import ctypes.util
import json
import os
import signal
import sqlite3
import subprocess
import sys
import threading

DEPTH = 9000          # under C_RECURSION_LIMIT (10000): Linux with 8 MiB stacks reaches it without crashing
SQL_DEPTH = 990       # under SQLITE_MAX_EXPR_DEPTH (1000)
FTS_DEPTH = 90        # under FTS5's parser stack (100 nested groups: "fts5: parser stack overflow" everywhere)


def nested_list(n):
    x = []
    for _ in range(n):
        x = [x]
    return x


def p_json_loads():
    json.loads("[" * DEPTH + "]" * DEPTH)


def p_json_dumps():
    json.dumps(nested_list(DEPTH))


def p_repr():
    repr(nested_list(DEPTH))


def p_sqlite_expr():
    c = sqlite3.connect(":memory:")
    c.execute("select " + "(" * SQL_DEPTH + "1" + ")" * SQL_DEPTH).fetchall()


def p_fts5_query():
    c = sqlite3.connect(":memory:")
    c.execute("create virtual table t using fts5(x)")
    c.execute("insert into t values ('ospf nssa translator')")
    q = "(" * FTS_DEPTH + "ospf" + ")" * FTS_DEPTH
    c.execute("select rowid, bm25(t) from t where t match ?", (q,)).fetchall()


def p_py_recursion():
    def f(n):
        return 0 if n == 0 else 1 + f(n - 1)
    f(sys.getrecursionlimit() - 50)


PROBES = {k[2:]: v for k, v in globals().items() if k.startswith("p_")}


def run(probe, place, stack):
    result = {}

    def body():
        try:
            PROBES[probe]()
            result["r"] = "OK"
        except BaseException as exc:  # noqa: BLE001 - the probe reports what stopped it
            result["r"] = type(exc).__name__

    if place == "main":
        body()
    else:
        if place == "sized":
            threading.stack_size(stack)
        t = threading.Thread(target=body)
        t.start()
        t.join()
    print(result.get("r", "NO RESULT"))


def default_pthread_stack():
    libc = ctypes.CDLL(ctypes.util.find_library("c") or None)
    attr = ctypes.create_string_buffer(512)
    size = ctypes.c_size_t()
    if libc.pthread_attr_init(attr) != 0:
        return None
    libc.pthread_attr_getstacksize(attr, ctypes.byref(size))
    return size.value


def no_core():
    """The crashing children must not leave python3.x.core files behind."""
    import resource
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def report(stack):
    import resource
    soft, _hard = resource.getrlimit(resource.RLIMIT_STACK)
    print(f"python {sys.version.split()[0]} on {sys.platform}; RLIMIT_STACK soft {soft // 1024} KiB (main thread); "
          f"default pthread stack {default_pthread_stack() // 1024} KiB; threading.stack_size() = "
          f"{threading.stack_size()}; recursion limit {sys.getrecursionlimit()}")
    places = ["main", "thread", "sized"]
    print("probe".ljust(14) + "".join(p.ljust(16) for p in places) + f"(sized = {stack // 1048576} MiB)")
    for probe in PROBES:
        row = []
        for place in places:
            r = subprocess.run([sys.executable, "-I", __file__, "run", probe, place, str(stack)],
                               capture_output=True, text=True, timeout=300, preexec_fn=no_core)
            if r.returncode < 0:
                row.append("CRASH " + signal.Signals(-r.returncode).name)
            else:
                row.append((r.stdout.strip() or r.stderr.strip()[-40:] or f"rc={r.returncode}")[:15])
        print(probe.ljust(14) + "".join(x.ljust(16) for x in row))


if __name__ == "__main__":
    if sys.argv[1] == "run":
        run(sys.argv[2], sys.argv[3], int(sys.argv[4]) if len(sys.argv) > 4 else 8 << 20)
    else:
        report(int(sys.argv[2]) if len(sys.argv) > 2 else 8 << 20)
