# S2 engine recipe; the resolver replaces the base with its immutable digest.
ARG BASE_IMAGE=python:3.12-slim-trixie@sha256:TO-PIN-PYTHON
FROM ${BASE_IMAGE}
ARG SOURCE_DATE_EPOCH
ENV SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH}
ENV PYTHONDONTWRITEBYTECODE=1 PIP_NO_INDEX=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONUNBUFFERED=1
RUN python -I -B -c "import sqlite3; assert sqlite3.sqlite_version_info >= (3,44,0), 'SQLite older than 3.44'"
# Complete git/age/GnuPG hash-verified dependency closure; no repository access.
# Requires the local BuildKit frontend; read-only archives never enter a layer.
RUN --network=none --mount=type=bind,source=debs,target=/debs,readonly \
    dpkg -i /debs/*.deb && git --version && age --version && gpg --version && gpgv --version
RUN --network=none --mount=type=bind,source=wheelhouse,target=/wheels,readonly \
    --mount=type=bind,source=requirements.lock,target=/tmp/engine.lock,readonly \
    python -m venv /opt/deep32/engine/.venv \
 && /opt/deep32/engine/.venv/bin/pip install --no-cache-dir --no-index --find-links /wheels --require-hashes -r /tmp/engine.lock
COPY engine/rag/core /opt/deep32/engine
RUN /opt/deep32/engine/.venv/bin/python -I -B -c "import sysconfig,pathlib; pathlib.Path(sysconfig.get_paths()['purelib'],'deep32-engine.pth').write_text('/opt/deep32/engine/src'+chr(10))"
COPY kb-tools /opt/deep32/builder/kb-tools
COPY kb-bundle /opt/deep32/builder/kb-bundle
COPY derived_layers.py corpus_from_blocks.py builder.py eval_sets.py wait_services.py LICENSE NOTICE /opt/deep32/builder/
RUN useradd --system --uid 10032 --home-dir /opt/deep32 deep32
USER deep32
WORKDIR /opt/deep32/engine
ENV PATH=/opt/deep32/engine/.venv/bin:$PATH HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
ENTRYPOINT ["python", "-I", "-B", "/opt/deep32/builder/builder.py"]
