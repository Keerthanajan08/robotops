FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY alembic.ini .
COPY alembic ./alembic
COPY app ./app
RUN useradd --create-home --uid 10001 appuser
USER 10001
# WEB_CONCURRENCY = number of uvicorn worker processes (read by uvicorn itself).
ENV PROMETHEUS_MULTIPROC_DIR=/tmp/prometheus WEB_CONCURRENCY=1
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --retries=5 CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz', timeout=2)"]
CMD ["sh","-c","rm -rf \"$PROMETHEUS_MULTIPROC_DIR\" && mkdir -p \"$PROMETHEUS_MULTIPROC_DIR\" && alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
