# MQTT reference

Set `mqtt.enabled: true` and Maverick connects to the broker at
`mqtt.host:mqtt.port` and does two separate things over it:

* **Home Assistant discovery** — every enabled display arrives as a real device
  with buttons, a switch, an image and diagnostic sensors
  (`src/maverick/ha/discovery.py`). This is what turns "a tool that talks to
  Home Assistant" into "a tool that belongs in Home Assistant": an automation
  presses a button instead of defining a `rest_command`.
* **The `mqtt` frame transport** — for a display whose `transport.type` is
  `mqtt`, the frame bytes and their metadata are published for your own client
  to subscribe to (`src/maverick/transports/mqtt.py`).

The first happens for every display as soon as MQTT is enabled. The second
happens only for displays that actually use the `mqtt` transport. They share a
topic tree but are independent: you can use discovery with a display that
delivers over [`http_pull`](transports.md#http_pull), and you can use the
transport without Home Assistant.

Everything below is verbatim from the source. Two defaults matter for reading
it: `mqtt.base_topic` is `maverick` and `mqtt.discovery_prefix` is
`homeassistant`.

## Topic layout

`{id}` is the display's configured `id`.

| Topic | Payload | Retained | QoS | Published by |
| --- | --- | --- | --- | --- |
| `{base_topic}/status` | `online` or `offline` | yes | 1 | Startup, shutdown, and the broker's last will |
| `{base_topic}/display/{id}/command` | A command word | no | 0 (subscribe) | **You.** Maverick subscribes; it never publishes here |
| `{base_topic}/display/{id}/state` | JSON object | yes | 1 | After every render, and after a schedule command |
| `{base_topic}/display/{id}/image_url` | A URL, as plain text | yes | 1 | After a render that produced a new frame, when `server.base_url` is set and `server.api_token` is not |
| `{base_topic}/display/{id}/preview` | PNG bytes | yes | 1 | The same, in every other case |
| `{base_topic}/display/{id}/meta` | JSON object | yes | 1 | The `mqtt` transport, on each delivery |
| `{base_topic}/display/{id}/frame` | Raw frame bytes | yes | 1 | The `mqtt` transport, on each delivery |
| `{discovery_prefix}/{component}/maverick_{id}/{suffix}/config` | JSON object, or empty to retract | yes | 1 | Discovery at startup |

Every publish Maverick makes is retained at QoS 1 — `MqttPublisher.publish`
defaults to `retain=True, qos=1` and nothing overrides it — and waits for the
broker's acknowledgement before returning. Retained means a client that
connects late, or reboots, gets the current frame and state immediately instead
of a blank panel and unknown sensors until the next render.

A display using the `mqtt` transport can move its two frame topics elsewhere
with the transport's `topic` option; the default is exactly
`{base_topic}/display/{id}`, so by default `/meta` and `/frame` sit alongside
`/state` and `/preview`. The discovery and state topics are not affected by
that option.

## Availability and the last will

`{base_topic}/status` carries the service's availability, and every discovery
payload points its `availability` at that one topic.

* On startup, `announce()` publishes `online` (retained) before announcing any
  display.
* On a clean shutdown, `offline` (retained).
* On an unclean one, the broker publishes `offline` for us: the will is
  registered as part of the CONNECT packet, with `qos=1, retain=True`, so Home
  Assistant marks every entity unavailable rather than showing a stale "last
  render" time forever.

The will has to be supplied when the publisher is constructed, before it
connects — paho attaches it to CONNECT, and setting it afterwards is silently
ignored. `Engine.start()` therefore builds it from the config and passes it in.

## Discovery

### Config topics

One retained JSON message per entity, at:

```text
{discovery_prefix}/{component}/maverick_{display id}/{suffix}/config
```

so the refresh button of a display with `id: kitchen` is announced at
`homeassistant/button/maverick_kitchen/refresh/config`. Keys whose value is
`None` are stripped before publishing, so an absent `configuration_url` simply
does not appear.

### The device block

Every entity carries the same `device`, which is what makes the entities group
into one device in Home Assistant:

| Key | Value |
| --- | --- |
| `identifiers` | `["maverick_{display id}"]` |
| `name` | The display's `name` |
| `manufacturer` | `Maverick` |
| `model` | The resolved panel profile's name, e.g. `Waveshare 7.5" monochrome (V2)` |
| `sw_version` | Maverick's version |
| `configuration_url` | `server.base_url`, omitted when it is unset |

Every entity also carries `availability: [{"topic": "{base_topic}/status"}]` and
`qos: 1`.

### Entities

Ten entities per display. Home Assistant builds each entity id from the device
name and the entity name, so a display named `Kitchen panel` gives
`button.kitchen_panel_refresh`, `switch.kitchen_panel_scheduled_renders`,
`image.kitchen_panel_screen`, and so on.

| Component | Config suffix | `unique_id` | `name` | Category | What it reads or sends |
| --- | --- | --- | --- | --- | --- |
| `button` | `refresh` | `maverick_{id}_refresh` | Refresh | `config` | Sends `refresh` to the command topic. Icon `mdi:refresh` |
| `button` | `full_refresh` | `maverick_{id}_full_refresh` | Full refresh | `config` | Sends `full_refresh` to the command topic. Icon `mdi:television-clean` |
| `switch` | `scheduled` | `maverick_{id}_scheduled` | Scheduled renders | `config` | Sends `schedule_on` / `schedule_off`; reads `value_json.schedule_enabled` from the state topic, with `state_on: true` and `state_off: false`. Icon `mdi:timer-outline` |
| `image` | `screen` | `maverick_{id}_image` | Screen | — | The current frame. [Two modes](#the-image-entity-two-modes). Icon `mdi:image` |
| `sensor` | `last_render` | `maverick_{id}_last_render` | Last render | `diagnostic` | `value_json.last_render_at`, `device_class: timestamp`. Icon `mdi:clock-outline` |
| `sensor` | `status` | `maverick_{id}_status` | Status | `diagnostic` | `value_json.status`. Icon `mdi:information-outline` |
| `sensor` | `render_duration` | `maverick_{id}_render_duration` | Render duration | `diagnostic` | `value_json.render_duration`, `device_class: duration`, unit `s`, `state_class: measurement`. Icon `mdi:timer-sand` |
| `sensor` | `ink_coverage` | `maverick_{id}_ink_coverage` | Ink coverage | `diagnostic` | `value_json.ink_coverage`, unit `%`, `state_class: measurement`. Icon `mdi:water-percent` |
| `sensor` | `frames` | `maverick_{id}_frames` | Frames delivered | `diagnostic` | `value_json.sequence`. Icon `mdi:counter` |
| `binary_sensor` | `problem` | `maverick_{id}_problem` | Problem | `diagnostic` | `value_json.problem`, `device_class: problem`, `payload_on: true`, `payload_off: false` |

Every entity except the image reads from the one state topic, each with its own
`value_template`. That is why a single retained state message keeps the whole
device consistent.

The `config` category puts the buttons and the switch under the device's
configuration controls; `diagnostic` puts the sensors under diagnostics. The
image entity has no category, so it is a primary entity and appears on the
device's main card — which is the point of it.

## Commands

Publish one of these words to `{base_topic}/display/{id}/command`. Maverick
subscribes to `{base_topic}/display/+/command` and takes the display id from the
topic, so the id must match a configured display.

| Payload | Effect |
| --- | --- |
| `refresh` | Render now, `trigger: "button"`. Skips delivery if the frame is unchanged. |
| `PRESS` | Identical to `refresh`. Maverick's own refresh button sets `payload_press: refresh`, so this spelling is for a button or script that sends the MQTT default instead. |
| `press` | Identical to `refresh`. |
| `full_refresh` | Render now with `force=true`: bypasses the unchanged-checksum shortcut and the lint gate, and asks the panel for a flashing full refresh that clears ghosting. |
| `schedule_on` | Resume scheduled and state-triggered renders for this display, then republish the state. |
| `schedule_off` | Pause them. Renders asked for by hand, by the API or by a button still run. |

The payload is decoded as UTF-8 and stripped of surrounding whitespace. Anything
else is logged as `unknown command` and ignored; a command for an unknown
display id is logged and ignored too. Nothing is published back to say a command
was rejected — the state topic is the feedback channel, and only a command that
actually did something updates it.

`schedule_off` lasts until `schedule_on` or a restart: it lives in the
scheduler's memory, not in the config file, which is the point — pausing the
bedroom panel overnight should not mean editing YAML.

## The state topic

One retained JSON object per display on `{base_topic}/display/{id}/state`,
republished after every render outcome and after a `schedule_on` / `schedule_off`
command. Every key that `_publish_state` writes:

```json
{
  "status": "ok",
  "problem": false,
  "schedule_enabled": true,
  "last_render_at": "2026-09-14T09:04:11+00:00",
  "last_delivery_at": "2026-09-14T09:04:13+00:00",
  "sequence": 412,
  "render_count": 530,
  "skip_count": 118,
  "error": null,
  "render_duration": 1.61,
  "ink_coverage": 12.4,
  "lint": "ok",
  "trigger": "schedule",
  "updated_at": "2026-09-14T09:04:13+00:00"
}
```

| Key | Type | Meaning |
| --- | --- | --- |
| `status` | string | `error` if the render failed or the display has consecutive failures; `unchanged` if this render was skipped; otherwise `ok`. |
| `problem` | boolean | True while `consecutive_failures` is above zero — i.e. the last render failed and none has succeeded since. Drives the problem binary sensor. |
| `schedule_enabled` | boolean | Whether the scheduler will run this display. Drives the scheduled-renders switch. |
| `last_render_at` | string or null | ISO 8601 UTC, to the second, of the last successful render. Null until there has been one. |
| `last_delivery_at` | string or null | The same for the last successful delivery. A skipped render advances neither. |
| `sequence` | integer | How many frames have been delivered for this display, across restarts. Drives the "Frames delivered" sensor. |
| `render_count` | integer | How many renders have completed, including ones that were then skipped. |
| `skip_count` | integer | How many renders were skipped — unchanged frames plus lint-blocked ones. A high ratio to `render_count` is healthy: it is the battery saving working. |
| `error` | string or null | The last error message, or null once a render succeeds. |
| `render_duration` | number or null | Seconds for the whole render-process-deliver cycle, to two decimal places. Null when the state was published by a schedule command rather than a render. |
| `ink_coverage` | number or null | Percentage of the panel covered in ink, to one decimal place, from the frame's `coverage.ink` metric. Null on a state published without a frame. |
| `lint` | string or null | The linter's one-line summary for this frame. |
| `trigger` | string or null | What caused this render: `schedule`, `startup`, `state` (a watched entity changed), `button` (MQTT), `api`, `cli`, or `manual`. |
| `updated_at` | string | ISO 8601 UTC, to the second, when this message was published. Always present. |

Two things follow from how it is published. No state is published at startup, so
the sensors stay unknown until the first render — with `schedule.render_on_start`
left at its default that is a few seconds. And the keys that come from a render
outcome (`render_duration`, `ink_coverage`, `lint`, `trigger`) are null in a
message published by `schedule_on` or `schedule_off`, because there was no
render to describe.

## The image entity: two modes

The image entity is announced one of two ways, decided by `_publishes_a_url`
(`src/maverick/ha/discovery.py`) and applied to both the discovery payload and
the publish that follows each render:

| Condition | Discovery keys | What flows through the broker |
| --- | --- | --- |
| `server.base_url` set, `server.api_token` empty | `url_topic: {base_topic}/display/{id}/image_url` | A URL: `{base_url}/api/displays/{id}/preview.png` |
| Anything else | `image_topic: {base_topic}/display/{id}/preview`, `content_type: image/png` | The PNG bytes themselves |

The URL mode is preferred, and set `server.base_url` if you can: pushing a full
frame through the broker on every render is wasteful when an HTTP URL will do.
The bytes mode exists so the entity still works when there is no URL a Home
Assistant instance could usefully be given — and it means a retained PNG of
every panel sits on your broker, which is worth knowing if the broker is shared
or its storage is small.

**Setting `server.api_token` switches the entity to the bytes mode**, whatever
`base_url` says. The token covers `/api/displays/{id}/preview.png`
(`src/maverick/server/api.py`), and an image entity fetches a `url_topic` URL
with no credentials at all — there is nowhere in it to put a token — so the
URL it would be given answers 401. Sending the frame over the broker is how the
entity keeps working; the cost is the retained PNG per panel described above.

Either way, the publish happens only when a render produced a frame **and** was
not skipped. An unchanged render leaves the retained image exactly as it was,
which is correct: the panel has not changed either.

The preview is the quantised frame — the real inks, what the panel is showing —
not the source screenshot.

## Frames for your own client

For a display with `transport.type: mqtt`, each delivery publishes two messages.
This is the pair to subscribe to if you are driving a panel yourself: a
Raspberry Pi with an Inky, an ESP32 holding a subscription, anything
mains-powered that can stay connected. A battery device that sleeps cannot hold
one; that is what [`http_pull`](http-api.md#the-pull-protocol) is for.

Both are retained at QoS 1, `/meta` first and then `/frame`, so a subscriber
that reconnects gets the current frame immediately.

**`{topic}/meta`** — a JSON object, every key from `MqttTransport.deliver`:

```json
{
  "id": "kitchen",
  "name": "Kitchen panel",
  "width": 800,
  "height": 480,
  "format": "packed",
  "color_scheme": "mono",
  "checksum": "0123456789abcdef",
  "bytes": 48000,
  "full_refresh": false,
  "sequence": 412,
  "trigger": "schedule"
}
```

| Key | Meaning |
| --- | --- |
| `id` | The display id. |
| `name` | The display's human-readable name. |
| `width`, `height` | Frame size in pixels, after any rotation. |
| `format` | The frame layout: `packed`, `planes`, `indexed`, `png` or `bmp`. See [the configuration reference](configuration.md#frame-formats) for what each means on the wire. |
| `color_scheme` | The palette the frame was quantised to: `mono`, `bwr`, `gray16`, `spectra6` and so on. |
| `checksum` | Sixteen hex characters identifying the frame's content. Compare it with what you last drew: equal means there is nothing to do. |
| `bytes` | Length of the `/frame` payload, so a client can tell a truncated message from a short one. |
| `full_refresh` | True when this frame should be drawn with a flashing full refresh to clear ghosting, rather than a partial one. Honour it if your panel supports both. |
| `sequence` | The display's delivery counter at the time of this frame. |
| `trigger` | What caused this render, same values as the state topic's `trigger`. |

**`{topic}/frame`** — the frame bytes exactly as the panel wants them, in the
`format` the metadata names. Nothing is wrapped around them: no base64, no
envelope.

The ordering is deliberate but not atomic: read `/meta` for identity and size,
then draw `/frame`. Because both are retained, a subscriber that joins mid-render
can briefly see the previous `/meta` with the new `/frame`; the `checksum` and
`bytes` keys are how you notice.

## Retracting a removed display

Retained discovery messages outlive the display that caused them. Delete a
display from the config and its Home Assistant device stays, permanently
unavailable, because the broker still holds the retained config payloads.

`MqttDiscovery.remove_display(display_id)` clears them: it publishes an **empty
retained payload** to each of the display's ten config topics, which is how
Home Assistant is told to delete an entity. The topics are exactly:

```text
{discovery_prefix}/button/maverick_{id}/refresh/config
{discovery_prefix}/button/maverick_{id}/full_refresh/config
{discovery_prefix}/switch/maverick_{id}/scheduled/config
{discovery_prefix}/image/maverick_{id}/screen/config
{discovery_prefix}/sensor/maverick_{id}/last_render/config
{discovery_prefix}/sensor/maverick_{id}/status/config
{discovery_prefix}/sensor/maverick_{id}/render_duration/config
{discovery_prefix}/sensor/maverick_{id}/ink_coverage/config
{discovery_prefix}/sensor/maverick_{id}/frames/config
{discovery_prefix}/binary_sensor/maverick_{id}/problem/config
```

Nothing calls `remove_display` automatically today — Maverick announces the
displays it has and never notices the ones that have gone. Until something does,
clear a removed display by publishing an empty retained message to each topic
above yourself:

```console
$ for t in button/refresh button/full_refresh switch/scheduled image/screen \
           sensor/last_render sensor/status sensor/render_duration \
           sensor/ink_coverage sensor/frames binary_sensor/problem; do
    mosquitto_pub -h core-mosquitto -r -n \
      -t "homeassistant/${t%%/*}/maverick_kitchen/${t##*/}/config"
  done
```

The display's own `state`, `preview`, `image_url`, `meta` and `frame` topics stay
retained too; clear them the same way (`-r -n`) if you want the tree tidy.

## Example automations

Both use the entity ids Home Assistant derives from a device named
`Kitchen panel`; check yours under **Settings → Devices & services → MQTT**.

### Refresh the panel when a sensor changes

The button is the intended way to trigger a render from an automation — no
`rest_command`, no token to configure.

```yaml
automation:
  - alias: Refresh the kitchen panel when the washing machine finishes
    triggers:
      - trigger: state
        entity_id: sensor.washing_machine_status
    conditions:
      - condition: state
        entity_id: binary_sensor.kitchen_panel_problem
        state: "off"
    actions:
      - action: button.press
        target:
          entity_id: button.kitchen_panel_refresh
```

A render triggered this way ignores `schedule.quiet_hours` and the
scheduled-renders switch: those gate the scheduler, not explicit requests. If
the frame turns out identical, delivery is skipped and the panel does not
refresh, so pressing the button more often than the dashboard changes costs
nothing but a render.

### Stop scheduled renders at night

```yaml
automation:
  - alias: Pause the bedroom panel overnight
    triggers:
      - trigger: time
        at: "23:00:00"
    actions:
      - action: switch.turn_off
        target:
          entity_id: switch.bedroom_panel_scheduled_renders

  - alias: Resume the bedroom panel in the morning
    triggers:
      - trigger: time
        at: "06:30:00"
    actions:
      - action: switch.turn_on
        target:
          entity_id: switch.bedroom_panel_scheduled_renders
```

The switch sends `schedule_off` and `schedule_on`, and its state comes back from
`schedule_enabled` on the state topic, so the automation and the config stay in
step. `schedule.quiet_hours` does the same job declaratively and survives a
restart; the switch is for changing your mind without editing the file.
