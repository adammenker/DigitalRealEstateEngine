FROM python:3.12.10-slim-bookworm@sha256:fd95fa221297a88e1cf49c55ec1828edd7c5a428187e67b5d1805692d11588db AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH

COPY pyproject.toml requirements.lock README.md ./
RUN pip install --upgrade pip==25.1.1 \
    && pip install --requirement requirements.lock
COPY src ./src
RUN pip install --no-deps .


FROM python:3.12.10-slim-bookworm@sha256:fd95fa221297a88e1cf49c55ec1828edd7c5a428187e67b5d1805692d11588db AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH \
    APP_ENV=local \
    DATABASE_URL=sqlite:////data/rank_rent.db \
    ALLOW_LIVE_API_CALLS=false

ARG RELEASE_GIT_SHA=development
ARG RELEASE_IMAGE_DIGEST=unavailable
ENV RELEASE_GIT_SHA=$RELEASE_GIT_SHA \
    RELEASE_IMAGE_DIGEST=$RELEASE_IMAGE_DIGEST

LABEL org.opencontainers.image.source="https://github.com/adammenker/DigitalRealEstateEngine" \
    org.opencontainers.image.revision=$RELEASE_GIT_SHA

WORKDIR /app

RUN apt-get update \
    && apt-get upgrade --yes \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv
COPY alembic.ini ./
COPY config ./config
COPY data ./data
COPY migrations ./migrations
COPY seeds ./seeds
COPY src ./src
COPY scripts ./scripts

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app \
    && chmod +x /app/scripts/docker-entrypoint.sh \
    && mkdir -p /data /app/generated_sites \
    && chown -R app:app /data /app/generated_sites

USER 10001:10001

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=3).read()"

ENTRYPOINT ["/app/scripts/docker-entrypoint.sh"]
CMD ["rank-rent", "web", "--host", "0.0.0.0", "--port", "8000"]
