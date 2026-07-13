FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY config ./config
COPY seeds ./seeds
COPY src ./src
COPY scripts ./scripts

RUN pip install --upgrade pip \
    && pip install -e . \
    && chmod +x /app/scripts/docker-entrypoint.sh \
    && mkdir -p /data /app/generated_sites

ENV APP_ENV=development \
    DATABASE_URL=sqlite:////data/rank_rent.db \
    ALLOW_LIVE_API_CALLS=false

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).read()"

ENTRYPOINT ["/app/scripts/docker-entrypoint.sh"]
CMD ["rank-rent", "web", "--host", "0.0.0.0", "--port", "8000"]
