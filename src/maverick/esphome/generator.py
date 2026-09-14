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
"""

from __future__ import annotations

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

#: Bytes per pixel for each decode type, used to size the frame buffer.
_BYTES_PER_PIXEL = {"BINARY": 0.125, "GRAYSCALE": 1.0, "RGB565": 2.0}


def _buffer_size(display: ResolvedDisplay, image_type: str) -> int:
    pixels = display.width * display.height
    # 20% headroom: the decoder needs scratch space, and a too-small buffer
    # fails at download time with a message that is not obviously about size.
    return int(pixels * _BYTES_PER_PIXEL[image_type] * 1.2) + 1024


def generate_esphome_config(display: ResolvedDisplay, config: Config) -> str:
    """Return a complete ESPHome YAML document for ``display``."""
    options = display.config.esphome
    profile = display.profile
    node = options.node_name or f"{display.id}-panel".replace("_", "-")
    image_type = _IMAGE_TYPE[display.color_scheme]
    buffer = options.buffer_size or _buffer_size(display, image_type)

    base = (config.server.base_url or "http://maverick.local:5000").rstrip("/")
    url = f"{base}/api/displays/{display.id}/frame"

    interval = display.config.schedule.interval_seconds or 900
    interval_text = f"{int(interval)}s"

    if not profile.esphome_model:
        model_line = (
            "    # This panel is not in ESPHome's waveshare_epaper model list.\n"
            "    # Set `model:` to the closest match from\n"
            "    # https://esphome.io/components/display/waveshare_epaper.html\n"
            "    model: 7.50inV2"
        )
    else:
        model_line = f"    model: {profile.esphome_model}"

    # A plain ESP32 has ~200 KB of usable heap. A full-colour or 16-grey frame
    # can exceed that by an order of magnitude, and the failure mode at flash
    # time is an allocation error that says nothing about panel choice.
    ram_warning = ""
    if buffer > 180_000:
        ram_warning = f"""
# ---------------------------------------------------------------------------
# WARNING: this frame needs {buffer // 1024} KB of RAM to decode, which a plain
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

    psram_block = ""
    if buffer > 180_000:
        psram_block = """
psram:
  mode: octal
  speed: 80MHz
"""

    token_header = ""
    if config.server.api_token:
        token_header = (
            "\n    headers:\n"
            f'      Authorization: "Bearer {config.server.api_token}"'
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

    return f"""{ram_warning}# ESPHome configuration for "{display.name}"
# Generated by Maverick for {profile.name}
# {display.width}x{display.height}, {display.color_scheme.value}, ~{display.dpi} dpi
#
# The device does no layout work: it downloads the frame Maverick already
# rendered and draws it. Change your dashboard, not this file.
#
# Before flashing:
#   1. Put wifi_ssid / wifi_password / api_key in your ESPHome secrets.yaml.
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
  ssid: !secret wifi_ssid
  password: !secret wifi_password
  # A captive portal fallback saves a trip to the panel when Wi-Fi changes.
  ap:
    ssid: "{node} setup"

captive_portal:

logger:
  level: INFO

api:
  encryption:
    key: !secret api_key

ota:
  - platform: esphome
{power}
http_request:
  verify_ssl: {str(options.verify_ssl).lower()}
  timeout: 30s
  # A rendered frame is large for an ESP32; this must exceed the buffer below.
  buffer_size_rx: {min(buffer + 2048, 65536)}

online_image:
  - id: dashboard_image
    url: "{url}"
    format: PNG
    type: {image_type}
    buffer_size: {buffer}
    # Maverick decides when to refresh; never poll on the component's own timer.
    update_interval: never{token_header}
    on_download_finished:
      - component.update: eink
    on_error:
      - logger.log: "frame download failed"

spi:
  clk_pin: {options.clk_pin}
  mosi_pin: {options.mosi_pin}

display:
  - platform: waveshare_epaper
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


def generate_all(config: Config) -> dict[str, str]:
    """Generate configs for every display whose transport is a pull transport."""
    out: dict[str, str] = {}
    for display in config.enabled_displays:
        resolved = display.resolved()
        if display.transport.type in ("http_pull", "mqtt", "file"):
            out[display.id] = generate_esphome_config(resolved, config)
    return out


__all__ = ["generate_esphome_config", "generate_all"]
