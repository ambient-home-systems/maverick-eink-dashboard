#!/usr/bin/with-contenv bashio
# shellcheck shell=bash
# ==============================================================================
# Maverick: Home Assistant app entrypoint.
#
# The app's options are not translated here. The service reads them itself,
# from /data/options.json, and sets the environment variables /config/maverick.yaml
# substitutes (src/maverick/ha/options.py, called by `_load` in
# src/maverick/cli.py). That keeps one source of truth for the connection
# details — the Configuration tab — and leaves this script the two things that
# genuinely belong to the container: putting a config file where the user can
# edit it, and starting the service.
# ==============================================================================
set -o errexit -o pipefail

readonly CONFIG_DIR=/config
readonly CONFIG_FILE="${CONFIG_DIR}/maverick.yaml"
readonly TEMPLATE=/usr/share/maverick/maverick.yaml

if [[ ! -f "${CONFIG_FILE}" ]]; then
    bashio::log.info "No ${CONFIG_FILE} yet; writing the starter configuration with one example display."
    bashio::log.info "Edit it in this app's configuration folder (addon_configs) and restart the app."
    cp "${TEMPLATE}" "${CONFIG_FILE}"
fi

# Relative paths in the config (the file transport's default ./out, for one)
# then land next to it, where the user can see them.
cd "${CONFIG_DIR}"

bashio::log.info "Starting $(maverick --version)"
# -c is a global option, so it goes before the subcommand: `maverick serve -c
# file` is an argparse error (exit 2) and the app dies on every start.
exec maverick -c "${CONFIG_FILE}" serve --host 0.0.0.0 --port 5000
