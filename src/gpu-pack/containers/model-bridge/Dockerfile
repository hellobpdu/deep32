# Build from the already verified offline builder image; no new image pull.
ARG BUILDER_IMAGE
FROM ${BUILDER_IMAGE}
USER root
COPY model-bridge /opt/deep32/model-bridge
RUN python -I -B -c "import pathlib; [compile(p.read_text(), str(p), 'exec') for p in pathlib.Path('/opt/deep32/model-bridge').glob('*.py')]"
USER deep32
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
ENTRYPOINT ["python", "-I", "-B", "/opt/deep32/model-bridge/linux_bridge.py"]
