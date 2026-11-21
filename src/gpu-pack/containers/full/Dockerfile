# Derived only from the verified local S2 builder; build-full.sh supplies its id.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
USER root
ARG SOURCE_DATE_EPOCH
ENV SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH} PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_INDEX=1 PIP_DISABLE_PIP_VERSION_CHECK=1
# Requires the local BuildKit frontend; read-only archives never enter a layer.
RUN --network=none --mount=type=bind,source=wheelhouse,target=/wheels,readonly \
    --mount=type=bind,source=gateway.lock,target=/tmp/gateway.lock,readonly \
    /opt/deep32/engine/.venv/bin/pip install --no-cache-dir --no-index --only-binary=:all: --find-links /wheels --require-hashes -r /tmp/gateway.lock
COPY engine /opt/deep32/engine
COPY gateway /opt/deep32/gateway
COPY import /opt/deep32/import
COPY pairing /opt/deep32/pairing
COPY full /opt/deep32/full
COPY model-bridge /opt/deep32/model-bridge
RUN /opt/deep32/engine/.venv/bin/python -I -B -c "import importlib; [importlib.import_module(x) for x in ('uvicorn','rag_core.api','mcp.server.mcpserver','mcp_types','starlette','httpx')]" \
 && /usr/bin/gpgv --version && openssl version
WORKDIR /opt/deep32
ENV PATH=/opt/deep32/engine/.venv/bin:$PATH HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
ENTRYPOINT ["python", "-I", "-B", "/opt/deep32/full/launch.py"]
