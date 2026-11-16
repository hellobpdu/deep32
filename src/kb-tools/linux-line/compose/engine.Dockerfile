# deep32-engine for S2 on 901 (the build schedule item 9). Base image by digest only (compose/check_compose.py --strict);
# every wheel installed from the local wheelhouse with --require-hashes (compose/lock-wheels.sh made the lock).
FROM python:3.12-slim-trixie@sha256:TO-PIN-PYTHON
ENV PYTHONDONTWRITEBYTECODE=1 PIP_NO_INDEX=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONUNBUFFERED=1
# SQLite 3.44.0 or newer: validate_root.py's fts_integrity gate runs the FTS5 integrity-check through
# PRAGMA integrity_check, which older SQLite skips (S2-7 on 901, 2026-11-15: bookworm's 3.40.1 failed the gate).
# trixie's Python links Debian 13's libsqlite3 (3.46.x). The build stops here if that ever regresses.
RUN python -I -c "import sqlite3, sys; print('sqlite', sqlite3.sqlite_version); sys.exit(0 if sqlite3.sqlite_version_info >= (3, 44, 0) else 'SQLite older than 3.44.0')"
# git: build_root.py takes the corpus with "git archive"; age: export_generation.py encrypts its export for one
# recipient (a throwaway lab key on 901). Versions from the base image's Debian archive, written to the build log.
RUN apt-get update && apt-get install -y --no-install-recommends git age  && rm -rf /var/lib/apt/lists/* && git --version && age --version
COPY wheelhouse/engine /wheels
COPY locks/engine.lock /tmp/engine.lock
RUN python -m venv /opt/deep32/engine/.venv \
 && /opt/deep32/engine/.venv/bin/pip install --no-index --find-links /wheels --require-hashes -r /tmp/engine.lock \
 && rm -rf /wheels /tmp/engine.lock
COPY engine/rag/core /opt/deep32/engine
# The export has no packaging metadata: rag_core is found through a .pth file in the venv (it also works under
# python -I, which ignores PYTHONPATH), as scripts/build_root.py imports rag_core without adding src itself.
RUN /opt/deep32/engine/.venv/bin/python -I -c "import sysconfig, pathlib; pathlib.Path(sysconfig.get_paths()['purelib'], 'deep32-engine.pth').write_text('/opt/deep32/engine/src' + chr(10))"
COPY lane /opt/deep32/lane
RUN useradd --system --uid 10032 --home-dir /opt/deep32 deep32 && mkdir -p /var/lib/deep32/rag-data \
 && chown deep32 /var/lib/deep32/rag-data
USER deep32
WORKDIR /opt/deep32/engine
ENV PATH=/opt/deep32/engine/.venv/bin:$PATH PYTHONPATH=/opt/deep32/engine/src
EXPOSE 8765
CMD ["uvicorn", "rag_core.api:app", "--host", "0.0.0.0", "--port", "8765", "--workers", "1"]
