# syntax=docker/dockerfile:1
FROM python:3.13-slim-bookworm
ARG VERSION=1.0.0
ARG REVISION=unknown
LABEL org.opencontainers.image.title="iSponsorBlockTV Web" \
      org.opencontainers.image.description="iSponsorBlockTV with a complete Chinese web administration panel" \
      org.opencontainers.image.source="https://github.com/dongbo501/isponsorblocktv-web" \
      org.opencontainers.image.licenses="GPL-3.0" \
      org.opencontainers.image.version=$VERSION \
      org.opencontainers.image.revision=$REVISION
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 DATA_DIR=/data
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends tini \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 1000 isbtv && useradd --uid 1000 --gid 1000 --no-create-home isbtv \
    && mkdir /data && chown isbtv:isbtv /data
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY webapp ./webapp
COPY vendor ./vendor
COPY upstream.json LICENSE NOTICE.md ./
USER 1000:1000
EXPOSE 8080
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3)"]
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "uvicorn", "webapp.app:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--no-access-log"]
