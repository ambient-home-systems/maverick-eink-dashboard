# A jailbroken Kindle or Kobo, over `http_pull`

*Last reviewed against commit `d103e74`.*

> **Not run on hardware by this project.** No Kindle and no Kobo has drawn a
> Maverick frame. The client below *was* run, against a real Maverick server on
> a developer machine, with the drawing command stubbed out — see
> [What was verified](#what-was-verified). Everything about the device itself is
> from the community projects linked here, not from a device we own.

An old e-reader is the best-value dashboard panel there is: a 300 dpi
sixteen-grey screen, a battery, Wi-Fi and a Linux userland, for the price of a
second-hand Paperwhite. What it does not have is any way to be pushed to — so
it polls, exactly like an ESP32 does, and the same
[pull protocol](../reference/http-api.md#the-pull-protocol) serves it.

1. [The panels](#the-panels)
2. [Configure the display](#configure-the-display)
3. [The frame URL](#the-frame-url)
4. [A polling client in POSIX shell](#a-polling-client-in-posix-shell)
5. [Drawing it: `fbink` and `eips`](#drawing-it-fbink-and-eips)
6. [The jailbreak, which is not ours](#the-jailbreak-which-is-not-ours)
7. [The `file` transport, for rsync instead of polling](#the-file-transport-for-rsync-instead-of-polling)
8. [What was verified](#what-was-verified)
9. [What to check first when it does not work](#what-to-check-first-when-it-does-not-work)

## The panels

| Panel id | Device | Resolution | Scheme | dpi | Format |
| --- | --- | --- | --- | --- | --- |
| `kindle-paperwhite-3` | Kindle Paperwhite 3 | 1072×1448 | gray16 | 300 | png |
| `kindle-basic` | Kindle 4 / Touch | 600×800 | gray16 | 167 | png |
| `kobo-clara` | Kobo Clara HD (Nickel or KOReader) | 1072×1448 | gray16 | 300 | png |

All three default to `http_pull` and to **PNG**, which is the point: unlike a
bare panel, an e-reader has a real image decoder, so there is no reason to send
it packed bytes it would have to unpack itself. `default_format: png` on the
catalogue entry is what makes `frame_format` resolve to PNG without you setting
it ([how resolution works](../reference/configuration.md#panel-profiles-and-per-display-overrides)).

All three are portrait and none claims partial refresh, so Maverick asks for a
full refresh every time. On these devices that flag changes nothing anyway —
how the screen is drawn is your drawing tool's business, not Maverick's.

300 dpi matters more than it looks. The theme's type scale is millimetre-based
and derived from `dpi`, so a dashboard rendered for `kindle-paperwhite-3` gets
physically legible type rather than 13-pixel text that is 1 mm tall. If you
override `width`/`height` for a device not listed here, override `dpi` too.

## Configure the display

```yaml
server:
  base_url: http://maverick.local:5000
  api_token: ${MAVERICK_TOKEN}      # optional, but see below

displays:
  - id: kitchen
    name: Kitchen Kindle
    panel: kindle-paperwhite-3
    dashboard: /lovelace-eink/kitchen
    schedule:
      every: 10m
    transport:
      type: http_pull
```

A jailbroken e-reader on your network is not a hardened device, and the frame
endpoint is the one route that exposes what your dashboard says. If you set
`server.api_token`, the client below sends it as `Access-Token`; without a
token, everything works the same and anyone on the network can read the frame.

## The frame URL

```text
GET {server.base_url}/api/displays/{display id}/frame
```

One URL, always the current frame. What the device does with the response
headers is what decides its battery life:

| Header | Use it for |
| --- | --- |
| `ETag` | Send it back as `If-None-Match`. A match answers **304** with no body — no download, and no screen refresh. |
| `X-Maverick-Next-Refresh` | Seconds to wait before asking again. It is the display's `schedule.every`, or 900 for a cron schedule. |
| `X-Maverick-Checksum` | The same identity as the ETag, unquoted, for a client that would rather not parse ETags. |
| `X-Maverick-Width` / `-Height` | Sanity-check before drawing. A mismatch means the config changed. |
| `Date` | The server clock. An e-reader that has been offline for a week is a useful thing to be able to set the time on. |

A **404** means nothing has been rendered yet — normal on a freshly started
server, and self-correcting. A **401** means the token is wrong or missing.

## A polling client in POSIX shell

[`clients/frame_poll.sh`](clients/frame_poll.sh) is the whole client. It is
`/bin/sh`, `curl` and `awk` — all three are on a jailbroken Kindle, and on a
Kobo with KOReader or NiLuJe's packages.

```sh
BASE=${BASE:-http://maverick.local:5000}
DISPLAY_ID=${DISPLAY_ID:-kitchen}
TOKEN=${TOKEN:-}
DIR=${DIR:-/mnt/us/maverick}
FRAME="$DIR/frame.png"
HDR="$DIR/frame.hdr"
ETAG="$DIR/etag"

mkdir -p "$DIR"
[ -f "$ETAG" ] || : > "$ETAG"

while true; do
    code=$(curl -s -o "$FRAME.new" -D "$HDR" -w '%{http_code}' \
        ${TOKEN:+-H "Access-Token: $TOKEN"} \
        -H "If-None-Match: $(cat "$ETAG")" \
        "$BASE/api/displays/$DISPLAY_ID/frame")

    case "$code" in
        200)
            mv "$FRAME.new" "$FRAME"
            awk 'tolower($1)=="etag:"{printf "%s", $2}' "$HDR" | tr -d '\r' > "$ETAG"
            fbink -q -c -g file="$FRAME" -f
            ;;
        304) ;;                                  # already on screen: do nothing
        401) echo "token rejected"; exit 1 ;;
        404) ;;                                  # nothing rendered yet; try later
        *)   echo "HTTP $code" ;;
    esac

    nap=$(awk 'tolower($1)=="x-maverick-next-refresh:"{printf "%d", $2}' "$HDR")
    sleep "${nap:-900}"
done
```

Run it:

```console
$ BASE=http://maverick.local:5000 DISPLAY_ID=kitchen TOKEN=s3cret ./frame_poll.sh
```

Four details are deliberate:

* **The ETag is stored on disk**, not in a variable, so a reboot or a crash does
  not cause one pointless full-screen redraw. It is written verbatim, quotes
  included, because the server compares the string exactly and does not
  understand weak validators.
* **The frame is downloaded to `.new` and renamed.** If the download is cut off
  half way, the last good frame is still on disk and still on screen.
* **304 does nothing at all.** Not "redraw the same image" — nothing. That is
  the entire saving; an e-ink refresh costs orders of magnitude more energy than
  the request that avoided it.
* **The sleep comes from the server**, so changing `schedule.every` in
  `config.yaml` changes the device's polling without touching the device.

Keep it running with whatever your jailbreak provides — a KUAL extension, an
`init` script, or `nohup ./frame_poll.sh &` from an SSH session for a first
test. On a Kindle you will also want to stop the stock UI from drawing over
you; how to do that is the jailbreak's business, not Maverick's.

## Drawing it: `fbink` and `eips`

Two tools, one recommendation.

**`fbink`** (from [NiLuJe's `FBInk`](https://github.com/NiLuJe/FBInk)) is the
maintained option and works on both Kindle and Kobo:

```console
$ fbink -q -c -g file=/mnt/us/maverick/frame.png -f
```

`-q` quiet, `-c` clear first, `-g file=…` draw an image, `-f` force a full
refresh. Add `-g file=…,halign=center,valign=center` if your frame is smaller
than the screen; better, set `width`/`height` on the display so it is not.

**`eips`** is Amazon's own tool, already on every Kindle:

```console
$ eips -c                       # clear
$ eips -g /mnt/us/maverick/frame.png
```

It is fine for a quick test, but what it accepts varies by firmware
generation — some builds want a specific bit depth, and the flags are not
consistent across models. Check `eips` with no arguments on *your* device before
building anything on it. On Kobo there is no `eips` at all.

Either way, the image you hand it is already the right size, already sixteen
greys, and already dithered for e-ink — so no scaling, no `convert`, no
`-dither` flags. Anything you do to the image between download and draw is
undoing work Maverick did.

## The jailbreak, which is not ours

**This page does not document jailbreaking, and you should be suspicious of any
page that does it in passing.** The procedure depends on your exact model and
firmware version, it changes, and a wrong step bricks the device.

The project to read is
[**hass-lovelace-kindle-screensaver**](https://github.com/sibbl/hass-lovelace-kindle-screensaver),
which `docs/architecture.md` already cites as prior art. It is the established
way to put a Home Assistant dashboard on a jailbroken Kindle, and its
documentation covers the device half properly: which models, the jailbreak, KUAL,
the online screensaver hook, and keeping the screen on.

The two projects overlap on purpose. That project renders on its server and
pushes an image the Kindle's screensaver hook displays; Maverick does the
rendering differently — a panel profile, a millimetre-based type scale, content
aware dithering and a lint pass — and serves it over a conditional-GET endpoint
instead. If you already run its client, pointing it at Maverick's frame URL is
the smallest possible change; if you are starting fresh, read its device
documentation and use the shell client above.

## The `file` transport, for rsync instead of polling

If the device already syncs a directory, or you would rather push than poll, use
the [`file`](../reference/transports.md#file) transport and let something else
carry the file:

```yaml
displays:
  - id: kitchen
    panel: kindle-paperwhite-3
    transport:
      type: file
      path: /srv/maverick/frames
      filename: kitchen.png
      write_preview: false
```

Then, from anywhere:

```console
$ rsync -az /srv/maverick/frames/kitchen.png kindle:/mnt/us/maverick/frame.png
```

Three things make this safe to point a dumb poller at:

* **The write is atomic.** The frame goes to `<name>.tmp` and is renamed over
  the target, so a reader never sees a half-written file and never draws a torn
  frame.
* **`filename` is fixed if you set it**, so the path on the far side never
  changes. Unset, the file is `<display id>.<ext>`, where the extension comes
  from the frame format — `.png` here.
* **`write_preview: true`** additionally writes `<display id>-preview.png`, a
  viewable render of the quantised frame. Useful while you are tuning a theme,
  pointless in production — and note it is named after the display id even when
  you set `filename`.

What you give up is the 304. A push has no way to know what the device is
already showing, so every sync is a transfer, and whatever draws the file has to
decide for itself whether anything changed. `schedule.skip_unchanged` (on by
default) means an unchanged frame is not re-delivered at all, which recovers
most of that on the server side.

## What was verified

Checked on this repository, against a running `maverick serve`:

* `clients/frame_poll.sh` passes `sh -n`, and was run for three iterations
  against a live server with `fbink` replaced by `echo`. The first pass fetched
  **200** and wrote the frame; the second and third sent `If-None-Match` and got
  **304** with no download and no draw; the ETag file held `"3b0a35f9e7324322"`
  and the sleep interval parsed out of `X-Maverick-Next-Refresh` as `300`,
  matching `every: 5m`.
* With `server.api_token` set, the same request without a token returned
  **401** `{"detail":"invalid or missing API token"}`, and the `Access-Token`
  header the script sends returned **200**.
* The frame served for a PNG display is a real PNG of the configured geometry
  (`file` reports `PNG image data, 800 x 480, 8-bit colormap`).
* The `file` transport wrote `<id>.png` and, with `write_preview: true`,
  `<id>-preview.png`.

Not checked: any Kindle, any Kobo, `fbink`, `eips`, any jailbreak, and the
1072×1448 geometry — the verification above used an 800×480 display, because
the checks were about the protocol, not the panel.

## What to check first when it does not work

| What you see | What it means | What to do |
| --- | --- | --- |
| `curl` prints nothing and the screen stays blank | The device cannot reach the server. | `curl -v $BASE/health` from the device. If that fails, it is DNS or routing: `maverick.local` needs mDNS, which e-readers often lack. Use an IP address in `server.base_url` and in `BASE`. |
| **401** on every request | `server.api_token` is set and `TOKEN` is not, or is wrong. | Set `TOKEN`. Any of `Access-Token`, `Authorization: Bearer` or `?token=` is accepted; the script uses the first. |
| **404** forever | No frame has ever been rendered for this display. | Check `maverick serve` is running and the display is enabled; `POST /api/displays/<id>/render` to force one. A 404 right after a restart is normal and clears itself. |
| Log line `[kitchen] FAILED: frame store unavailable (server not running)` | The render happened outside `maverick serve`, so the frame was never published. | Use `maverick serve`. `maverick render` alone has no store to publish into. |
| `server.base_url is not set, so devices cannot be told where to fetch from.` | `base_url` is empty. | Set it — it is what goes into every URL a device is given. |
| Always **200**, never **304** | The ETag is not making the round trip. | Check the `etag` file has quotes in it. `If-None-Match: 0123…` without quotes never matches; `"0123…"` does. |
| Always **304**, screen never updates | The frame genuinely has not changed, or the device is drawing from a stale file. | `curl -sD - -o /dev/null <url>` and compare `x-maverick-checksum` with what you last drew. Force a new frame with `POST /api/displays/<id>/render?force=true`. |
| The image draws, but rotated or clipped | The device's screen is not the panel geometry you configured. | Compare `X-Maverick-Width`/`-Height` with your screen. Set `width`, `height` and `rotation` on the display rather than transforming the image on the device. |
| Log line `could not write /srv/maverick/frames: …` (file transport) | The directory could not be created or written. | Check permissions and free space on `transport.path`. |

Every fetch, 304 included, is recorded: `last_pulled_at` in
`GET /api/displays/<id>` is how you tell a sleeping device from a dead one.
