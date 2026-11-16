# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Jeffrey A. Cummings
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
ARG SOURCE_DATE_EPOCH
ENV SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH}
WORKDIR /app
COPY wheelhouse/ /wheelhouse/
COPY requirements.lock /requirements.lock
COPY build-backend.lock /build-backend.lock
RUN python -m pip install --no-compile --no-index --find-links=/wheelhouse --require-hashes --no-deps --only-binary=:all: -r /build-backend.lock
RUN python -m pip install --no-compile --no-index --find-links=/wheelhouse --require-hashes --no-build-isolation --only-binary=:all: --no-binary=jieba -r /requirements.lock && rm -rf /wheelhouse
COPY chain.py worker.py convert.py LICENSE AGPL-3.0.txt /app/
COPY production-converter/ /app/production-converter/
COPY document-styles/ /app/document-styles/
ENV PYTHONDONTWRITEBYTECODE=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
ENTRYPOINT ["python", "/app/convert.py"]
