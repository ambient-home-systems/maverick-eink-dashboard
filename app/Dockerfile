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
ARG MAVERICK_REF=992fbfcf12ee7d4d977616ac54c60072f5ad8f1b

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

CMD ["/run.sh"]
