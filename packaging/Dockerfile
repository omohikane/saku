# SAKU container image (Web UI + autonomous daemon in one process).
# Works on any architecture with a python:slim base (x86_64, arm64).
FROM python:3.12-slim

WORKDIR /app

# Install the package first so dependency layers cache independently of the code.
COPY pyproject.toml README.md ./
COPY saku ./saku
RUN pip install --no-cache-dir .

ENV SAKU_CONFIG=/etc/saku/config.toml \
    PYTHONUNBUFFERED=1 \
    SAKU_UI_TOKEN=""

EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8787/api/health', timeout=3).status==200 else 1)"

CMD ["python", "-m", "saku.ui"]
