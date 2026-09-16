# Maverick, as a standalone image.
#
# Modelled on app/Dockerfile, which CONTRIBUTING.md explains installs the
# package from a pinned commit (MAVERICK_REF) because the Home Assistant app
# store builds it independently of a `docker build` on this tree. This image
# has no such constraint — it is built from the working tree it ships with —
# so it installs the package from the build context instead, and it is not
# read by the Supervisor: no /data/options.json, no bashio, no ingress. Plain
# `debian:bookworm-slim` rather than app/Dockerfile's
# `ghcr.io/home-assistant/base-debian:bookworm`, since this image is meant to
# run with no Supervisor around it at all.
FROM debian:bookworm-slim

ENV LANG=C.UTF-8 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 \
    MAVERICK_CHROMIUM_PATH=/usr/bin/chromium \
    PATH=/opt/maverick/bin:${PATH}

# Fonts: the injected theme prefers Noto Sans, DejaVu Sans and Liberation Sans
# (src/maverick/eink/theme.py); without them Chromium falls back to whatever
# the base image has, which is nothing worth reading on ink.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 \
        python3-venv \
        ca-certificates \
        chromium \
        fonts-dejavu-core \
        fonts-liberation \
        fonts-noto-core \
    && rm -rf /var/lib/apt/lists/*

# A virtual environment keeps pip away from Debian's own site-packages
# (PEP 668). PATH above puts its bin/ first, so `maverick` and `python` resolve
# to it from `docker run`.
RUN python3 -m venv /opt/maverick \
    && /opt/maverick/bin/pip install --upgrade pip

COPY . /src
RUN /opt/maverick/bin/pip install /src \
    && rm -rf /src

LABEL \
    org.opencontainers.image.title="Maverick" \
    org.opencontainers.image.description="Render Home Assistant dashboards to e-ink panels" \
    org.opencontainers.image.source="https://github.com/ambient-home-systems/maverick-eink-dashboard" \
    org.opencontainers.image.licenses="MIT"

# Same probe as app/Dockerfile: /health answers as soon as the server is up,
# before any browser has started, so a slow first render does not read as a
# crash.
HEALTHCHECK --interval=60s --timeout=10s --start-period=120s --retries=3 \
    CMD python3 -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:5000/health', timeout=5).status == 200 else 1)"

VOLUME ["/config", "/media", "/share"]

CMD ["maverick", "-c", "/config/maverick.yaml", "serve", "--host", "0.0.0.0", "--port", "5000"]
