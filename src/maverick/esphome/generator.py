"""Generate a ready-to-flash ESPHome configuration for a display.

**Which firmware for which panel?** Two answers, and the right one depends on
the radio, not on preference:

* **BLE e-paper tags** (shelf labels, OpenDisplay Flex, XIAO ePaper kits) run
  **OpenDisplay firmware**. They are battery devices measured in months, and
  BLE is what makes that possible. Maverick pushes to them over BLE, directly
  or through Home Assistant's Bluetooth proxies. No ESPHome involved.
* **Wired or mains-powered ESP32 panels** (Waveshare, LilyGO, DIY builds) run
  **ESPHome**. They have Wi-Fi, so pulling a rendered PNG over HTTP is simpler
  and more robust than any custom protocol, and ESPHome gives OTA updates and
  native Home Assistant integration for free.

The generated config uses ESPHome's ``online_image`` component to fetch the
frame Maverick rendered, so the device does no layout work at all — which is
the entire point. The firmware never changes when the dashboard does.

**Nothing secret is written into the file.** Wi-Fi, the ESPHome API key and
Maverick's own API token are all ``!secret`` references, resolved from the
``secrets.yaml`` beside the configuration when ESPHome compiles it. The token
used to be written in literally, which made the generated document itself a
secret and every copy of it a leak; :func:`describe_esphome` hands the setup
UI the names of the secrets the file expects, and the values Maverick knows
(the token, if one is set) so they can be copied across in one step.

**The keys are ESPHome's, checked against ESPHome.** ``scripts/check_esphome.py``
runs ``esphome config`` over a configuration for every panel in the catalogue
that names a model, and CI runs it (``.github/workflows/ci.yml``, the
``esphome`` job). That is validation, not a flash: the untested banner on the
recipe stays until someone runs the result on a panel.
"""

from __future__ import annotations

import base64
from secrets import token_bytes
from typing import Any

from ..config import Config, ResolvedDisplay
from ..eink.palette import ColorScheme

#: Maverick colour scheme -> the online_image decode type ESPHome should use.
_IMAGE_TYPE = {
    ColorScheme.MONO: "BINARY",
    ColorScheme.GRAY4: "GRAYSCALE",
    ColorScheme.GRAY8: "GRAYSCALE",
    ColorScheme.GRAY16: "GRAYSCALE",
    ColorScheme.BWR: "RGB565",
    ColorScheme.BWY: "RGB565",
    ColorScheme.BWRY: "RGB565",
    ColorScheme.SPECTRA6: "RGB565",
    ColorScheme.ACEP7: "RGB565",
}

#: Bytes per pixel for each decode type, used to estimate the decoded frame.
_BYTES_PER_PIXEL = {"BINARY": 0.125, "GRAYSCALE": 1.0, "RGB565": 2.0}

#: Above this many decoded bytes a plain ESP32 (~200 KB of usable heap) cannot
#: hold the frame, and the generated file says so and asks for PSRAM.
PSRAM_THRESHOLD = 180_000

#: `online_image.buffer_size` is the *download* buffer, and ESPHome caps it at
#: 64 KiB (`cv.int_range(256, 65536)` in `esphome/components/online_image`).
#: The decoded image lives elsewhere, in heap or PSRAM.
DOWNLOAD_BUFFER_MAX = 65_536

#: The `!secret` names the generated file references. `secrets.yaml` next to
#: the ESPHome configuration must define every one of them, or the compile
#: fails naming the missing key.
SECRET_WIFI_SSID = "wifi_ssid"
SECRET_WIFI_PASSWORD = "wifi_password"
SECRET_API_KEY = "api_key"
SECRET_AUTHORIZATION = "maverick_authorization"

#: Panel vendors whose catalogue entries are never an ESP32 running ESPHome:
#: e-readers, TRMNL, and a Pi driving an Inky. The pull transport suits them,
#: the firmware config does not, so the setup UI does not offer it for them.
_NOT_ESPHOME_VENDORS = frozenset({"amazon", "kobo", "trmnl", "pimoroni"})

#: Transports over which an ESPHome node collects frames. `opendisplay` and
#: `webhook` reach a device that is not running this firmware.
_ESPHOME_TRANSPORTS = frozenset({"http_pull", "mqtt", "file"})


def _decoded_size(display: ResolvedDisplay, image_type: str) -> int:
    """Roughly what the decoded frame costs in RAM, with headroom."""
    pixels = display.width * display.height
    # 20% headroom: the decoder needs scratch space, and a too-small
    # allocation fails at download time with a message that is not obviously
    # about size.
    return int(pixels * _BYTES_PER_PIXEL[image_type] * 1.2) + 1024


def esphome_applicable(display: ResolvedDisplay) -> bool:
    """Whether an ESPHome configuration makes sense for this display at all.

    True for a display that collects frames over a transport an ESPHome node
    can use, on a panel that is plausibly wired to an ESP32. A Kindle over
    `http_pull` is a pull display too, but no firmware config helps it.
    """
    return (
        display.transport_type in _ESPHOME_TRANSPORTS
        and display.profile.vendor not in _NOT_ESPHOME_VENDORS
    )


def generate_esphome_config(display: ResolvedDisplay, config: Config) -> str:
    """Return a complete ESPHome YAML document for ``display``."""
    return describe_esphome(display, config)["yaml"]


def _fresh_api_key() -> str:
    """A new ESPHome native API encryption key: 32 random bytes, base64.

    The format ESPHome's `api.encryption.key` takes
    (https://esphome.io/components/api.html), and the same thing the Device
    Builder's wizard generates for a new device.
    """
    return base64.b64encode(token_bytes(32)).decode("ascii")


def describe_esphome(display: ResolvedDisplay, config: Config) -> dict[str, Any]:
    """The generated document plus everything a guided install step needs.

    Returned alongside the YAML rather than parsed back out of it: the node
    name (which is the filename ESPHome expects), the secrets the file
    references with the values Maverick already knows, whether the panel has a
    driver ESPHome knows by name, and whether the frame needs PSRAM. The setup
    UI's *Install on device* panel is drawn from this
    (`src/maverick/server/static/app.js`), and `GET
    /api/displays/{id}/esphome` serves it (`src/maverick/server/api.py`).
    """
    options = display.config.esphome
    profile = display.profile
    node = options.node_name or f"{display.id}-panel".replace("_", "-")
    image_type = _IMAGE_TYPE[display.color_scheme]
    decoded = _decoded_size(display, image_type)
    needs_psram = decoded > PSRAM_THRESHOLD
    # `buffer_size` on `online_image` is the download buffer, capped by ESPHome
    # at 64 KiB; an explicit `esphome.buffer_size` is honoured up to that cap.
    download_buffer = min(options.buffer_size or decoded, DOWNLOAD_BUFFER_MAX)

    base = (config.server.base_url or "http://maverick.local:5000").rstrip("/")
    url = f"{base}/api/displays/{display.id}/frame"

    interval = display.config.schedule.interval_seconds or 900
    interval_text = f"{int(interval)}s"

    platform = profile.esphome_platform or "waveshare_epaper"
    model_known = bool(profile.esphome_model)
    if not model_known:
        model_line = (
            "    # ESPHome has no driver for this panel in Maverick's catalogue, so the\n"
            "    # model below is a placeholder. Set `model:` (and `platform:`, if the\n"
            "    # panel is not a waveshare_epaper one) from\n"
            "    # https://esphome.io/components/display/waveshare_epaper.html\n"
            "    model: 7.50inV2"
        )
    else:
        model_line = f"    model: {profile.esphome_model}"

    # A plain ESP32 has ~200 KB of usable heap. A full-colour or 16-grey frame
    # can exceed that by an order of magnitude, and the failure mode at flash
    # time is an allocation error that says nothing about panel choice.
    ram_warning = ""
    psram_block = ""
    if needs_psram:
        ram_warning = f"""
# ---------------------------------------------------------------------------
# WARNING: this frame needs {decoded // 1024} KB of RAM to decode, which a plain
# ESP32 does not have (~200 KB usable heap).
#
# Options, best first:
#   1. Use a board with PSRAM (esp32-s3-devkitc-1, or an ESP32-WROVER) and keep
#      the `psram:` block below.
#   2. Drive this panel from a Raspberry Pi over the `mqtt` transport instead,
#      where memory is not a constraint.
#   3. Reduce the panel's colour depth in Maverick if the dashboard does not
#      need it: a mono frame for this panel is {display.width * display.height // 8 // 1024} KB.
# ---------------------------------------------------------------------------
"""
        # Octal PSRAM is an ESP32-S3 thing; on a classic ESP32 or a WROVER the
        # PSRAM is quad, and ESPHome rejects `octal` there outright.
        psram_block = (
            "\npsram:\n  mode: octal\n  speed: 80MHz\n"
            if "s3" in options.board.lower()
            else "\npsram:\n"
        )

    secrets: list[dict[str, Any]] = [
        {"name": SECRET_WIFI_SSID, "value": None, "description": "Wi-Fi network the panel joins."},
        {"name": SECRET_WIFI_PASSWORD, "value": None, "description": "Its password."},
        {
            # Any 32 random bytes are a valid key, so one is made up here
            # rather than sent the user to `openssl`: fresh on every call,
            # which is fine because the key lives only in `secrets.yaml`, the
            # setup UI says which names are already there, and Home Assistant
            # reads it from the Device Builder when it adopts the device.
            "name": SECRET_API_KEY,
            "value": _fresh_api_key(),
            "description": (
                "ESPHome's native API encryption key. Maverick made this one up; "
                "any 32 random bytes in base64 work, so keep the one already in "
                "secrets.yaml if there is one."
            ),
        },
    ]
    header_block = ""
    if config.server.api_token:
        # A `!secret` rather than the literal: the generated document is
        # copied, downloaded and pasted, and a token in it travels with it.
        header_block = (
            "\n    request_headers:\n"
            f"      Authorization: !secret {SECRET_AUTHORIZATION}"
        )
        secrets.append(
            {
                "name": SECRET_AUTHORIZATION,
                "value": f"Bearer {config.server.api_token}",
                "description": (
                    "Maverick's API token, as the panel presents it: `server.api_token` "
                    "with `Bearer ` in front."
                ),
            }
        )

    if options.deep_sleep:
        power = f"""
# Battery operation. The node is unreachable while asleep, so OTA updates need
# it awake: press the panel's reset button and flash within the wake window.
deep_sleep:
  id: sleeper
  run_duration: 30s
  sleep_duration: {interval_text}
"""
        # Fetch once per wake, then straight back to sleep. This must live
        # inside the single top-level `esphome:` block — a second one would be
        # a duplicate YAML key and would silently discard the node name.
        on_boot = """
  on_boot:
    priority: -100
    then:
      - component.update: dashboard_image"""
        schedule_block = ""
    else:
        power = ""
        on_boot = ""
        schedule_block = f"""
interval:
  - interval: {interval_text}
    then:
      - component.update: dashboard_image
"""

    secret_lines = "\n".join(
        f"#        {entry['name']}" for entry in secrets
    )
    yaml = f"""{ram_warning}# ESPHome configuration for "{display.name}"
# Generated by Maverick for {profile.name}
# {display.width}x{display.height}, {display.color_scheme.value}, ~{display.dpi} dpi
#
# The device does no layout work: it downloads the frame Maverick already
# rendered and draws it. Change your dashboard, not this file.
#
# Before flashing:
#   1. Put these in the secrets.yaml next to this file:
{secret_lines}
#   2. Check the pins below against your wiring.
#   3. Confirm `model:` matches your panel exactly — a wrong model usually
#      shows as a garbled or half-drawn screen rather than an error.

esphome:
  name: {node}
  friendly_name: {display.name}{on_boot}

esp32:
  board: {options.board}
  framework:
    type: arduino
{psram_block}
wifi:
  ssid: !secret {SECRET_WIFI_SSID}
  password: !secret {SECRET_WIFI_PASSWORD}
  # A captive portal fallback saves a trip to the panel when Wi-Fi changes.
  ap:
    ssid: "{node} setup"

captive_portal:

logger:
  level: INFO

api:
  encryption:
    key: !secret {SECRET_API_KEY}

ota:
  - platform: esphome
{power}
http_request:
  verify_ssl: {str(options.verify_ssl).lower()}
  timeout: 30s
  # A rendered frame arrives in many TCP segments; a larger receive buffer
  # than the 512-byte default keeps the download from stalling.
  buffer_size_rx: 8192

online_image:
  - id: dashboard_image
    url: "{url}"
    format: PNG
    type: {image_type}
    # The download buffer, not the decoded frame: ESPHome caps it at 64 KiB
    # and decodes into heap (or PSRAM) separately.
    buffer_size: {download_buffer}
    # Maverick decides when to refresh; never poll on the component's own timer.
    update_interval: never{header_block}
    on_download_finished:
      - component.update: eink
    on_error:
      - logger.log: "frame download failed"

spi:
  clk_pin: {options.clk_pin}
  mosi_pin: {options.mosi_pin}

display:
  - platform: {platform}
    id: eink
    cs_pin: {options.cs_pin}
    dc_pin: {options.dc_pin}
    busy_pin: {options.busy_pin}
    reset_pin: {options.reset_pin}
{model_line}
    rotation: 0
    # Maverick pushes; the panel must not redraw on a timer of its own, or it
    # will burn refreshes drawing a frame it already shows.
    update_interval: never
    lambda: |-
      it.image(0, 0, id(dashboard_image));
{schedule_block}
button:
  - platform: template
    name: "Fetch frame now"
    on_press:
      - component.update: dashboard_image

sensor:
  - platform: wifi_signal
    name: "Wi-Fi signal"
    update_interval: 120s

text_sensor:
  - platform: template
    name: "Last frame"
    lambda: 'return {{"{display.id}"}};'
    update_interval: 3600s
"""
    return {
        "yaml": yaml,
        "node": node,
        "filename": f"{node}.yaml",
        "board": options.board,
        "platform": platform,
        "model": profile.esphome_model,
        "model_known": model_known,
        "image_type": image_type,
        "decoded_bytes": decoded,
        "needs_psram": needs_psram,
        "deep_sleep": options.deep_sleep,
        "frame_url": url,
        "secrets": secrets,
    }


def generate_all(config: Config) -> dict[str, str]:
    """Generate configs for every display whose transport is a pull transport."""
    out: dict[str, str] = {}
    for display in config.enabled_displays:
        resolved = display.resolved()
        if display.transport_type in _ESPHOME_TRANSPORTS:
            out[display.id] = generate_esphome_config(resolved, config)
    return out


__all__ = [
    "DOWNLOAD_BUFFER_MAX",
    "PSRAM_THRESHOLD",
    "SECRET_API_KEY",
    "SECRET_AUTHORIZATION",
    "SECRET_WIFI_PASSWORD",
    "SECRET_WIFI_SSID",
    "describe_esphome",
    "esphome_applicable",
    "generate_all",
    "generate_esphome_config",
]
