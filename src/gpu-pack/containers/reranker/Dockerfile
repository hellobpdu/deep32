ARG BASE_IMAGE
FROM ${BASE_IMAGE}
ARG SOURCE_DATE_EPOCH
ENV SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH}
WORKDIR /app
COPY requirements.lock /requirements.lock
# Requires the local BuildKit frontend; the read-only inputs never enter a layer.
RUN --network=none --mount=type=bind,source=wheelhouse,target=/wheelhouse,readonly \
    python -m pip install --no-cache-dir --no-compile --no-index --find-links=/wheelhouse --require-hashes -r /requirements.lock
COPY reranker_api.py LICENSE NOTICE /app/
ENV PYTHONDONTWRITEBYTECODE=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 CC=/usr/bin/gcc
CMD ["python", "-m", "uvicorn", "reranker_api:app", "--host", "0.0.0.0", "--port", "8082", "--workers", "1", "--no-access-log"]
