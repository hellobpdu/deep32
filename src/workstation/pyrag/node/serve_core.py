"""deep32-engine launcher for Deep32 First light (PYTHON-ON-OPENBSD 3.4 and 4.1). Minimal for now: pledge/unveil, the
UNIX socket and exact_local arrive with the node-shim step (10.2 step 10, section 7.2) behind this same entry point.

  /usr/local/d32/py312/bin/python3.12 -I -B -X utf8 /usr/local/d32/node/serve_core.py --host H --port P

It runs production's own command line (`uvicorn rag_core.api:app --host H --port P --workers 1`, deep32-engine.service)
through uvicorn's CLI, so every uvicorn default (loop=auto picks uvloop, http=auto picks httptools, lifespan, proxy
headers) is production's. Before anything else it gives every new thread an 8 MiB stack (configure_threads).
Environment as production's /etc/deep32-engine.env (RAG_ROOT, RAG_CORE_API_TOKEN, ...). D32_RAG_CORE_CODE overrides the
code tree (default /usr/local/d32/deep32-engine/current, a symlink to the release commit's src + config).
"""
import os
import sys
import threading

THREAD_STACK = 8 << 20
NODE = os.path.dirname(os.path.abspath(__file__))
STANDINS = os.path.join(NODE, "standins")
CODE = os.environ.get("D32_RAG_CORE_CODE", "/usr/local/d32/deep32-engine/current")


def configure_threads(size: int = THREAD_STACK) -> None:
    """Every thread started after this call gets `size` bytes of stack.

    OpenBSD's default pthread stack is 512 KiB and CPython 3.12 sets none for OpenBSD (Python/thread_pthread.h),
    while its C recursion limit (10000, Include/cpython/pystate.h) assumes Linux, where glibc gives each thread
    RLIMIT_STACK (8 MiB). uvicorn/anyio run deep32-engine's sync endpoints in worker threads, so a deep json/repr/pickle
    recursion there would kill the process with SIGSEGV on OpenBSD where production raises RecursionError.
    8 MiB restores production's behaviour; tools/stack_probe.py and test_node.py prove both sides."""
    threading.stack_size(size)


def main(argv: list[str]) -> None:
    configure_threads()
    src = os.path.join(CODE, "src")
    sys.path.insert(0, src)
    sys.path.append(STANDINS)  # after the venv: the stand-ins only fill what is missing there (torch, pylate)
    os.chdir(CODE)             # production: WorkingDirectory=<the engine checkout>
    import uvicorn

    uvicorn.main.main(args=["rag_core.api:app", "--app-dir", src, "--workers", "1", *argv], prog_name="uvicorn")


if __name__ == "__main__":
    main(sys.argv[1:])
