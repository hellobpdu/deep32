# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Jeffrey A. Cummings
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
ARG SOURCE_DATE_EPOCH
ENV SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH}
WORKDIR /app
# Bind only the authenticated full Bookworm closure for this exact base.
RUN --network=none --mount=type=bind,source=debs,target=/debs,readonly \
    sha256sum --check /debs/base-dpkg.sha256 && dpkg -i /debs/*.deb \
    && audit=$(dpkg --audit) && test -z "$audit" \
    && python -I -B -c "import ctypes; ctypes.CDLL('libGL.so.1')"
COPY requirements.lock /requirements.lock
COPY build-backend.lock /build-backend.lock
# Keep the pinned backend installed for the permitted jieba source build.
# Requires the local BuildKit frontend; neither install retains the input archives.
RUN --network=none --mount=type=bind,source=wheelhouse,target=/wheelhouse,readonly \
    python -m pip install --no-cache-dir --no-compile --no-index --find-links=/wheelhouse --require-hashes --no-deps --only-binary=:all: -r /build-backend.lock
RUN --network=none --mount=type=bind,source=wheelhouse,target=/wheelhouse,readonly \
    python -m pip install --no-cache-dir --no-compile --no-index --find-links=/wheelhouse --require-hashes --no-build-isolation --only-binary=:all: --no-binary=jieba -r /requirements.lock
RUN --network=none python -I -B -m pip check \
    && python -I -B -c "import accelerate, cv2"
COPY chain.py worker.py convert.py LICENSE AGPL-3.0.txt /app/
COPY production-converter/ /app/production-converter/
COPY document-styles/ /app/document-styles/
ENV PYTHONDONTWRITEBYTECODE=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
ENTRYPOINT ["python", "/app/convert.py"]
