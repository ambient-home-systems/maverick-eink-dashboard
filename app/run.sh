#!/usr/bin/with-contenv bashio
# shellcheck shell=bash
# ==============================================================================
# Maverick: Home Assistant app entrypoint.
#
# The app's options never reach the service directly. They become environment
# variables, and /config/maverick.yaml reads them through the ${VAR}
# substitution the config loader already supports (src/maverick/config.py).
# That keeps one source of truth for the displays, the file the user edits,
# and lets the connection details change from the Configuration tab without
# touching it.
# ==============================================================================
set -o errexit -o pipefail

readonly CONFIG_DIR=/config
readonly CONFIG_FILE="${CONFIG_DIR}/maverick.yaml"
readonly TEMPLATE=/usr/share/maverick/maverick.yaml

# ---------------------------------------------------------------- required --

if ! bashio::config.has_value 'home_assistant_token'; then
    bashio::log.fatal "home_assistant_token is empty."
    bashio::log.fatal "Create a long-lived access token under your profile (Security tab),"
    bashio::log.fatal "paste it into this app's Configuration tab, and start the app again."
    bashio::exit.nok
fi

HA_URL="$(bashio::config 'home_assistant_url' 'http://homeassistant:8123')"
HA_TOKEN="$(bashio::config 'home_assistant_token')"
MAVERICK_LOG_LEVEL="$(bashio::config 'log_level' 'info')"
MAVERICK_API_TOKEN="$(bashio::config 'api_token' '')"
MAVERICK_BASE_URL="$(bashio::config 'base_url' '')"

# ---------------------------------------------------------------- base URL --
# Panels that pull frames need an address they can reach, which is never the
# container's own. Without an explicit option, use the host's first IPv4
# address; bashio reports it with its prefix length, hence the cut.

if [[ -z "${MAVERICK_BASE_URL}" ]]; then
    host_ip="$(bashio::network.ipv4_address 2>/dev/null | head -n 1 | cut -d/ -f1 || true)"
    if [[ -n "${host_ip}" && "${host_ip}" != "null" ]]; then
        MAVERICK_BASE_URL="http://${host_ip}:5000"
        bashio::log.info "base_url is not set; panels will be told to fetch from ${MAVERICK_BASE_URL}"
    else
        bashio::log.warning "base_url is not set and the host address could not be read;"
        bashio::log.warning "panels that pull frames will not know where to fetch from."
    fi
fi

# -------------------------------------------------------------------- MQTT --
# Explicit options win. Otherwise the Mosquitto broker app, when installed,
# hands over its host and credentials through the Supervisor (services:
# mqtt:want in config.yaml). Neither present means no MQTT, which is allowed:
# displays then do not appear as Home Assistant devices.

MQTT_ENABLED=false
MQTT_HOST=core-mosquitto
MQTT_PORT=1883
MQTT_USERNAME=""
MQTT_PASSWORD=""

if bashio::config.has_value 'mqtt_host'; then
    MQTT_ENABLED=true
    MQTT_HOST="$(bashio::config 'mqtt_host')"
    MQTT_PORT="$(bashio::config 'mqtt_port' '1883')"
    MQTT_USERNAME="$(bashio::config 'mqtt_username' '')"
    MQTT_PASSWORD="$(bashio::config 'mqtt_password' '')"
    bashio::log.info "MQTT: using the broker from the app options (${MQTT_HOST}:${MQTT_PORT})"
elif bashio::services.available 'mqtt'; then
    MQTT_ENABLED=true
    MQTT_HOST="$(bashio::services 'mqtt' 'host')"
    MQTT_PORT="$(bashio::services 'mqtt' 'port')"
    MQTT_USERNAME="$(bashio::services 'mqtt' 'username')"
    MQTT_PASSWORD="$(bashio::services 'mqtt' 'password')"
    bashio::log.info "MQTT: using the Mosquitto broker app (${MQTT_HOST}:${MQTT_PORT}); displays will appear as devices"
else
    bashio::log.notice "MQTT: no broker configured and the Mosquitto broker app is not installed;"
    bashio::log.notice "displays will not appear as Home Assistant devices. Set mqtt_host to change that."
fi

export HA_URL HA_TOKEN MAVERICK_LOG_LEVEL MAVERICK_API_TOKEN MAVERICK_BASE_URL
export MQTT_ENABLED MQTT_HOST MQTT_PORT MQTT_USERNAME MQTT_PASSWORD

# ------------------------------------------------------------------ config --

if [[ ! -f "${CONFIG_FILE}" ]]; then
    bashio::log.info "No ${CONFIG_FILE} yet; writing the starter configuration with one example display."
    bashio::log.info "Edit it in this app's configuration folder (addon_configs) and restart the app."
    cp "${TEMPLATE}" "${CONFIG_FILE}"
fi

# Relative paths in the config (the file transport's default ./out, for one)
# then land next to it, where the user can see them.
cd "${CONFIG_DIR}"

bashio::log.info "Starting $(maverick --version)"
exec maverick serve -c "${CONFIG_FILE}" --host 0.0.0.0 --port 5000
