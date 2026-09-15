# Maverick

Put a live Home Assistant dashboard on an e-ink panel. The app loads the
dashboard in a bundled Chromium, restyles it for ink, quantises it to the
panel's real ink colours, refuses to ship a blank or illegible frame, and
delivers it over BLE (OpenDisplay tags, through Home Assistant's Bluetooth),
MQTT, HTTP pull (ESPHome, Kindle, TRMNL), a webhook or a file.

Install it, paste a long-lived access token on the Configuration tab, start it,
and edit `maverick.yaml` in the app's configuration folder to describe your
panels. The Documentation tab has the rest.

**Not yet run on a Home Assistant installation by the project.** The image
builds and Chromium starts inside it in CI; please report what happens on your
system at
https://github.com/ambient-home-systems/maverick-eink-dashboard/issues.
