# A Raspberry Pi and a Pimoroni Inky, over MQTT

> **Not run on an Inky by this project.** No Pimoroni panel has drawn a Maverick
> frame, and `inky` is not installed on the machine this was written on. The
> client's message handling *was* exercised against a real frame payload with
> stub `paho`/`inky` modules — see [What was verified](#what-was-verified).

A Raspberry Pi with an Inky HAT is mains-powered and always connected, which
makes it the opposite case from a battery panel: there is no reason to poll.
Maverick pushes the frame to an MQTT topic the moment it is rendered, and a
small client on the Pi draws it.

1. [The panels](#the-panels)
2. [Configure the display](#configure-the-display)
3. [What gets published](#what-gets-published)
4. [The client](#the-client)
5. [`full_refresh`, and what an Inky can do with it](#full_refresh-and-what-an-inky-can-do-with-it)
6. [The one thing to check on real hardware](#the-one-thing-to-check-on-real-hardware)
7. [Running it as a service](#running-it-as-a-service)
8. [What was verified](#what-was-verified)
9. [What to check first when it does not work](#what-to-check-first-when-it-does-not-work)

## The panels

| Panel id | Panel | Resolution | Scheme | dpi |
| --- | --- | --- | --- | --- |
| `inky-phat-bwr` | Inky pHAT | 212×104 | bwr | 111 |
| `inky-what-bwr` | Inky wHAT | 400×300 | bwr | 119 |
| `inky-impression-5in7` | Inky Impression 5.7" | 600×448 | acep7 | 132 |
| `inky-impression-7in3` | Inky Impression 7.3" | 800×480 | spectra6 | 128 |

All four default to `transport: mqtt` and none claims partial refresh — the
Inky library redraws the whole panel on every `show()`, so there is nothing to
claim.

The two Impressions are slow. A seven-colour or Spectra refresh takes tens of
seconds, during which the panel cycles through its inks visibly. Schedule them
in tens of minutes, not minutes.

## Configure the display

On the Maverick side:

```yaml
mqtt:
  enabled: true
  host: mqtt.local
  port: 1883
  username: maverick
  password: ${MQTT_PASSWORD}
  base_topic: maverick

displays:
  - id: kitchen
    name: Kitchen Inky
    panel: inky-impression-7in3
    dashboard: /lovelace-eink/kitchen
    schedule:
      every: 30m
    transport:
      type: mqtt
```

`mqtt.enabled: true` does two separate jobs: it turns on Home Assistant
discovery for *every* display, and it makes the `mqtt` transport available to
displays that ask for it. You get the buttons, the pause switch and the
diagnostic sensors in Home Assistant whether or not you use the transport —
see [the MQTT reference](../reference/mqtt.md) and
[the Home Assistant guide](../guides/home-assistant.md).

The transport has exactly one option:

| Option | Default | Notes |
| --- | --- | --- |
| `topic` | `<mqtt.base_topic>/display/<display id>` | Base topic for this display's two frame topics. Set it only if you need the frames somewhere else in the tree; it does not move the state, preview or discovery topics. |

## What gets published

Each delivery publishes two messages, `/meta` first and then `/frame`, both
**retained at QoS 1**:

```text
maverick/display/kitchen/meta     {"id": "kitchen", "width": 800, ...}
maverick/display/kitchen/frame    <the frame bytes>
```

Retained is what makes the client simple: a Pi that reboots gets the current
frame immediately, instead of a blank panel until the next render.

The metadata is every key from `MqttTransport.deliver`, and four of them are
what a client actually uses:

| Key | Use |
| --- | --- |
| `checksum` | Sixteen hex characters identifying the frame's content. Equal to the last one you drew means there is nothing to do. |
| `bytes` | Length of the `/frame` payload. Compare it with what arrived to spot a truncated message, or a retained `/meta` that has not caught up with a new `/frame`. |
| `full_refresh` | True when this frame should be drawn with a flashing full refresh to clear ghosting. |
| `format` | The wire format — `png` for an `mqtt` display unless you override `frame_format`. |

`width`, `height`, `color_scheme`, `id`, `name`, `sequence` and `trigger` are
there too; [the reference](../reference/mqtt.md#frames-for-your-own-client) lists
all of them.

**The payload is a PNG** because `mqtt` defaults to `FrameFormat.PNG`. Not an
ordinary photographic PNG: a palettised, mode-`P` image whose colours are the
panel's *measured* inks, already quantised and dithered for this exact panel.
An 800×480 Spectra 6 frame comes to about 4.5 KB — small enough that publishing
it retained on every render is unremarkable.

## The client

[`clients/inky_client.py`](clients/inky_client.py) is forty-odd lines:

```console
$ pip install paho-mqtt pillow inky
$ python inky_client.py --host mqtt.local --display kitchen
```

| Flag | Default | |
| --- | --- | --- |
| `--host` | *required* | Broker host. |
| `--port` | `1883` | |
| `--username` / `--password` | unset | If your broker needs them. |
| `--display` | *required* | The Maverick display id. |
| `--base-topic` | `maverick` | Must match `mqtt.base_topic`. |

What it does:

```python
panel = auto()                      # detects the Inky model over I2C
...
def on_frame(_client, _userdata, message):
    meta = state["meta"]
    if meta is None or meta["bytes"] != len(message.payload):
        return                      # the retained pair has not caught up yet
    if meta["checksum"] == state["drawn"] and not meta["full_refresh"]:
        return                      # already on the panel
    draw(message.payload, meta)
```

Three decisions worth explaining:

* **It waits for `/meta`.** Both topics are retained, so on connect the broker
  delivers both — but a client that joins mid-render can briefly see the
  previous `/meta` beside the new `/frame`. Comparing `meta["bytes"]` with the
  payload length is the cheap way to notice, and skipping is safe: another pair
  is coming.
* **It tracks what it drew.** A retained message is re-delivered on every
  reconnect. Without the `checksum` check, a flapping Wi-Fi link would mean a
  twenty-second Impression refresh every time it reconnected.
* **`auto()` detects the panel.** The Inky HATs carry an EEPROM the library
  reads over I2C, so nothing in the script names a model. If `auto()` raises,
  your I2C is not enabled or the HAT is not seated — nothing to do with
  Maverick.

`--base-topic` plus `--display` must reconstruct the topic Maverick publishes
to. If you set the transport's `topic` option, the script cannot work it out —
either drop that option or edit the one line that builds `base`.

## `full_refresh`, and what an Inky can do with it

Maverick sets `full_refresh` when the frame count since the last full refresh
reaches the display's cadence, when a render is forced, or — and this is the
case that matters here — **whenever the panel profile says
`supports_partial: false`**, which is true of all four Inky entries.

So on a catalogue Inky the flag is always true. That is not redundant: the
library's `show()` is always a full update anyway, so the only thing the flag
can change on this panel is *whether an unchanged frame is redrawn*. The client
reads it exactly that way — redraw when the checksum changed, or when
`full_refresh` asks for a ghost-clearing pass over an identical frame.

If you point the client at a display whose panel does support partial refresh,
the flag starts varying and the same line does the sensible thing.

## The one thing to check on real hardware

The frame arrives already quantised to the panel's inks. The client hands it to
the library as RGB:

```python
image = Image.open(io.BytesIO(payload)).convert("RGB")
panel.set_image(image)
panel.show()
```

`set_image` on an RGB image maps it onto the panel's own palette, with the
library's dithering. Because the incoming pixels already sit on (or very near)
palette colours, that second pass should be close to a no-op — but "should be"
is doing work in that sentence, and this is the line most likely to need
changing once someone runs it on a real Impression.

If it comes out muddy or speckled, the fix is to stop the second pass rather
than to tune it: build a mode-`P` image whose indices are the *library's*
palette order and pass that instead, since `set_image` uses a `P` image's
indices directly. Maverick's own indices are its own order, not the library's,
so they have to be remapped — nearest colour, once, at startup. The Impression
driver also takes a `saturation` keyword that the pHAT and wHAT drivers do not,
which is why the script does not pass one.

The same applies at the other end of the pipeline: if the colours are close but
wrong, that is Maverick's measured ink table rather than the library, and
`image.palette_overrides` is the knob ([configuration
reference](../reference/configuration.md#displaysimage)).

## Running it as a service

```ini
# /etc/systemd/system/inky-dashboard.service
[Unit]
Description=Maverick Inky client
After=network-online.target

[Service]
ExecStart=/usr/bin/python3 /home/pi/inky_client.py --host mqtt.local --display kitchen
Restart=always
RestartSec=10
User=pi

[Install]
WantedBy=multi-user.target
```

`Restart=always` is enough of a recovery strategy: the frame topic is retained,
so a restarted client draws the current frame within a second of reconnecting,
without waiting for the next render.

## What was verified

Checked on this repository:

* `clients/inky_client.py` compiles (`python -m py_compile`).
* Its two message callbacks and its `on_connect` subscription were run with
  stub `paho.mqtt.client` and `inky.auto` modules, against **a real frame
  payload** — the PNG a `inky-impression-7in3` display actually produced, taken
  from the `file` transport, which writes the same `frame.payload` the `mqtt`
  transport publishes. In order: a `/frame` before any `/meta` was ignored; a
  `/meta` + `/frame` pair drew once; a repeat with `full_refresh: true` drew
  again; a repeat with `full_refresh: false` did not; a truncated payload was
  ignored. `on_connect` subscribed to
  `maverick/display/porch/meta` and `maverick/display/porch/frame` at QoS 1.
* That payload is an 800×480 mode-`P` PNG containing five distinct colours,
  4,482 bytes.

Not checked: any Inky, any broker, `paho-mqtt` or the `inky` library — the
stubs stood in for all four. In particular, `panel.set_image()` and
`panel.show()` were recorded, not executed, so
[the caveat above](#the-one-thing-to-check-on-real-hardware) is exactly as
untested as it sounds.

## What to check first when it does not work

Delivery failures show up in the log as `[<id>] FAILED: <detail>`, in the
`error` key of the state topic, and in a render response's `delivery` field.
The `mqtt` transport has two
([reference](../reference/transports.md#mqtt)):

| Message | What to do |
| --- | --- |
| `MQTT is not enabled. Set mqtt.enabled: true and configure the broker.` | The display asks for the `mqtt` transport but no publisher is running. Set `mqtt.enabled: true` and the broker keys. |
| `MQTT publish to maverick/display/kitchen failed: <exc>` | The connection dropped or the broker rejected the publish after startup. Check the broker is up and that credentials and `mqtt.tls` still match. |

Before either of those, `MqttPublisher.start()` can fail outright with
`Timed out connecting to the MQTT broker at <host>:<port>. Check mqtt.host,
credentials, and that the broker is running.` — that is a startup failure, not
a delivery one, and it happens fifteen seconds after `maverick serve` starts.

On the Pi side:

| What you see | What to do |
| --- | --- |
| The client connects but never draws | Subscribe by hand: `mosquitto_sub -h mqtt.local -t 'maverick/display/kitchen/#' -v`. No retained messages means nothing was ever delivered — check the display's `transport.type` is `mqtt` and that a render has happened. |
| It draws once and never again | Expected when nothing changes: `schedule.skip_unchanged` means an unchanged frame is not re-delivered. Press **Refresh** in Home Assistant, or `POST /api/displays/kitchen/render?force=true`. |
| Topics do not match | `--base-topic` must equal `mqtt.base_topic`, and the display id must match. A `topic` option on the transport moves the frame topics and the script will not find them. |
| A size complaint from the library | The panel geometry and the display geometry disagree — `set_image` wants an image the size of the panel. Fix it in `config.yaml` by setting the right `panel`, or `width`/`height`, rather than resizing on the Pi; the client's `resize` is a safety net, not a plan. |
| The image is there but looks wrong | See [the one thing to check on real hardware](#the-one-thing-to-check-on-real-hardware), and compare against `/api/displays/kitchen/preview.png`, which is the frame as Maverick intended it. |
