FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=3000

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates ffmpeg nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py ./
COPY static ./static
COPY templates ./templates

RUN useradd --create-home appuser \
    && mkdir downloads \
    && chown appuser:appuser downloads

USER appuser

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ['PORT'], timeout=3)" || exit 1

CMD ["sh", "-c", "exec gunicorn --bind 0.0.0.0:${PORT} --workers ${WEB_CONCURRENCY:-2} --timeout 300 --access-logfile - --error-logfile - app:app"]
