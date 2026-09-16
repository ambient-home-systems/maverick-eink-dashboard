# Changelog

## 0.3.0

- **Displays can be added, edited and deleted from the web UI**, with no more
  hand-editing `/config/maverick.yaml` and restarting: an *Add display*
  dialog, and an *Edit* drawer on every card covering every setting the
  display has, including a live *Preview* before you save.
- **A display can now show several dashboards, one after another.** Add pages
  in the Edit drawer, turn on rotation, and the panel cycles through them on
  its own; a picker on the card and an MQTT select let you jump to one
  directly.
- **The Dashboard field now offers your actual Home Assistant dashboards** in
  a dropdown, instead of asking you to type or paste a URL.
- **Each card shows its render history** — an expandable log of past renders,
  what triggered each one, and why any of them failed.
- **Each card can show what the dashboard looked like before it was
  converted for the panel**, next to the converted frame, to tell a rendering
  problem from a conversion one at a glance.
- **The web UI updates itself** — cards refresh on their own, and buttons no
  longer reload the whole page.
- **"MQTT off" now explains what enabling it gets you and how**, instead of
  just stating it.
- **Fixes: the web UI and panel-preview images could be read by anyone on
  your network even with an access token set**, and the generated ESPHome
  configuration leaked that token to anyone who could reach the app. Both are
  closed; if you use an access token, update as soon as you can.
- **Fixes the card grid scrolling sideways on a phone.**

## 0.2.7

- **The same app as 0.2.6, released under a tag that works.** Nothing in the
  app changed. The 0.2.6 tag pointed at the previous release's code, so if
  0.2.6 was offered to you and did not fix the missing **Link with Home
  Assistant** button, that is why — update again. Installing 0.2.7 fresh needs
  nothing special.

## 0.2.6

- **Fixes "Set base_url first" blocking the link button.** The app is supposed
  to work out the address panels fetch from by itself when you leave
  **Base URL for panels** empty, but it was never granted the Home Assistant
  API access it needs to read the host's address — so it got nothing, and the
  web UI offered no **Link with Home Assistant** button. The app now requests
  read-only access to Home Assistant's information endpoints for this.
- **The message now says where to set it**, if you would rather set the
  address by hand: the **Base URL for panels** option on the Configuration tab.

  If you are on 0.2.5 and stuck on this, setting that option unblocks you
  without waiting for the update.

## 0.2.5

- **Fixes the web UI when opened through Home Assistant.** Since 0.2.3 the
  **Open Web UI** button opened the page embedded in Home Assistant, but the
  page's own links did not survive the move: previews showed as broken images
  and **Link with Home Assistant** failed with an error instead of sending you
  to the login page. It works now, embedded or on port 5000.
- **Fixes a broken credential leaving you stuck.** If the app held a
  credential Home Assistant would not accept, the page claimed "Home Assistant
  connected" and offered no **Link with Home Assistant** button, so there was
  no way to replace it. The page now tells you it is not connected and offers
  the button.

## 0.2.4

- **Fixes a fresh install failing with "Invalid client id".** Before you had
  linked anything, the app started as though an account were already linked
  and every render failed against Home Assistant. Unset options were reaching
  the service as the text `null` instead of as nothing, which also left the
  **Link with Home Assistant** button refusing to start (it saw `null` as the
  base URL) and put an `api_token` nobody knew in front of the endpoints
  panels pull frames from. If you hit this, update and restart: no
  configuration change is needed.

## 0.2.3

- **Open Web UI now opens inside Home Assistant.** The button used to link to
  `http://[HOST]:5000/`, which Home Assistant resolves to your external
  address (for example a Nabu Casa URL) when you are connected remotely —
  and a raw port is never reachable through that connection. Ingress support
  proxies the setup UI through the Supervisor instead, so the button opens it
  embedded in Home Assistant on any connection. Port 5000 stays published for
  panels that pull frames directly.

## 0.2.2

- **Fixes the app exiting the moment it starts.** The start script passed the
  config file to `maverick` in a position its command-line parser does not
  accept, so the service exited with a usage message before doing anything at
  all — on every start, since the app was first published. This was the real
  cause of "the app will not start"; the 0.2.1 notes below describe a second,
  genuine problem that sat behind it and would have bitten next.

  Update to 0.2.2 and start the app; a fresh install gets it straight away.
  This is the first release where a working app is actually offered as an
  update, so no rebuild by hand is needed.

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
