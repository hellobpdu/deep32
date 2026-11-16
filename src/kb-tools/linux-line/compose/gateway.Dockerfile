# deep32-gateway for S2 on 901. Same rules as engine.Dockerfile.
FROM python:3.12-slim-trixie@sha256:TO-PIN-PYTHON
ENV PYTHONDONTWRITEBYTECODE=1 PIP_NO_INDEX=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONUNBUFFERED=1
COPY wheelhouse/gateway /wheels
COPY locks/gateway.lock /tmp/gateway.lock
RUN python -m venv /opt/deep32/gateway/.venv \
 && /opt/deep32/gateway/.venv/bin/pip install --no-index --find-links /wheels --require-hashes -r /tmp/gateway.lock \
 && rm -rf /wheels /tmp/gateway.lock
COPY engine/rag/edge /opt/deep32/gateway
RUN useradd --system --uid 10033 --home-dir /opt/deep32 deep32gw
USER deep32gw
WORKDIR /opt/deep32/gateway
ENV PATH=/opt/deep32/gateway/.venv/bin:$PATH
EXPOSE 8443
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8443", "--workers", "1"]
