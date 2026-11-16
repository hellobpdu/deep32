"""deep32-engine launcher with exact_local (PYTHON-ON-OPENBSD 6.4 and 7.2 steps 2-3), beside pyrag's serve_core.py.

  /usr/local/d32/venv/deep32-engine/bin/python3.12 -I -B -X utf8 /usr/local/d32/node/serve_core_exact.py --host H --port P

Order:
 1. configure_threads() from serve_core.py (8 MiB per new thread: OpenBSD's 512 KiB default would let a deep
    json/repr recursion in a worker thread SIGSEGV where production raises RecursionError);
 2. deep32-engine's code tree and the torch/pylate stand-ins on sys.path exactly as serve_core.py puts them;
 3. exact_local imported (libd32vec dlopened) and make_qdrant_client replaced in rag_core.dense and
    rag_core.retrievers.dense, so a RAG_QDRANT_URL of file:<generation>/dense gives the retrievers an ExactClient;
    any other URL still gives production's QdrantClient;
 4. serve_core.main(): production's own uvicorn command line (`uvicorn rag_core.api:app --workers 1 ...`), which
    runs the app's lifespan in this process, after the patch.
pledge/unveil and the UNIX socket (7.2 steps 4-6) are not here yet.
"""
import importlib.util
import os
import sys

NODE = os.path.dirname(os.path.abspath(__file__))


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, os.path.join(NODE, name + ".py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def prepare() -> object:
    """Steps 1-3; returns the serve_core module (whose main() is step 4). Also used by the in-process test hooks."""
    serve_core = _load("serve_core")
    serve_core.configure_threads()
    src = os.path.join(serve_core.CODE, "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    if serve_core.STANDINS not in sys.path:
        sys.path.append(serve_core.STANDINS)
    exact_local = _load("exact_local")
    import rag_core.dense
    import rag_core.retrievers.dense

    exact_local.install(rag_core.dense, rag_core.retrievers.dense)
    return serve_core


def main(argv: list[str]) -> None:
    serve_core = prepare()
    serve_core.main(argv)


if __name__ == "__main__":
    main(sys.argv[1:])
