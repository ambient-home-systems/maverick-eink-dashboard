# Changelog

## 0.2.1

- **Updating to this version fixes an app that stops right after starting.**
  0.2.0's image installed the package from a commit predating account linking,
  while the `maverick.yaml` written on the first start uses the keys that
  release added; the configuration models reject keys they do not know, so the
  service exited before its web UI — and so before **Link with Home
  Assistant** — could be reached. The version number is what the Supervisor
  offers an update against, which is why this is a release rather than a quiet
  fix: 0.2.0 is now installable and startable from a clean slate.
- `${VAR:-default}` in `maverick.yaml` now falls back for a variable that is
  set to the empty string as well as one that is unset, as `:-` does in a
  shell. The app's `run.sh` exports a value for every substitution it writes,
  empty for the options you have not filled in, so a default written beside
  the reference now survives that.
- The repository tests that the package the image installs accepts the starter
  `maverick.yaml` the app writes, so the mismatch above cannot return
  unnoticed.

## 0.2.0

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
