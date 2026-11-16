"""uvicorn entry point (`uvicorn app:app`): builds deep32-gateway from the environment. See rag_edge.py."""

from rag_edge import Config, create_app

app = create_app(Config.from_env())
