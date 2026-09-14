# TRMNL, in bring-your-own-server mode

> **Not run on hardware by this project.** No TRMNL device has been pointed at
> Maverick. Both server endpoints *were* exercised against a running
> `maverick serve`, and the transcript below is that real output — see
> [What was verified](#what-was-verified). What the firmware does with it is
> read from TRMNL's BYOS protocol, not observed.

[TRMNL](https://usetrmnl.com/) is a finished 7.5" e-ink panel with a
bring-your-own-server mode: point the device at your own host and it stops
talking to the vendor's cloud entirely. Maverick implements the two endpoints
that mode needs, so a TRMNL panel is a display like any other — except that it
identifies itself by MAC instead of being configured with a URL.

1. [Configure the display](#configure-the-display)
2. [The `mac` key](#the-mac-key)
3. [Why BMP](#why-bmp)
4. [The two endpoints](#the-two-endpoints)
5. [The handshake, as curl](#the-handshake-as-curl)
6. [The token](#the-token)
7. [What was verified](#what-was-verified)
8. [What to check first when it does not work](#what-to-check-first-when-it-does-not-work)

## Configure the display

```yaml
server:
  base_url: http://maverick.local:5000    # the device must be able to reach this
  api_token: ${MAVERICK_TOKEN}            # optional; see The token below

displays:
  - id: hallway
    name: Hallway
    panel: trmnl-7in5
    dashboard: /lovelace-eink/hallway
    schedule:
      every: 15m
    transport:
      type: http_pull
      mac: A4:CF:12:34:56:78
```

That is the whole configuration. There is nothing to set on the device beyond
telling it your server's address, which is done in its own setup flow.

`trmnl-7in5` is 800×480, `mono`, ~124 dpi, `default_transport: http_pull` and
`default_format: bmp` ([catalogue](../reference/panels.md#trmnl)). It does not
claim partial refresh, so every frame is a full one.

## The `mac` key

TRMNL firmware does not send a display id — it sends its MAC address in an
**`ID`** header, and expects the server to work out which screen that is.
`_display_for_mac` in `server/api.py` walks the enabled displays looking for a
`mac` in the transport block:

```python
for display in application.config.enabled_displays:
    configured = str(getattr(display.transport, "mac", "") or "").upper()
    if configured and configured.replace("-", ":") == mac.replace("-", ":"):
        return display
```

Three things follow:

* **Case and separator do not matter.** `a4-cf-12-34-56-78` and
  `A4:CF:12:34:56:78` match the same display.
* **Only enabled displays match.** `enabled: false` makes the device invisible
  to the handshake, which is a tidy way to take a panel out of service.
* **`mac` is not an `http_pull` option.** It will not appear in that
  transport's table in [the transport reference](../reference/transports.md#http_pull),
  which says the transport takes no options of its own — and that is accurate.
  `TransportConfig` permits extra keys and hands them to the transport, but this
  one is read by the API layer instead, where the handshake lives. The transport
  never sees it. (The same key name *is* an option on
  [`opendisplay`](../reference/transports.md#opendisplay), where it means the
  BLE tag's address. Different transport, different reader, same word.)

Find the MAC in the TRMNL device's own setup screen, or in your router's DHCP
leases. If it is wrong, both endpoints answer **404** and the device has nothing
to display.

## Why BMP

`trmnl-7in5` sets `default_format: bmp`, so the frame endpoint serves
`image/bmp` rather than the PNG most displays get. TRMNL's firmware draws a
1-bit BMP at the panel's native resolution; that is what the format exists for,
and it is why the catalogue pins it rather than leaving the transport default
(which would be PNG).

The numbers line up exactly: 800 × 480 pixels at one bit each is 48,000 bytes,
plus a 62-byte header — a 48,062-byte file, which is what the server sends.

```console
$ file hallway.bmp
hallway.bmp: PC bitmap, Windows 3.x format, 800 x 480 x 1, image size 48000,
             2 important colors, cbSize 48062, bits offset 62
```

You can override it per display with `frame_format`, but there is no good reason
to: the device wants BMP, and a PNG it cannot decode looks identical to a dead
server.

## The two endpoints

Neither is behind the API token — the device has no token when it first calls,
so gating the handshake on one would make setup impossible. Full detail in
[the HTTP API reference](../reference/http-api.md#trmnl-bring-your-own-server).

### `GET /api/setup`

Called once, when the device is claimed.

| Key | Value |
| --- | --- |
| `status` | The literal `200`, in the body as well as the HTTP status. |
| `api_key` | `server.api_token` if set, **otherwise the display id**. The firmware stores this and sends it back as `Access-Token` on every later request. |
| `friendly_id` | The first six characters of the display id, uppercased — `hallway` gives `HALLWA`. |
| `image_url` | `server.base_url` + `/api/displays/<id>/frame`. |
| `message` | `"welcome to maverick"`. |

An unmatched MAC gives HTTP **404** and
`{"status": 404, "message": "no display for MAC …"}`.

### `GET /api/display`

Called on every wake, before fetching the image.

| Key | Value |
| --- | --- |
| `status` | The literal `0` — TRMNL's "nothing wrong" code, not an HTTP status. |
| `image_url` | The frame URL again. |
| `filename` | `<display id>-<checksum>`, or `<display id>-pending` when nothing has been rendered yet. It changes exactly when the frame does, which is how the firmware knows there is something new. |
| `refresh_rate` | Seconds until the next wake — the display's `schedule.every`, or 900 for a cron schedule. The same number as `X-Maverick-Next-Refresh`. |
| `reset_firmware` | Always `false`. |
| `update_firmware` | Always `false`. |

`base_url` matters here in a way it does not for an ESPHome panel: the device is
*told* where the image is, on every wake. Leave `server.base_url` unset and
`image_url` is a bare path the firmware cannot resolve.

## The handshake, as curl

This is the firmware's sequence, done by hand. Run it from another machine to
prove the server side works before you blame the device.

```console
$ curl -s -H 'ID: A4:CF:12:34:56:78' http://maverick.local:5000/api/setup
{
    "status": 200,
    "api_key": "s3cret",
    "friendly_id": "HALLWA",
    "image_url": "http://maverick.local:5000/api/displays/hallway/frame",
    "message": "welcome to maverick"
}

$ curl -s -H 'ID: A4:CF:12:34:56:78' http://maverick.local:5000/api/display
{
    "status": 0,
    "image_url": "http://maverick.local:5000/api/displays/hallway/frame",
    "filename": "hallway-3b0a35f9e7324322",
    "refresh_rate": 900,
    "reset_firmware": false,
    "update_firmware": false
}

$ curl -sD - -o hallway.bmp \
    -H 'ID: A4:CF:12:34:56:78' -H 'Access-Token: s3cret' \
    http://maverick.local:5000/api/displays/hallway/frame
HTTP/1.1 200 OK
etag: "3b0a35f9e7324322"
cache-control: no-cache
x-maverick-checksum: 3b0a35f9e7324322
x-maverick-width: 800
x-maverick-height: 480
x-maverick-colors: 2
x-maverick-next-refresh: 900
content-length: 48062
content-type: image/bmp

$ curl -sD - -o /dev/null \
    -H 'Access-Token: s3cret' \
    -H 'If-None-Match: "3b0a35f9e7324322"' \
    http://maverick.local:5000/api/displays/hallway/frame
HTTP/1.1 304 Not Modified
etag: "3b0a35f9e7324322"
x-maverick-next-refresh: 900
```

Note the third call: the checksum in `filename` from `/api/display` is the same
`3b0a35f9e7324322` that comes back as the ETag. Two independent ways for the
firmware to notice nothing has changed, and the 304 is the one that saves the
refresh.

An unknown MAC, for comparison:

```console
$ curl -s -o /dev/null -w '%{http_code}\n' \
    -H 'ID: DE:AD:BE:EF:00:01' http://maverick.local:5000/api/setup
404
$ curl -s -H 'ID: DE:AD:BE:EF:00:01' http://maverick.local:5000/api/setup
{"status":404,"message":"no display for MAC DE:AD:BE:EF:00:01"}
```

## The token

With `server.api_token` unset, nothing here needs authentication and the
handshake hands back the display id as the `api_key` — a placeholder the
firmware stores and sends, and which nothing checks.

With it set, the shape is:

* `/api/setup` and `/api/display` stay open, and `/api/setup` returns the real
  token as `api_key`.
* The **frame** endpoint requires it. The firmware presents the stored key in an
  `Access-Token` header, so `_require_token` accepts that header alongside
  `Authorization: Bearer` and `?token=`. Both spellings the BYOS documentation
  uses — `Access-Token` and `ACCESS_TOKEN` — are accepted, because header names
  are case-insensitive but hyphens and underscores are not interchangeable.
* All three routes to the same secret are compared with `hmac.compare_digest`.

This is [the decision recorded in P0.2](../documentation-plan.md#p02--verify-and-fix-three-behaviours-the-documentation-will-advertise):
accept the header the firmware already sends, rather than append the token to
`image_url`. Putting it in the URL would have worked too, but it would have
written the secret into a string the device stores and every proxy and access
log records. The caveat recorded there still stands: this relies on current
firmware attaching its headers to the image download when the image is on the
same host as the API. Firmware old enough to do a bare `GET` for the image would
need the token in the URL, and Maverick does not offer that today.

## What was verified

Run against a live `maverick serve` on this repository, with a `trmnl-7in5`
display carrying `transport.mac: A4:CF:12:34:56:78`:

* Every JSON body and every header block above is real output. Only the host
  and port have been rewritten (the test server ran on `127.0.0.1:5099`), and
  the transcript's `image_url` values are shown rewritten to match.
* `/api/setup` returned `api_key: "hallway"` — the display id — with no
  `server.api_token`, and `api_key: "s3cret"` with one set.
* `friendly_id` came back `HALLWA`.
* `ID: a4-cf-12-34-56-78` matched the same display as `A4:CF:12:34:56:78`.
* An unknown MAC returned HTTP 404 with the body shown.
* The served frame was 48,062 bytes of `image/bmp`, and `file` reported
  `PC bitmap, Windows 3.x format, 800 x 480 x 1, image size 48000`.
* `refresh_rate: 900` matched the display's `every: 15m`.
* With a token configured, the frame endpoint returned **401** with no token,
  and **200** for each of `Access-Token`, `Authorization: Bearer` and `?token=`.

Not checked: any TRMNL device, the firmware's behaviour, its battery life, or
what it does with a response it does not like.

## What to check first when it does not work

| What you see | What it means | What to do |
| --- | --- | --- |
| Device says it cannot reach the server | It is not resolving or routing to `base_url`. | Put an IP address in `server.base_url`; do not rely on mDNS. Check the server is bound to something other than `127.0.0.1` — `server.host: 0.0.0.0`. |
| **404** from `/api/setup` or `/api/display` | No enabled display has that MAC. | Compare the `mac` in your config against the device's, character by character. Check `enabled` is not `false`. The MAC is matched against `transport.mac`, nowhere else. |
| Handshake succeeds, the screen stays blank | The device is fetching `image_url` and getting something it cannot use. | Fetch `image_url` yourself. **401**: the token. **404**: nothing rendered yet — force one with `POST /api/displays/<id>/render`. **200** with `content-type: image/png`: the display's `frame_format` was overridden away from `bmp`. |
| `image_url` is `/api/displays/hallway/frame` with no host | `server.base_url` is unset. | Set it. The device cannot resolve a bare path. |
| Log line `[hallway] FAILED: frame store unavailable (server not running)` | The render ran outside `maverick serve`. | Only `maverick serve` publishes to the frame store the device pulls from. |
| `server.base_url is not set, so devices cannot be told where to fetch from.` | The `http_pull` probe's complaint, same cause. | As above. |
| The screen updates far less often than you expect | `refresh_rate` comes from `schedule.every`; a cron schedule always reports 900. | Set `schedule.every` rather than `cron` for a device that sleeps on the number you give it. |
| The screen never updates, but fetches are recorded | 304s: the frame has not changed. Correct, not broken. | `last_pulled_at` in `GET /api/displays/hallway` proves it is awake and asking. Force a change with `?force=true`. |
