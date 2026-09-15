# Changelog

## Unreleased

- **Link with Home Assistant** in the Web UI: obtains a credential through
  Home Assistant's own authorization flow and saves it to the app's options,
  so no long-lived token has to be copied by hand. New
  `home_assistant_refresh_token` and `home_assistant_client_id` options hold
  the result; both are written for you.
- The app no longer refuses to start when `home_assistant_token` is empty. It
  warns instead, because the Web UI is where linking happens and it has to be
  running to be reached.

## 0.1.0

- First release of the Home Assistant app: a Debian image with Chromium and
  the Maverick package pinned to a commit; options for the Home Assistant
  connection, the log level, an optional API token, the pull base URL and an
  optional MQTT broker (the Mosquitto broker app is used automatically); a
  starter `maverick.yaml` written on first start; `/media` and `/share` mapped
  read-write for the OpenDisplay and file transports.
