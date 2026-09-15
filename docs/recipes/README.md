None of these recipes has been run on hardware by the project yet. Each page says what was verified and how.

# Device recipes

*Last reviewed against commit `d103e74`.*

One page per way of getting a frame onto a panel. Each is written from the
source — the transport, the endpoint or the generator that actually does the
work — and each ends with a "What to check first when it does not work"
section drawn from that transport's own failure messages.

Take the untested banner at the top of every page seriously, and read each
page's **What was verified** section before you trust it. Some of what is here
was exercised against a running Maverick on a developer machine (HTTP
transcripts, generated files, the client scripts); none of it was exercised
against a panel, a tag, an e-reader or a TRMNL device. Where a page makes a
claim about hardware, it says where the claim came from.

| Recipe | Device | Transport |
| --- | --- | --- |
| [ESPHome and a Waveshare panel](esphome-waveshare.md) | An ESP32 wired to a Waveshare e-paper module | [`http_pull`](../reference/transports.md#http_pull) |
| [OpenDisplay BLE tags](opendisplay-tags.md) | Electronic shelf labels reflashed with OpenDisplay firmware | [`opendisplay`](../reference/transports.md#opendisplay) |
| [Kindle and Kobo](kindle-kobo.md) | A jailbroken e-reader polling for PNGs | [`http_pull`](../reference/transports.md#http_pull), or [`file`](../reference/transports.md#file) |
| [TRMNL](trmnl.md) | A TRMNL 7.5" panel in bring-your-own-server mode | [`http_pull`](../reference/transports.md#http_pull) |
| [Raspberry Pi and a Pimoroni Inky](inky-mqtt.md) | A Pi holding an MQTT subscription | [`mqtt`](../reference/transports.md#mqtt) |
| [Webhook and file](webhook-and-file.md) | Anything else: a directory, or an HTTP endpoint of your own | [`file`](../reference/transports.md#file), [`webhook`](../reference/transports.md#webhook) |

## Which one is mine?

The first question is not which panel you own, it is **whether the device can be
reached or only reach out**:

* A device that sleeps most of the time cannot be pushed to. It wakes, asks for
  a frame, and sleeps again — that is `http_pull`, and the
  [pull protocol](../reference/http-api.md#the-pull-protocol) is built around
  making an unchanged frame cost nothing.
* A device that is always on can hold a subscription, so the frame can be pushed
  the moment it exists — that is `mqtt`.
* A BLE tag can do neither: it has no IP address at all, so something has to
  connect to it over Bluetooth — that is `opendisplay`.

Then, within that:

| If you have | Start here |
| --- | --- |
| A Waveshare panel and an ESP32 | [esphome-waveshare.md](esphome-waveshare.md) |
| A shelf label, an OpenDisplay Flex or a XIAO ePaper kit | [opendisplay-tags.md](opendisplay-tags.md) |
| An old Kindle or Kobo | [kindle-kobo.md](kindle-kobo.md) |
| A TRMNL | [trmnl.md](trmnl.md) |
| A Raspberry Pi with an Inky HAT | [inky-mqtt.md](inky-mqtt.md) |
| Something with an HTTP endpoint, or a directory something else watches | [webhook-and-file.md](webhook-and-file.md) |
| A panel that is not in [the catalogue](../reference/panels.md) | The closest recipe above, plus `generic-mono` with `width`/`height` overrides |

## The client scripts

Both are standalone and meant to be copied to the device:

| Script | For |
| --- | --- |
| [`clients/frame_poll.sh`](clients/frame_poll.sh) | A POSIX-shell poller for an e-reader: `curl`, `awk`, an ETag kept on disk, `fbink` to draw. |
| [`clients/inky_client.py`](clients/inky_client.py) | An MQTT client for a Pimoroni Inky: `paho-mqtt`, Pillow and the `inky` library. |

Both pass a syntax check and both were exercised as far as they can be without
the hardware; the pages they belong to say exactly how far that was.

## Reporting a run

The banners come off these pages when someone reports a real one. If you get a
recipe working — or find where it is wrong — an issue with the panel id, the
device, the firmware version and what you had to change is worth more than
anything that can be written from the source.

## Related reading

* [Configuration reference](../reference/configuration.md) — every key, its type
  and default.
* [Transport reference](../reference/transports.md) — every transport option and
  every failure message.
* [Panel catalogue](../reference/panels.md) — every panel id and what it sets.
* [HTTP API reference](../reference/http-api.md) — the pull protocol in full.
* [MQTT reference](../reference/mqtt.md) — the topic tree, the payloads and the
  discovery entities.
* [Home Assistant guide](../guides/home-assistant.md) — connecting the whole
  thing to Home Assistant.
