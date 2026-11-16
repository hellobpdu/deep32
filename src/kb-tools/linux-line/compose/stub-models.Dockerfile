# stub-models for S2 (work/engine/stub-models: standard library only, numbers from SHA-256 of the input).
FROM python:3.12-slim-trixie@sha256:TO-PIN-PYTHON
COPY stub-models/stub_models.py /opt/stub/stub_models.py
RUN useradd --system --uid 10034 stub
USER stub
EXPOSE 9000
CMD ["python", "-I", "-B", "/opt/stub/stub_models.py", "--host", "0.0.0.0", "--any-host", "--port", "9000", "--dim", "1024", "--mode", "bow"]
