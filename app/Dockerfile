# Maverick, packaged as a Home Assistant app.
#
# Debian rather than Alpine on purpose. Playwright's driver is a glibc Node
# binary, and Debian's own chromium package exists for amd64 and arm64, so the
# "Playwright ships no ARM Chromium" problem from the README does not exist
# inside this image. The Supervisor builds it on the machine that installs it.
ARG BUILD_FROM=ghcr.io/home-assistant/base-debian:bookworm
FROM ${BUILD_FROM}

# The commit of this repository the image installs. Pinned so that an image
# built today and one built next month contain the same code. The release
# procedure in CONTRIBUTING.md moves it to the release tag alongside the
# version in config.yaml.
#
# It must name a commit whose package accepts the starter config in rootfs/,
# which ships from this directory instead. Every model bar TransportConfig
# forbids unknown keys (src/maverick/config.py:71-77), so a ref left behind at
# an older release writes keys that release rejects and the app dies on its
# first start; tests/test_app.py loads the one through the other.
#
# v0.2.3 names d8b2e1f, the 0.2.3 release commit: enables Home Assistant
# ingress for the Open Web UI button, so it opens embedded in Home Assistant
# instead of a raw host:port link that a remote connection (Nabu Casa, a
# reverse proxy) could never reach.
ARG MAVERICK_REF=v0.2.3

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
# to it from run.sh and from `docker run`.
RUN python3 -m venv /opt/maverick \
    && pip install --upgrade pip \
    && pip install \
        "https://github.com/ambient-home-systems/maverick-eink-dashboard/archive/${MAVERICK_REF}.tar.gz"

COPY rootfs /
COPY run.sh /run.sh
RUN chmod a+x /run.sh

LABEL \
    org.opencontainers.image.title="Maverick" \
    org.opencontainers.image.description="Render Home Assistant dashboards to e-ink panels" \
    org.opencontainers.image.source="https://github.com/ambient-home-systems/maverick-eink-dashboard" \
    org.opencontainers.image.licenses="MIT"

# The Supervisor watches this instead of a `watchdog` key in config.yaml, which
# its linter now rejects. /health answers as soon as the server is up, before
# any browser has started, so a slow first render does not read as a crash.
HEALTHCHECK --interval=60s --timeout=10s --start-period=120s --retries=3 \
    CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:5000/health', timeout=5).status == 200 else 1)"

CMD ["/run.sh"]
