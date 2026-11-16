"""deep32-gateway launcher for Deep32 First light (PYTHON-ON-OPENBSD 3.4). Minimal for now: pledge/unveil and the UNIX
socket to deep32-engine arrive with the node-shim step (section 7.2) behind this same entry point.

  /usr/local/bin/python3.14 -I -B -X utf8 /usr/local/d32/node/serve_edge.py --host 127.0.0.1 --port 8766

It runs production's command line (`uvicorn app:app --host 127.0.0.1 --port 8766`, deep32-gateway.service) through
uvicorn's CLI. Environment as production's /etc/deep32-gateway.env (RAG_CORE_URL, RAG_CORE_API_TOKEN, TAILSCALE_DNS_NAME,
RAG_MCP_TOKENS, RAG_MCP_ALLOWED_IPS). D32_RAG_EDGE_CODE overrides the code tree (default
/usr/local/d32/deep32-gateway/current). No stand-ins: deep32-gateway imports nothing OpenBSD lacks.
"""
import os
import sys
import threading

THREAD_STACK = 8 << 20
CODE = os.environ.get("D32_RAG_EDGE_CODE", "/usr/local/d32/deep32-gateway/current")


def configure_threads(size: int = THREAD_STACK) -> None:
    """8 MiB per new thread, as glibc gives production (OpenBSD's default is 512 KiB); see serve_core.py."""
    threading.stack_size(size)


def main(argv: list[str]) -> None:
    configure_threads()
    sys.path.insert(0, CODE)
    os.chdir(CODE)
    import uvicorn

    uvicorn.main.main(args=["app:app", "--app-dir", CODE, *argv], prog_name="uvicorn")


if __name__ == "__main__":
    main(sys.argv[1:])
