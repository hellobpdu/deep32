ARG BASE_IMAGE
FROM ${BASE_IMAGE}
ARG SOURCE_DATE_EPOCH
ENV SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH}
WORKDIR /app
COPY wheelhouse/ /wheelhouse/
COPY requirements.lock /requirements.lock
RUN python -m pip install --no-compile --no-index --find-links=/wheelhouse --require-hashes -r /requirements.lock && rm -rf /wheelhouse
COPY reranker_api.py /app/
ENV PYTHONDONTWRITEBYTECODE=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 CC=/usr/bin/gcc
CMD ["python", "-m", "uvicorn", "reranker_api:app", "--host", "0.0.0.0", "--port", "8082", "--workers", "1", "--no-access-log"]
