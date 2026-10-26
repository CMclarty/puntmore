# puntmore scraper: one real Chrome on a virtual display, kept-open pages.
# Built by .github/workflows/scraper-image.yml -> ghcr.io/cmclarty/puntmore-scraper
FROM mcr.microsoft.com/playwright/python:v1.55.0-noble

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    TZ=Europe/London \
    LANGUAGE=en_GB:en \
    DISPLAY=:99 \
    SCRAPER_DATA_DIR=/data \
    SCRAPER_PROFILE_DIR=/data/profile

RUN apt-get update \
 && apt-get install -y --no-install-recommends xvfb x11vnc tzdata \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt \
 # Real Google Chrome where it exists (amd64). Elsewhere the bundled Chromium
 # from the base image is used - browser.launch falls back on its own.
 && if [ "$(dpkg --print-architecture)" = "amd64" ]; then playwright install chrome; fi

COPY . /app/scraper
WORKDIR /app
RUN chmod +x /app/scraper/deploy/entrypoint.sh

VOLUME /data
# VNC, for logging in and clearing a check by hand. Publish it to the host's
# 127.0.0.1 only and reach it over an SSH tunnel - never on a public port.
EXPOSE 5900
HEALTHCHECK --interval=2m --timeout=10s --start-period=3m \
  CMD python -c "import time,sys; sys.exit(time.time()-int(open('/data/heartbeat').read())>300)"
STOPSIGNAL SIGTERM
ENTRYPOINT ["/app/scraper/deploy/entrypoint.sh"]
CMD ["run"]
