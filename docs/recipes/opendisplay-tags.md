# OpenDisplay BLE shelf labels

> **Not run on hardware by this project.** No tag has been written to, and no
> BLE scan has been run — the machine this was written on has no Bluetooth
> adapter. Everything here is read from `transports/opendisplay.py`, `cli.py`
> and the panel catalogue. [What was verified](#what-was-verified) says exactly
> what that leaves.

Electronic shelf labels are the cheapest e-paper you can put on a wall. Reflash
one with [OpenDisplay](https://github.com/OpenDisplay/py-opendisplay) firmware
and it becomes a battery panel that runs for months on a coin cell — because it
speaks BLE and nothing else. There is no access point to configure, no IP
address, and no way for the tag to fetch anything: everything is pushed to it.

1. [The tags in the catalogue](#the-tags-in-the-catalogue)
2. [Two modes, one decision](#two-modes-one-decision)
3. [Finding a tag: `maverick scan`](#finding-a-tag-maverick-scan)
4. [`mode: ha`](#mode-ha)
5. [`mode: ble`](#mode-ble)
6. [Refresh: full, fast, and `full_refresh_every`](#refresh-full-fast-and-full_refresh_every)
7. [Why the image arrives already quantised](#why-the-image-arrives-already-quantised)
8. [What was verified](#what-was-verified)
9. [What to check first when it does not work](#what-to-check-first-when-it-does-not-work)

## The tags in the catalogue

Every id below is in [`panels.yaml`](../reference/panels.md) and defaults to
`transport: opendisplay`, so naming the panel is most of the configuration.

| Panel id | Size | Inks | dpi | Partial refresh | Full refresh every |
| --- | --- | --- | --- | --- | --- |
| `opendisplay-solum-2in6-bwr` | 152×296 | bwr | 128 | yes | 12 frames |
| `opendisplay-solum-2in9-bwr` | 296×128 | bwr | 111 | yes | 12 frames |
| `opendisplay-solum-4in2-bwr` | 400×300 | bwr | 119 | yes | 12 frames |
| `opendisplay-solum-7in5-bwr` | 800×480 | bwr | 124 | no | every frame |
| `opendisplay-mono-4in26` | 800×480 | mono | 219 | yes | 20 frames |
| `opendisplay-spectra-7in3` | 800×480 | spectra6 | 128 | no | every frame |
| `opendisplay-flex-2in9` | 296×128 | bwr | 111 | yes | 12 frames |
| `opendisplay-xiao-7in5` | 800×480 | mono | 124 | no | every frame |

Two entries have notes worth reading before you buy:
`opendisplay-spectra-7in3` refreshes in around twenty seconds and should be
scheduled sparingly, and `opendisplay-flex-2in9` has physical buttons — which
the OpenDisplay *integration* surfaces in Home Assistant as event entities.
Maverick does not see them; it renders and delivers, and an automation in Home
Assistant is what turns a button press into a render.

`opendisplay-solum-2in6-bwr` carries `native_rotation: 270`, so Maverick renders
a landscape viewport and rotates it for you. Set `displays[].rotation` only if
you have mounted the tag differently from the way the catalogue assumes.

## Two modes, one decision

The transport reaches a tag two ways, and the choice is made by the machine
Maverick runs on, not by preference:

```text
Does the process running Maverick have a Bluetooth adapter it can use?
├── No  (a container, an add-on, a VM, a server in a cupboard)  → mode: ha
└── Yes (a standalone host, a Pi, a laptop on the same floor)   → mode: ble
    └── ...but is the tag in range of *that* adapter?
        ├── Yes → mode: ble works
        └── No  → mode: ha anyway, if Home Assistant has a proxy in the room
```

| | `mode: ha` | `mode: ble` |
| --- | --- | --- |
| Needs Bluetooth on this host | no | **yes** |
| Needs Home Assistant | **yes** | no |
| Needs the `opendisplay` extra installed | no | **yes** |
| Reaches | anything any Home Assistant Bluetooth adapter or ESPHome Bluetooth proxy can hear | only tags in range of this host's adapter |
| Good for | the normal case: Maverick in a container, tags spread around a house | standalone runs, setup, diagnostics |

`mode: ha` is the default in all but name: the option's default is `auto`,
which resolves to `ha` when a Home Assistant connection exists and `ble`
otherwise (`OpenDisplayTransport.deliver`).

The reason to prefer `ha` is coverage, not convenience. Home Assistant delivers
through whatever Bluetooth it has, **including ESPHome Bluetooth proxies** — so
a £5 ESP32 in the room with the tag extends your range further than any adapter
in the server ever will.

## Finding a tag: `maverick scan`

```console
$ maverick scan
Found 2 tag(s):
  AA:BB:CC:DD:EE:FF  OpenDisplay_A1B2
  AA:BB:CC:DD:EE:00  OpenDisplay_C3D4

Add one to your config:
  - id: my-tag
    panel: opendisplay-solum-2in6-bwr   # pick your model
    dashboard: /lovelace-eink/tag
    transport:
      type: opendisplay
      mode: ble
      mac: "AA:BB:CC:DD:EE:FF"
```

That block is printed for the first tag by name order, and it is a starting
point rather than a finished display: the `panel:` line is a guess the comment
admits to, and `dashboard:` is a placeholder.

`--timeout` sets how long the scan runs (default 10 seconds). Tags advertise
only intermittently to save power, so a tag that does not appear in one scan may
appear in the next.

Scanning always uses the local adapter — there is no scan-through-Home-Assistant
path — so in a container it fails, and says so:

```text
BLE scan failed: <error>
A local Bluetooth adapter is required for scanning. If you run Maverick as an
add-on without Bluetooth, use the OpenDisplay integration in Home Assistant and
transport mode: ha instead.
```

Two other outcomes, both exit code 1: `py-opendisplay is not installed. Install
the 'opendisplay' extra.` and `No OpenDisplay tags found. Check they are powered
and in range.`

In `ha` mode you do not need `maverick scan` at all — Home Assistant's own
OpenDisplay integration has already discovered the tag, and what you need from
it is a device id, not a MAC.

## `mode: ha`

```yaml
displays:
  - id: fridge
    panel: opendisplay-solum-2in9-bwr
    dashboard: /lovelace-eink/tag
    schedule:
      every: 30m
    transport:
      type: opendisplay
      mode: ha
      device_id: 0a1b2c3d4e5f60718293a4b5c6d7e8f9
```

`device_id` is **the device registry id** from the OpenDisplay integration — not
the entity id, not the MAC. Open the device page in Home Assistant and take it
from the URL (`/config/devices/device/<this>`). Getting this wrong is the single
most common failure in this mode, which is why the error message says so.

Maverick does not send the image over the wire to Home Assistant. It writes a
PNG somewhere both processes can see and calls `opendisplay.upload_image` with a
media-source id pointing at it:

| Option | Default | What it is |
| --- | --- | --- |
| `media_dir` | `/media/maverick` | Directory the frame is written to, as `<display id>.png`. Both processes must see the same directory at this path. |
| `media_root` | `/media` | Home Assistant's media folder. `media_dir` must be inside it — Home Assistant will not read an image from anywhere else. |
| `media_source_prefix` | `media-source://media_source/local` | Prepended to the frame's path relative to `media_root` to build the `media_content_id`. |
| `rotation` | unset | Passed to the action as the *tag's* rotation. This is not `displays[].rotation`, which Maverick has already applied to the pixels. |

So with the defaults, the frame lands at `/media/maverick/fridge.png` and the
action is called with
`media-source://media_source/local/maverick/fridge.png`. If Maverick runs in a
container, that path has to be a shared mount, not a copy.

## `mode: ble`

```yaml
displays:
  - id: fridge
    panel: opendisplay-solum-2in9-bwr
    dashboard: /lovelace-eink/tag
    transport:
      type: opendisplay
      mode: ble
      mac: "AA:BB:CC:DD:EE:FF"
      encryption_key: 000102030405060708090a0b0c0d0e0f
      timeout: 20
      max_attempts: 4
```

Install the optional dependency first — it is not pulled in by default:

```console
$ pip install "maverick-eink-dashboard[opendisplay]"
```

| Option | Default | Notes |
| --- | --- | --- |
| `mac` | unset | The tag's MAC. Either this or `device_name` is required; without both, delivery raises `opendisplay mode 'ble' needs either 'mac' or 'device_name'. Run `maverick scan` to find your tags.` |
| `device_name` | unset | The advertised name, when the MAC is not known. |
| `encryption_key` | unset | AES-128 key, **as hex** — `bytes.fromhex()` is applied, so 32 hex characters, no `0x`, no colons. Unset sends unencrypted, which tags that do not require a key accept. A malformed value raises a `ValueError` from `fromhex` before any BLE work happens. |
| `timeout` | `20` | Seconds to wait for the BLE connection. Raise it for a tag at the edge of range; a slow connection is more common than a refused one. |
| `max_attempts` | `4` | Connection attempts before giving up. BLE connections to battery tags fail often and retry cheaply, which is why the default is not 1. |
| `scan_timeout` | `10` | Seconds the transport's own `probe()` scans for. Nothing calls `probe()` today — no CLI command and no route reaches it — so this option currently has no effect. |

`timeout` and `max_attempts` multiply: with the defaults, a tag that is simply
not there costs about eighty seconds before `deliver()` returns a failure. On a
five-minute schedule that is survivable; on a one-minute schedule it is not, so
give BLE displays a schedule with room in it.

## Refresh: full, fast, and `full_refresh_every`

Every delivery picks one of two refresh modes, and Maverick decides, not the
tag:

```python
refresh = od.RefreshMode.FULL if context.full_refresh else od.RefreshMode.FAST
if not context.display.profile.supports_partial:
    refresh = od.RefreshMode.FULL
```

* **Fast** is the partial update: quicker, quieter, and it leaves a little
  ghosting behind each time.
* **Full** is the flashing update that clears the panel and redraws it. It is
  what removes accumulated ghosting.

`full_refresh` comes from the engine, which asks for one when the frame count
since the last full refresh reaches the display's cadence — `full_refresh_every`
from [`displays[].schedule`](../reference/configuration.md#displaysschedule),
falling back to the panel profile's value (the last column of
[the table above](#the-tags-in-the-catalogue)). A forced render also asks for
one.

Two consequences:

* On a panel with `supports_partial: false` — the 7.5" Solum, the Spectra, the
  XIAO — **every** update is a full refresh, whatever you set. The cadence is
  ignored because there is nothing to accumulate.
* On a partial-capable tag, lowering `full_refresh_every` trades battery and
  speed for a cleaner panel. Raising it does the opposite, and ghosting is
  cumulative: it will not look wrong for the first few frames.

```yaml
displays:
  - id: fridge
    schedule:
      every: 15m
      full_refresh_every: 8      # a clearing refresh every two hours
```

This is also reachable from Home Assistant: the **Full refresh** button in the
MQTT discovery device forces one on demand
([MQTT reference](../reference/mqtt.md#commands)).

## Why the image arrives already quantised

`py-opendisplay` can dither an image onto the tag's palette itself. Maverick
switches that off, and the reason is worth understanding because it is the same
reason the frames look as good as they do.

By the time a frame reaches a transport it has already been through Maverick's
pipeline: fitted, colour-mapped to the panel's *measured* inks, and dithered
with a mode that classifies the image first — broad midtone areas (photographs,
gradients) get error diffusion, while high-contrast bimodal regions (text,
icons, borders) get thresholded, because error diffusion on text erodes the
stems and turns small type into blotches.

Dithering that image again would undo exactly that work: the second pass sees an
already-quantised image, has no idea which pixels were protected, and diffuses
error across the text it was meant to leave alone.

So the transport maps Maverick's dither mode onto the library's — and maps
`AUTO`, the content-aware one, to `NONE`:

```python
DitherMode.AUTO: "NONE",   # we already did it, so do not redo it
```

It also passes `fit=STRETCH` and `rotate=ROTATE_0` for the same reason: the
image is already the tag's exact resolution in the tag's exact orientation, so
every transformation the library could apply is a transformation that could only
make it worse.

If you set `image.dither` to something specific, that mode *is* passed through
to the library — the mapping is one-to-one for the named kernels. Leaving it at
the default `auto` is what turns the second pass off.

## What was verified

Checked on this repository:

* Every panel id, resolution, ink scheme, dpi, partial-refresh flag and cadence
  in the table is read from `devices/panels.yaml` through `all_panels()`.
* Every option name, default and error string is from
  `transports/opendisplay.py`, and each appears in
  [the transport reference](../reference/transports.md#opendisplay).
* The `maverick scan` output is transcribed from `cmd_scan` in `cli.py`,
  including the configuration block it prints. **The command was not run** —
  there is no Bluetooth adapter here — so the tag names and MACs in it are
  invented, and only the shape of the output is real.

Not checked: any BLE connection, any Home Assistant service call, any tag, any
refresh timing, and the behaviour of `py-opendisplay`, which is not installed
here.

## What to check first when it does not work

Most delivery failures are returned rather than raised, and appear in the log
as `[<id>] FAILED: <detail>`, in the MQTT state topic's `error` key, and in the
`delivery` field of a `POST /api/displays/<id>/render` response. Two of the rows
below are raised instead — a missing `device_id` and a bad `mode` — and surface
as a traceback under `[<id>] scheduled render failed`, because the engine's
`try` covers the render step and not the delivery. Either way the text is the
same, and these are every failure this transport can produce
([reference](../reference/transports.md#opendisplay)):

| Message | What to do |
| --- | --- |
| `opendisplay mode 'ha' needs a Home Assistant connection; set home_assistant.url and home_assistant.token, or use mode: ble.` | The Home Assistant client is not connected. Check `home_assistant.url`/`token` with `maverick check`, or switch to `mode: ble`. |
| `transport 'opendisplay' requires the 'device_id' option to be set` *(raised)* | `mode: ha` without `device_id`. Take it from the device page URL in Home Assistant. |
| `opendisplay.upload_image failed: … it is the device registry id, not the entity id or the MAC.` | The action itself failed. Confirm the OpenDisplay integration is installed and the tag is online, then re-check `device_id` — the message names the usual mistake because it is the usual mistake. |
| `cannot write to /media/maverick (…). The add-on needs the 'media:rw' mapping, or set transport.media_dir to a shared path.` | The process cannot create or write `media_dir`. Grant the mapping, or point `media_dir` somewhere writable *and* visible to Home Assistant. |
| `media_dir … is not inside media_root …; Home Assistant can only read images from its media folder.` | Put `media_dir` under `media_root`, or set `media_root` to wherever `media_dir` really is. |
| `py-opendisplay is not installed. Install it with pip install 'maverick-eink-dashboard[opendisplay]', or use mode: ha …` | The `ble` path without the extra. Install it, or use `ha`. |
| `opendisplay mode 'ble' needs either 'mac' or 'device_name'. Run maverick scan to find your tags.` *(raised)* | Add one of them to the transport block. |
| `BLE upload to AA:BB:CC:DD:EE:FF failed: <exc>` | The connection or the upload failed. In order: is the tag powered; is it in range of *this* host; does it want an `encryption_key`; is `timeout` long enough; is something else (a phone, `bluetoothctl`) holding a connection to it. `maverick scan` tells you whether the adapter can see it at all. |
| `opendisplay mode must be auto, ha or ble (got '…')` *(raised)* | A typo in `mode`. |

Two failures that are not this transport's, and look like they are:

* **The tag updates but the image is wrong** — wrong geometry, or text you
  cannot read. That is the panel profile, not the transport. Check the id
  against [the catalogue](../reference/panels.md) and look at
  `/api/displays/<id>/preview.png`, which is the exact image that was sent.
* **Nothing is delivered at all and there is no error.** The render was skipped,
  not failed: an unchanged frame (`schedule.skip_unchanged`), quiet hours, or
  the Home Assistant switch that pauses the schedule. The log line says
  `skipped:` and the reason.
