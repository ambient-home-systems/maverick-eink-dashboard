Suits anything that fetches on its own schedule rather than being reached
by Maverick: an ESP32 running ESPHome's `online_image`, a jailbroken Kindle
or Kobo polling in a loop, or a TRMNL device in bring-your-own-server mode.
These are almost always battery devices that spend most of their life
asleep, which is exactly what a pull transport is for.

Nothing can be pushed to a sleeping device, so "delivered" here means
"published and waiting to be collected": `deliver()` stores the frame in
the server's frame store and returns `DeliveryResult.awaiting_pull`, and the
real confirmation is the device's own fetch of
`/api/displays/{id}/frame`, recorded separately so `/api/displays` can
report when a panel last woke. See the module docstring in
`transports/base.py` for the push-versus-pull distinction this transport is
built around.

Failures this transport can return, and what to do about each:

| Message | Cause | Fix |
| --- | --- | --- |
| `frame store unavailable (server not running)` | `deliver()` ran outside `maverick serve` (there is no HTTP server to publish through, and so nowhere for a device to pull from). | Run this display under `maverick serve`, not `maverick render`, or accept that a bare `render` only writes the file passed to `--out`. |
| `server.base_url is not set, so devices cannot be told where to fetch from. Set it to a URL reachable from the panel.` (from `probe`) | `server.base_url` is empty, so the frame URL handed to the device (and printed by `maverick check`) would be meaningless. | Set `server.base_url` to the URL the panel can reach this server at. |
