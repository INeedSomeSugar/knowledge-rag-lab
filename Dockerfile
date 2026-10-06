FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    DATA_DIR=/data

WORKDIR /app
COPY pyproject.toml README.md ./
COPY app/ ./app/
COPY scripts/ ./scripts/
COPY evaluation/support_corpus/ ./evaluation/support_corpus/

RUN python -m pip install --no-cache-dir . \
    && groupadd --gid 10001 rag \
    && useradd --uid 10001 --gid rag --no-create-home rag \
    && mkdir /data \
    && chown rag:rag /data

USER 10001:10001
EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=2).close()"]

CMD ["python", "-m", "scripts.serve", "--demo", "--bootstrap", "--host", "0.0.0.0", "--data-dir", "/data"]
