#!/usr/bin/env bash
# The local dev loop from CONTRIBUTING.md's "Running against a real Home
# Assistant": docker-compose.dev.yml stands up a real Home Assistant and
# Mosquitto broker next to Maverick, built from the root Dockerfile.
set -o errexit -o pipefail -o nounset

readonly COMPOSE_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/docker-compose.dev.yml"

compose() {
    docker compose -f "${COMPOSE_FILE}" "$@"
}

usage() {
    cat <<'EOF'
Usage: scripts/dev.sh <command>

  up            Build and start homeassistant, mosquitto and maverick.
  down          Stop and remove the dev stack.
  logs          Follow logs from all three services.
  render <id>   Render one display now (omit <id> to render all).
  check         Run `maverick check` inside the running container.

Set HA_TOKEN in the environment before `up` once Home Assistant onboarding
is done: open http://localhost:8123, create the user, then create a
long-lived access token under that user's profile, Security tab.
EOF
}

cmd_up() {
    if [[ -z "${HA_TOKEN:-}" ]]; then
        echo "warning: HA_TOKEN is not set. maverick will start, but every render" >&2
        echo "will fail until you finish Home Assistant onboarding at" >&2
        echo "http://localhost:8123, create a long-lived access token, and re-run" >&2
        echo "with HA_TOKEN=<token> scripts/dev.sh up." >&2
    fi
    compose up --build -d
}

case "${1:-}" in
    up)
        cmd_up
        ;;
    down)
        compose down
        ;;
    logs)
        compose logs -f
        ;;
    render)
        if [[ -n "${2:-}" ]]; then
            compose exec maverick maverick -c /config/maverick.yaml render "${2}"
        else
            compose exec maverick maverick -c /config/maverick.yaml render
        fi
        ;;
    check)
        compose exec maverick maverick -c /config/maverick.yaml check
        ;;
    *)
        usage
        exit 1
        ;;
esac
