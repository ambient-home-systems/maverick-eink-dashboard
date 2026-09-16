"""Configuration schema.

A display's configuration is deliberately shallow: name a panel from the
catalog and a dashboard path, and everything else has a defensible default
derived from the panel. Every derived value remains overridable, because
someone always has a panel mounted sideways behind glass.

Environment substitution (``${VAR}`` and ``${VAR:-default}``) is supported
everywhere so secrets stay out of the file — which matters because this config
usually lives in a Home Assistant add-on's ``/config`` share.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    field_validator,
    model_validator,
)

from .devices import PanelProfile, get_panel
from .eink.dither import DitherMode
from .eink.lint import LintThresholds
from .eink.pack import FrameFormat, PackOptions
from .eink.palette import ColorScheme
from .eink.pipeline import FitMode

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")
_DURATION = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(ms|s|m|h|d)?\s*$", re.I)
_UNITS = {"ms": 0.001, "s": 1, "m": 60, "h": 3600, "d": 86400}


class ConfigError(ValueError):
    """Raised for a configuration problem a user can fix."""


def parse_duration(value: str | int | float) -> float:
    """Accept ``30``, ``"30s"``, ``"5m"``, ``"1h"`` and return seconds."""
    if isinstance(value, (int, float)):
        return float(value)
    match = _DURATION.match(str(value))
    if not match:
        raise ConfigError(f"{value!r} is not a duration (try '30s', '5m', '1h')")
    return float(match.group(1)) * _UNITS[(match.group(2) or "s").lower()]


#: What `displays[].dashboard` renders when nothing says otherwise, and the
#: value `pages` is checked against: a display sets one or the other, not both.
DEFAULT_DASHBOARD = "/lovelace/0"


def page_name_for(dashboard: str) -> str:
    """Derive a page name from a dashboard path: its last segment, in words.

    ``/lovelace-eink/kitchen`` becomes "Kitchen" and
    ``file:///opt/mockups/wall-board.html`` becomes "Wall Board". A path with
    nothing to take a segment from keeps the whole value, because a page with
    no name at all could not be selected by one.
    """
    trimmed = dashboard.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    segment = trimmed.rsplit("/", 1)[-1]
    if segment.endswith((".html", ".htm")):
        segment = segment.rsplit(".", 1)[0]
    words = segment.replace("-", " ").replace("_", " ").strip()
    return words.title() if words else dashboard


def expand_env(value: Any) -> Any:
    """Recursively expand ``${VAR}`` / ``${VAR:-default}``.

    ``:-`` means what it means in a shell: the default stands in when the
    variable is unset *or* set to the empty string. The distinction is not
    academic here, because under the Home Assistant app a value is set for
    every substitution in the starter config, empty for the options a user has
    not filled in (``load_app_options`` in ``src/maverick/ha/options.py``) — so
    treating empty as "set" would push an empty string past defaults like
    ``${MQTT_PORT:-1883}`` and fail validation on a field that has a perfectly
    good fallback written next to it.

    ``${VAR}`` without a default still requires the variable to be set, and
    still yields the empty string when it is set and empty: with no default
    written there is nothing else it could mean.
    """
    if isinstance(value, str):
        def _sub(m: re.Match[str]) -> str:
            found = os.environ.get(m.group(1))
            default = m.group(2)
            if found is None:
                if default is None:
                    raise ConfigError(
                        f"Environment variable {m.group(1)} is referenced in the "
                        f"config but not set (use ${{{m.group(1)}:-default}} to "
                        "make it optional)."
                    )
                return default
            if not found and default is not None:
                return default
            return found
        return _ENV_PATTERN.sub(_sub, value)
    if isinstance(value, dict):
        return {k: expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [expand_env(v) for v in value]
    return value


class Base(BaseModel):
    # validate_default matters: several fields accept "5m" and normalize to
    # seconds in a validator, and pydantic skips validators on defaults unless
    # told otherwise — which would leave callers with a str where a float is
    # documented.
    model_config = ConfigDict(
        extra="forbid", validate_assignment=True, validate_default=True
    )


# --------------------------------------------------------------------------- #
# Connections
# --------------------------------------------------------------------------- #

class HomeAssistantConfig(Base):
    """How to reach Home Assistant.

    Values come from the config file, or from ``${HA_TOKEN}``-style environment
    substitution. Whatever authenticates has to be good for a *frontend*
    session, not just the REST API: rendering a dashboard means loading it in a
    browser, and the supervisor token does not give a browser a session.

    Two credentials satisfy that. ``token`` is a long-lived access token copied
    from a profile page. ``refresh_token`` with ``client_id`` is an IndieAuth
    grant, which the setup UI obtains for itself — see
    `src/maverick/ha/auth.py`. A linked account takes precedence when both are
    present.
    """

    url: str = Field(
        default="http://homeassistant.local:8123",
        description=(
            "Base URL of Home Assistant, used for the REST and WebSocket APIs and, "
            "unless `frontend_url` is set, for loading the dashboard in the browser."
        ),
    )
    token: str = Field(
        default="",
        description=(
            "Long-lived access token from a Home Assistant profile. The supervisor token "
            "does not work: it authenticates the REST API but not the frontend, and "
            "rendering a dashboard needs a frontend session. Leave empty to link an "
            "account from the setup UI instead."
        ),
    )
    refresh_token: str = Field(
        default="",
        description=(
            "IndieAuth refresh token, normally obtained by the setup UI's *Link with "
            "Home Assistant* button rather than written by hand. Needs `client_id` set "
            "too, and takes precedence over `token`."
        ),
    )
    client_id: str = Field(
        default="",
        description=(
            "The client identifier `refresh_token` was issued to, which Home Assistant "
            "requires on every refresh. The setup UI uses `server.base_url`."
        ),
    )
    verify_ssl: bool = Field(
        default=True,
        description=(
            "Verify Home Assistant's TLS certificate. False also tells the renderer to "
            "ignore certificate errors, which is what a self-signed certificate needs."
        ),
    )
    frontend_url: str | None = Field(
        default=None,
        description=(
            "Used only when rendering, if the frontend must be reached on a different "
            "host than the API (reverse proxies, add-on networking)."
        ),
    )

    @field_validator("url", "frontend_url")
    @classmethod
    def _strip_slash(cls, v: str | None) -> str | None:
        """A trailing slash on `url` or `frontend_url` is stripped."""
        return v.rstrip("/") if v else v

    @property
    def render_url(self) -> str:
        return (self.frontend_url or self.url).rstrip("/")

    @property
    def has_credentials(self) -> bool:
        """Whether anything here can authenticate a frontend session."""
        return bool(self.token or (self.refresh_token and self.client_id))


class MqttConfig(Base):
    """MQTT is optional, but it is how displays appear natively in HA."""

    enabled: bool = Field(
        default=False,
        description=(
            "Connect to the broker, publish frames for the `mqtt` transport and announce "
            "every display to Home Assistant by discovery. Off means displays never appear "
            "as Home Assistant devices."
        ),
    )
    host: str = Field(
        default="core-mosquitto",
        description="Broker hostname. `core-mosquitto` is the Home Assistant add-on broker.",
    )
    port: int = Field(
        default=1883,
        description="Broker port: usually 1883, or 8883 when `tls` is on.",
    )
    username: str = Field(
        default="",
        description="Broker username. Empty connects anonymously.",
    )
    password: str = Field(
        default="",
        description="Broker password, sent with `username`.",
    )
    client_id: str = Field(
        default="maverick",
        description=(
            "MQTT client identifier used when connecting. It must be unique on the broker: "
            "two clients sharing one identifier disconnect each other in a loop."
        ),
    )
    discovery_prefix: str = Field(
        default="homeassistant",
        description=(
            "Topic prefix Home Assistant watches for discovery messages. Change it only if "
            "it was changed in the MQTT integration."
        ),
    )
    base_topic: str = Field(
        default="maverick",
        description=(
            "Root of Maverick's own topics: `<base_topic>/display/<id>/frame` and "
            "`<base_topic>/display/<id>/meta` per display, commands on "
            "`<base_topic>/display/+/command`, and availability on `<base_topic>/status`."
        ),
    )
    tls: bool = Field(
        default=False,
        description="Connect to the broker over TLS.",
    )


class ServerConfig(Base):
    """Maverick's own HTTP server.

    It serves the frames devices pull, the trigger and status API, and the setup
    UI. Devices are told where to fetch from by ``base_url``, so that value is
    the one worth getting right.
    """

    host: str = Field(
        default="0.0.0.0",  # noqa: S104 - a container needs to bind all interfaces
        description="Address the HTTP server binds to. `0.0.0.0` is right inside a container.",
    )
    port: int = Field(
        default=5000,
        description="Port the HTTP server listens on.",
    )
    base_url: str = Field(
        default="",
        description=(
            "Advertised to devices that pull frames, and used for the links in Home "
            "Assistant discovery. Must be reachable *from them*, not just from your laptop."
        ),
    )
    api_token: str = Field(
        default="",
        description=(
            "Optional token for the API, the setup UI and the preview images, accepted as a "
            "bearer token, an `Access-Token` header or a `?token=` query parameter, and "
            "written into generated ESPHome configurations. Requests arriving through the "
            "Home Assistant app's ingress are exempt, because Home Assistant has already "
            "authenticated them. Setting it also switches the MQTT image entity to sending "
            "frames over the broker, since Home Assistant fetches an image URL with no "
            "credentials. Empty leaves every endpoint unauthenticated."
        ),
    )
    enable_ui: bool = Field(
        default=True,
        description="Serve the setup UI at `/`. False serves the JSON API alone.",
    )


# --------------------------------------------------------------------------- #
# Display configuration
# --------------------------------------------------------------------------- #

class ThemeConfig(Base):
    """Overrides for the injected e-ink stylesheet."""

    enabled: bool = Field(
        default=True,
        description=(
            "Inject the generated e-ink stylesheet. False leaves Home Assistant's own "
            "styling in place and applies `extra_css` alone."
        ),
    )
    body_mm: float = Field(
        default=3.2,
        description=(
            "Height of body text in millimeters on the panel, converted to pixels using the "
            "panel's dpi. It is a physical size, not a browser font size."
        ),
    )
    scale_ratio: float = Field(
        default=1.25,
        description="Ratio of the modular type scale: each step up multiplies by this.",
    )
    min_font_weight: int = Field(
        default=400,
        ge=100,
        le=900,
        description="Minimum font weight. 400 is the floor at which stems survive 1-bit output.",
    )
    strong_font_weight: int = Field(
        default=700,
        ge=100,
        le=900,
        description="Weight used for headings and numeric readouts.",
    )
    rule_mm: float = Field(
        default=0.25,
        description="Border width in millimeters — hairlines disappear below roughly 0.2 mm.",
    )
    radius_mm: float = Field(
        default=0.0,
        description="Card corner radius in millimeters. Sharp corners dither more cleanly.",
    )
    font_stack: str | None = Field(
        default=None,
        description=(
            "CSS font stack for the page. Unset uses the built-in e-ink stack, which prefers "
            "faces whose stems survive quantization."
        ),
    )
    hide_chrome: bool = Field(
        default=True,
        description="Hide the Home Assistant toolbar, sidebar and other frontend chrome.",
    )
    use_spot_colour: bool = Field(
        default=True,
        description=(
            "Let the panel's spot ink carry alerts and state highlights. The ink is used as "
            "a text color where it is legible against the panel's white, and as a highlight "
            "fill under the foreground ink where it is not — yellow is 1.3:1 as text and "
            "6.9:1 under black. It has no effect on a panel without a spot ink."
        ),
    )
    letter_spacing_em: float | None = Field(
        default=None,
        description=(
            "Letter spacing in em. Unset derives it from the panel's dpi: 0.012 below 150 dpi, "
            "where adjacent stems smear together, and 0 above."
        ),
    )
    extra_css: str = Field(
        default="",
        description=(
            "CSS appended verbatim, last, so it can override anything the theme generates. "
            "It is applied even when `enabled` is false."
        ),
    )
    css_file: str | None = Field(
        default=None,
        description=(
            "Path to a CSS file merged after `extra_css`. A path that does not exist fails "
            "the render rather than being skipped."
        ),
    )


class ImageConfig(Base):
    """Overrides for the image pipeline."""

    dither: DitherMode = Field(
        default=DitherMode.AUTO,
        description="How continuous tone is mapped onto the panel's inks; see Dither modes.",
    )
    fit: FitMode = Field(
        default=FitMode.CONTAIN,
        description="How the screenshot is resized onto the panel; see Fit modes.",
    )
    serpentine: bool = Field(
        default=True,
        description=(
            "Alternate the scan direction on each row while diffusing error, which hides "
            "directional artefacts. Only the error-diffusion kernels use it: `none` and "
            "`ordered` have no error to carry, and `auto` diffuses through Pillow, whose "
            "implementation is always left-to-right."
        ),
    )
    exposure: float = Field(
        default=1.0,
        description="Multiplies luminance before quantization. Above 1 lightens.",
    )
    contrast: float = Field(
        default=1.08,
        description=(
            "Contrast stretch before quantization. E-ink benefits from a little more than a "
            "screen wants."
        ),
    )
    gamma: float = Field(
        default=1.0,
        description=(
            "Gamma applied before quantization. Panels are closer to linear than sRGB, so a "
            "mild decode keeps midtones from crushing to black."
        ),
    )
    saturation: float = Field(
        default=1.0,
        description=(
            "Saturation boost for color panels. Their gamut is small; pushing saturation "
            "first means more pixels land on a real ink instead of dithering between two."
        ),
    )
    sharpen: float = Field(
        default=0.6,
        description=(
            "Unsharp mask radius in pixels. Ink bleeds slightly; a light sharpen restores the "
            "edge the panel loses. 0 disables it."
        ),
    )
    black_level: int = Field(
        default=0,
        ge=0,
        le=255,
        description="Black clip point, 0-255, applied as a level stretch before quantization.",
    )
    white_level: int = Field(
        default=255,
        ge=0,
        le=255,
        description="White clip point, 0-255, applied as a level stretch before quantization.",
    )
    invert: bool = Field(
        default=False,
        description=(
            "Invert the image before quantization, for a panel wired or mounted to show a "
            "negative. Distinct from `pack.invert`, which inverts the packed indices instead."
        ),
    )
    palette_overrides: dict[str, tuple[int, int, int]] = Field(
        default_factory=dict,
        description=(
            "Measured ink values for this panel, keyed by palette name (`black`, `white`, "
            "`red`, ...) with `[r, g, b]` values. Despite sitting under `image`, they drive "
            "both the quantization palette and the injected stylesheet, so the page is styled "
            "with the same inks the frame is quantized to."
        ),
    )

    @model_validator(mode="after")
    def _levels(self) -> ImageConfig:
        """`black_level` must be below `white_level`."""
        if self.black_level >= self.white_level:
            raise ConfigError("black_level must be below white_level")
        return self


class LintConfig(Base):
    """Thresholds for the render linter."""

    blank_ratio: float = Field(
        default=0.995,
        gt=0.0,
        le=1.0,
        description=(
            "Fraction of the frame that may be a single ink before `blank_render` calls it "
            "blank. That finding is an error, so it also blocks delivery while "
            "`block_on_lint_error` is true."
        ),
    )
    max_ink_coverage: float = Field(
        default=0.62,
        ge=0.0,
        le=1.0,
        description=(
            "Fraction of the frame that may be a non-white ink before `heavy_ink` warns. "
            "Dense frames refresh slowly and ghost more."
        ),
    )
    max_hairline_ratio: float = Field(
        default=0.28,
        ge=0.0,
        le=1.0,
        description=(
            "Fraction of inked pixels that may lie on the boundary of the inked area before "
            "`hairlines` warns. Text-heavy monochrome frames sit near 1.0 by construction; "
            "see the design guide before lowering it."
        ),
    )
    max_speckle_ratio: float = Field(
        default=0.035,
        ge=0.0,
        le=1.0,
        description=(
            "Fraction of the frame that may be isolated salt-and-pepper dither noise before "
            "`dither_speckle` warns."
        ),
    )
    max_spot_coverage: float = Field(
        default=0.18,
        ge=0.0,
        le=1.0,
        description=(
            "Coverage any one spot ink may reach before `spot_ink_overuse.<ink>` warns. "
            "Checked for every pigment beyond black, white and the grey ramp."
        ),
    )
    min_feature_mm: float = Field(
        default=0.18,
        gt=0.0,
        description=(
            "Pixel size in millimeters below which `sub_threshold_pixel` notes that "
            "single-pixel detail is under the eye's threshold at reading distance."
        ),
    )

    def to_thresholds(self) -> LintThresholds:
        return LintThresholds(
            blank_ratio=self.blank_ratio,
            max_ink_coverage=self.max_ink_coverage,
            max_hairline_ratio=self.max_hairline_ratio,
            max_speckle_ratio=self.max_speckle_ratio,
            max_spot_coverage=self.max_spot_coverage,
            min_feature_mm=self.min_feature_mm,
        )


class RenderConfig(Base):
    """How the browser should capture the dashboard."""

    settle: str | float = Field(
        default="2s",
        description="Extra wait after load. Cards that fetch history need a moment.",
    )
    timeout: str | float = Field(
        default="45s",
        description=(
            "Budget for navigation and for waiting on `wait_for_selector`. Exceeding it "
            "fails the render."
        ),
    )
    supersample: int = Field(
        default=2,
        ge=1,
        le=4,
        description=(
            "Render at N times panel resolution then downsample. 2 gives markedly better text "
            "on low-dpi panels; it costs memory and time."
        ),
    )
    viewport_width: int | None = Field(
        default=None,
        description=(
            "Browser viewport width, if it should differ from the panel's logical size. Used "
            "only when `viewport_height` is set as well."
        ),
    )
    viewport_height: int | None = Field(
        default=None,
        description=(
            "Browser viewport height, if it should differ from the panel's logical size. Used "
            "only when `viewport_width` is set as well."
        ),
    )
    zoom: float = Field(
        default=1.0,
        description=(
            "Zoom the page before capture; Home Assistant's own layout breakpoints respond to "
            "it. With the theme enabled it multiplies the dpi-derived zoom rather than "
            "fighting it."
        ),
    )
    wait_for_selector: str | None = Field(
        default=None,
        description=(
            "CSS selector to wait for before capturing. Unset waits for `home-assistant`, "
            "which is present on every Home Assistant page."
        ),
    )
    crop_to_selector: str | None = Field(
        default=None,
        description=(
            "CSS selector to capture instead of the whole page. A selector that matches "
            "nothing fails the render rather than capturing the page."
        ),
    )
    wait_for_images: bool = Field(
        default=True,
        description=(
            "Wait until every `<img>` has decoded. Weather icons are usually the slowest thing "
            "on the page."
        ),
    )
    debug_artifacts: bool = Field(
        default=False,
        description=(
            "Keep the pre-quantization screenshot next to the frame preview and the lint "
            "report, under `<data_dir>/debug/<id>/`."
        ),
    )
    keep_screenshot: bool = Field(
        default=True,
        description=(
            "Keep the pre-quantisation screenshot, downscaled to the panel's resolution, so "
            "the setup UI can show the source render next to the quantised frame — the "
            "question `debug_artifacts` otherwise needs Samba or SSH to answer. Stored in "
            "memory and under `<data_dir>/frames/<id>.screenshot.png`, one PNG at panel "
            "resolution per display; turn it off to save that memory."
        ),
    )

    @field_validator("settle", "timeout")
    @classmethod
    def _duration(cls, v: str | float) -> float:
        """`settle` and `timeout` accept a duration and are stored as seconds."""
        return parse_duration(v)


class ScheduleConfig(Base):
    """When to re-render.

    ``every`` and ``cron`` are mutually exclusive. ``on_change`` subscribes to
    Home Assistant's state stream, which is how a render follows the data
    instead of a clock.
    """

    enabled: bool = Field(
        default=True,
        description=(
            "Include this display in the scheduler. False leaves it renderable on demand "
            "only; a Home Assistant switch can also pause it at runtime without editing "
            "this file."
        ),
    )
    every: str | float | None = Field(
        default=None,
        description="Render on a fixed interval. Mutually exclusive with `cron`.",
    )
    cron: str | None = Field(
        default=None,
        description=(
            "Render on a five-field crontab expression, in the server's local time zone. "
            "Mutually exclusive with `every`."
        ),
    )
    quiet_hours: str | None = Field(
        default=None,
        description=(
            'Skip scheduled and state-triggered renders in this window. Format "23:00-06:30"; '
            "a window may wrap midnight. Renders asked for by hand, by the API or at startup "
            "ignore it."
        ),
    )
    on_change: list[str] = Field(
        default_factory=list,
        description=(
            "Entity ids to watch: a state change on any of them re-renders the display. It "
            "needs a Home Assistant connection, and attribute-only updates are ignored."
        ),
    )
    debounce: str | float = Field(
        default="10s",
        description="Ignore `on_change` bursts closer together than this.",
    )
    full_refresh_every: int = Field(
        default=0,
        description=(
            "Force a flashing full refresh every N frames to clear ghosting. 0 uses the panel "
            "profile's recommendation."
        ),
    )
    skip_unchanged: bool = Field(
        default=True,
        description="Skip delivery when the frame is byte-identical to the last one.",
    )
    render_on_start: bool = Field(
        default=True,
        description="Render once at startup rather than waiting out the first interval.",
    )

    @field_validator("debounce")
    @classmethod
    def _debounce(cls, v: str | float) -> float:
        """`debounce` accepts a duration and is stored as seconds."""
        return parse_duration(v)

    @model_validator(mode="after")
    def _exclusive(self) -> ScheduleConfig:
        """Set `every` or `cron`, not both, and `quiet_hours` must look like "23:00-06:30"."""
        if self.every and self.cron:
            raise ConfigError("set either 'every' or 'cron', not both")
        if self.every is not None:
            parse_duration(self.every)
        if self.quiet_hours and not re.match(r"^\d{1,2}:\d{2}-\d{1,2}:\d{2}$", self.quiet_hours):
            raise ConfigError("quiet_hours must look like '23:00-06:30'")
        return self

    @property
    def interval_seconds(self) -> float | None:
        return parse_duration(self.every) if self.every is not None else None


class TransportConfig(Base):
    """Where the finished frame goes.

    Extra keys are permitted and handed to the transport, because each one has
    its own options and forbidding them would mean restating every field here.
    """

    model_config = ConfigDict(extra="allow")

    type: str = Field(
        default="http_pull",
        description=(
            "Registry name of the transport that delivers the frame. Every other key in this "
            "section is that transport's own option; see Transport options."
        ),
    )


class EsphomeConfig(Base):
    """Inputs for the generated ESPHome configuration.

    Only needed if you want Maverick to write the device firmware config for
    you. Pin defaults match the most common wiring for an ESP32 devkit driving
    a Waveshare panel via the vendor's HAT.
    """

    board: str = Field(
        default="esp32dev",
        description="PlatformIO board id written into the generated `esp32:` block.",
    )
    clk_pin: str = Field(
        default="GPIO13",
        description=(
            "SPI clock pin. The pin defaults are the Waveshare ESP32 driver board pinout, "
            "which is what most people actually have; override them per board."
        ),
    )
    mosi_pin: str = Field(default="GPIO14", description="SPI data (MOSI) pin.")
    cs_pin: str = Field(default="GPIO15", description="SPI chip-select pin.")
    dc_pin: str = Field(default="GPIO27", description="Data/command pin.")
    busy_pin: str = Field(default="GPIO25", description="Panel busy pin.")
    reset_pin: str = Field(default="GPIO26", description="Panel reset pin.")
    deep_sleep: bool = Field(
        default=False,
        description=(
            "Sleep between fetches instead of staying awake, waking on the display's own "
            "interval. Essential on battery, and it means the device is unreachable — and so "
            "cannot be flashed over the air — between wakes."
        ),
    )
    buffer_size: int = Field(
        default=0,
        description=(
            "Bytes reserved for the downloaded frame. Too small and the fetch fails; 0 "
            "generates a size from the panel's resolution and color scheme."
        ),
    )
    verify_ssl: bool = Field(
        default=False,
        description=(
            "Whether the generated firmware verifies the server's TLS certificate. Off by "
            "default: certificate validation costs an ESP32 memory the frame buffer needs."
        ),
    )
    node_name: str | None = Field(
        default=None,
        description=(
            "ESPHome node name. Unset derives one from the display id, as `<id>-panel` with "
            "underscores replaced by hyphens."
        ),
    )


class PageConfig(Base):
    """One dashboard in a display's rotation.

    A display with `pages` renders one of them at a time and moves between them
    on command or, with `rotate`, on its own timeline. `dwell` is how long this
    page stays up before rotation moves on; unset means it changes at every
    scheduled render.
    """

    dashboard: str = Field(
        description=(
            "What to render for this page: a Home Assistant dashboard path such as "
            "`/lovelace-eink/kitchen`, or a fully qualified URL. Read exactly as "
            "`displays[].dashboard` is."
        ),
    )
    name: str = Field(
        default="",
        description=(
            "Label for this page, shown in the setup UI and offered by the Home "
            "Assistant Page select. Empty derives one from the last segment of "
            "`dashboard`. Names must be unique within a display, because a page is "
            "selected by name."
        ),
    )
    dwell: str | float | None = Field(
        default=None,
        description=(
            "How long this page stays on the panel before `rotate` moves to the next "
            "one. Unset advances at every scheduled render; the page never changes "
            "faster than the schedule that drives it."
        ),
    )

    @model_validator(mode="after")
    def _defaults(self) -> PageConfig:
        """An empty `name` is derived from `dashboard`, and `dwell` must be a duration."""
        if not self.name:
            object.__setattr__(self, "name", page_name_for(self.dashboard))
        if self.dwell is not None:
            parse_duration(self.dwell)
        return self

    @property
    def dwell_seconds(self) -> float | None:
        """`dwell` in seconds, or None for a page that advances on every tick."""
        return parse_duration(self.dwell) if self.dwell is not None else None


class DisplayConfig(Base):
    """One physical panel."""

    id: str = Field(
        description=(
            "Identifier for this display, unique within the file. It becomes a URL path "
            "segment and an MQTT topic level."
        ),
    )
    name: str = Field(
        default="",
        description=(
            "Human-readable name, shown in the setup UI and used for the Home Assistant "
            "device. Empty derives one from the id."
        ),
    )
    panel: str = Field(
        default="generic-mono",
        description=(
            "Panel id from the catalog, as listed by `maverick panels`. It supplies the "
            "resolution, color scheme, dpi, rotation, frame format and refresh behavior "
            "that the keys below override."
        ),
    )
    dashboard: str = Field(
        default=DEFAULT_DASHBOARD,
        description=(
            "What to render: a Home Assistant dashboard path such as "
            "`/lovelace-eink/kitchen`, or a fully qualified URL. Any scheme counts as "
            "absolute, so `file:///...` renders a local page. The single-page "
            "shorthand: set this or `pages`, not both."
        ),
    )
    pages: list[PageConfig] = Field(
        default_factory=list,
        description=(
            "Several dashboards for one panel, rendered one at a time. The page is "
            "changed by `POST /api/displays/{id}/page`, by the Home Assistant Page "
            "select, or on its own with `rotate`. Empty leaves the display on "
            "`dashboard`."
        ),
    )
    rotate: bool = Field(
        default=False,
        description=(
            "Advance to the next page on each scheduled render, once the current "
            "page's `dwell` has elapsed. Off leaves the page where it was put."
        ),
    )
    enabled: bool = Field(
        default=True,
        description="Render and deliver this display. False keeps it configured but idle.",
    )

    # Panel overrides — all default to the catalog entry.
    width: int | None = Field(
        default=None,
        description="Panel width in pixels. Unset uses the catalog value for `panel`.",
    )
    height: int | None = Field(
        default=None,
        description="Panel height in pixels. Unset uses the catalog value for `panel`.",
    )
    color_scheme: ColorScheme | None = Field(
        default=None,
        description=(
            "Inks to quantize to; see Color schemes. Unset uses the catalog value for "
            "`panel`."
        ),
    )
    dpi: int | None = Field(
        default=None,
        description=(
            "Pixels per inch, used to turn the theme's millimeter sizes into pixels. Unset "
            "uses the catalog value for `panel`."
        ),
    )
    rotation: int | None = Field(
        default=None,
        description=(
            "Rotation in degrees applied after fitting, for a panel mounted sideways. Unset "
            "uses the panel's native rotation."
        ),
    )
    frame_format: FrameFormat | None = Field(
        default=None,
        description=(
            "Wire format for the delivered frame; see Frame formats. Unset uses the panel's "
            "default format, and failing that a default for the configured transport."
        ),
    )

    theme: ThemeConfig = Field(
        default_factory=ThemeConfig,
        description="Overrides for the injected e-ink stylesheet.",
    )
    image: ImageConfig = Field(
        default_factory=ImageConfig,
        description="Overrides for the image pipeline.",
    )
    lint: LintConfig = Field(
        default_factory=LintConfig,
        description="Thresholds for the render linter.",
    )
    render: RenderConfig = Field(
        default_factory=RenderConfig,
        description="How the browser should capture the dashboard.",
    )
    schedule: ScheduleConfig = Field(
        default_factory=ScheduleConfig,
        description="When to re-render.",
    )
    transport: TransportConfig = Field(
        default_factory=TransportConfig,
        description="Where the finished frame goes.",
    )
    pack: PackOptionsConfig = Field(
        default_factory=lambda: PackOptionsConfig(),
        description="Controller quirks for raw-frame transports.",
    )
    esphome: EsphomeConfig = Field(
        default_factory=lambda: EsphomeConfig(),
        description="Inputs for the ESPHome configuration `maverick esphome <id>` generates.",
    )

    @field_validator("id")
    @classmethod
    def _slug(cls, v: str) -> str:
        """`id` must be lowercase alphanumeric with `-` or `_`, starting with a letter or digit."""
        if not re.match(r"^[a-z0-9][a-z0-9_-]*$", v):
            raise ConfigError(
                f"display id {v!r} must be lowercase alphanumeric with - or _ "
                "(it becomes a URL path and an MQTT topic)"
            )
        return v

    @model_validator(mode="after")
    def _defaults(self) -> DisplayConfig:
        """`panel` must name a catalog entry, and `rotation` must be 0, 90, 180 or 270."""
        if not self.name:
            object.__setattr__(self, "name", self.id.replace("-", " ").replace("_", " ").title())
        # Validate the panel now so a typo fails at load, not at first render.
        get_panel(self.panel)
        if self.rotation is not None and self.rotation not in (0, 90, 180, 270):
            raise ConfigError("rotation must be 0, 90, 180 or 270")
        return self

    @model_validator(mode="after")
    def _pages(self) -> DisplayConfig:
        """`dashboard` and `pages` are alternatives, and page names are unique.

        Both together would leave two answers to "what does this panel show",
        and nothing to say which wins — so it is rejected at load rather than
        resolved by a precedence rule nobody would remember. A page is selected
        by name over MQTT and in the setup UI, so two pages sharing one is
        rejected too.
        """
        if self.pages and self.dashboard != DEFAULT_DASHBOARD:
            raise ConfigError(
                "set either 'dashboard' or 'pages', not both: 'dashboard' is the "
                "single-page shorthand, and a display with pages renders those"
            )
        names = [page.name for page in self.pages]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ConfigError(
                "page names must be unique within a display, because a page is "
                f"selected by name; repeated: {', '.join(repr(n) for n in duplicates)}"
            )
        return self

    @property
    def profile(self) -> PanelProfile:
        return get_panel(self.panel)

    @property
    def page_entries(self) -> list[PageConfig]:
        """The pages this display renders, shorthand included.

        A display with no `pages` has exactly one — `dashboard` — so everything
        downstream can count pages and index into them without asking which
        form the display was written in.
        """
        if self.pages:
            return list(self.pages)
        return [PageConfig(dashboard=self.dashboard)]

    def page_at(self, index: int) -> PageConfig:
        """The page at `index`, wrapping rather than raising.

        The index lives in `DisplayState` and outlives the config it was
        recorded against, so a display whose page list has since been shortened
        must still render something.
        """
        entries = self.page_entries
        return entries[index % len(entries)]

    def page_index_for(self, name: str) -> int:
        """The index of the page called `name`. Raises `ConfigError` if there is none."""
        for index, page in enumerate(self.page_entries):
            if page.name == name:
                return index
        known = ", ".join(repr(p.name) for p in self.page_entries)
        raise ConfigError(
            f"display {self.id!r} has no page named {name!r}; its pages are {known}"
        )

    def for_page(self, index: int) -> DisplayConfig:
        """This display in its single-page form, rendering the page at `index`.

        A copy rather than a mutation, and the one place the page list turns
        back into the single `dashboard` the renderer reads (`resolve_url`,
        `src/maverick/render/dashboard.py`). The list goes with it: the page
        has been chosen by the time this is called, and a copy carrying both
        would be the shape `_pages` refuses — which `ResolvedDisplay` would
        then reject when it validates the display it wraps.
        """
        if not self.pages:
            return self
        page = self.page_at(index)
        return self.model_copy(update={"dashboard": page.dashboard, "pages": []})

    def resolved(self) -> ResolvedDisplay:
        """Merge catalog defaults with user overrides."""
        p = self.profile
        return ResolvedDisplay(
            config=self,
            profile=p,
            width=self.width or p.width,
            height=self.height or p.height,
            color_scheme=self.color_scheme or p.color_scheme,
            dpi=self.dpi or p.dpi,
            rotation=self.rotation if self.rotation is not None else p.native_rotation,
            frame_format=self.frame_format
            or (FrameFormat(p.default_format) if p.default_format else None)
            or _default_format_for(self.transport.type),
            full_refresh_every=self.schedule.full_refresh_every or p.full_refresh_every,
        )


class PackOptionsConfig(Base):
    """Controller quirks for raw-frame transports."""

    msb_first: bool = Field(
        default=True,
        description=(
            "Pack the leftmost pixel of each byte into the most significant bit. False packs "
            "it into the least significant bit."
        ),
    )
    invert: bool = Field(
        default=False,
        description=(
            "Invert the packed indices, so index 0 becomes the last ink. For controllers that "
            "clock 1 for white. Distinct from `image.invert`, which inverts the image itself."
        ),
    )
    plane_order: list[str] | None = Field(
        default=None,
        description=(
            "Ink order for the `planes` frame format, by palette name. Unset packs black "
            "first, then the spot colors."
        ),
    )
    plane_active_low: bool = Field(
        default=False,
        description=(
            "Some controllers clock 1 for the *absence* of an ink on the black plane; this "
            "inverts each plane to suit them."
        ),
    )

    def to_options(self) -> PackOptions:
        return PackOptions(
            msb_first=self.msb_first,
            invert=self.invert,
            plane_order=tuple(self.plane_order) if self.plane_order else None,
            plane_active_low=self.plane_active_low,
        )


def _default_format_for(transport_type: str) -> FrameFormat:
    """Pick the wire format a transport actually wants."""
    # Keys must match registered Transport.name values.
    return {
        "opendisplay": FrameFormat.PNG,  # the library takes a PIL image
        "mqtt": FrameFormat.PNG,
        "file": FrameFormat.PNG,
        "http_pull": FrameFormat.PNG,
        "webhook": FrameFormat.PNG,
    }.get(transport_type, FrameFormat.PACKED)


class ResolvedDisplay(BaseModel):
    """A display with catalog defaults and overrides merged."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    config: DisplayConfig = Field(description="The display as the user wrote it.")
    profile: PanelProfile = Field(description="The catalog entry named by `panel`.")
    width: int = Field(description="Panel width in pixels, after overrides.")
    height: int = Field(description="Panel height in pixels, after overrides.")
    color_scheme: ColorScheme = Field(description="Ink capability to quantize to, after overrides.")
    dpi: int = Field(description="Pixels per inch, after overrides.")
    rotation: int = Field(description="Rotation in degrees, after overrides.")
    frame_format: FrameFormat = Field(description="Wire format for the frame, after overrides.")
    full_refresh_every: int = Field(
        description="Frames between forced full refreshes, after overrides.",
    )

    @property
    def id(self) -> str:
        return self.config.id

    @property
    def name(self) -> str:
        return self.config.name


# --------------------------------------------------------------------------- #
# Root
# --------------------------------------------------------------------------- #

class Config(Base):
    home_assistant: HomeAssistantConfig = Field(
        default_factory=HomeAssistantConfig,
        description="How to reach Home Assistant.",
    )
    mqtt: MqttConfig = Field(
        default_factory=MqttConfig,
        description="MQTT broker and discovery settings.",
    )
    server: ServerConfig = Field(
        default_factory=ServerConfig,
        description="Maverick's own HTTP server.",
    )
    displays: list[DisplayConfig] = Field(
        default_factory=list,
        description=(
            "The panels to render. Each entry needs at least an `id`. Once the display "
            "store below exists it is the source of the displays and this list is "
            "ignored, so it is a starting point rather than a running record."
        ),
    )
    displays_file: str = Field(
        default="",
        description=(
            "The display store: the file Maverick writes the displays to and reads them "
            "back from, which is what lets the setup UI change one. Empty means "
            "`<data_dir>/displays.yaml`. The `displays:` list above is imported into it "
            "the first time, and ignored once it exists."
        ),
    )
    data_dir: str = Field(
        default="./data",
        description=(
            "Directory for rendered frames, previews, debug artefacts, state and — "
            "unless `displays_file` says otherwise — the display store."
        ),
    )
    log_level: Literal["debug", "info", "warning", "error"] = Field(
        default="info",
        description="Logging verbosity. The `--log-level` flag overrides it for one run.",
    )
    block_on_lint_error: bool = Field(
        default=True,
        description=(
            "Refuse to deliver a frame whose lint report has errors — a blank render, most "
            "often. `maverick render --force` overrides it for one render."
        ),
    )

    # Where `displays` came from on this load, for `maverick check` to print.
    # Private because it describes the load rather than the file: nobody writes
    # it, nothing validates it, and it must not appear in the reference or in a
    # dump.
    _displays_source: str = PrivateAttr(default="")

    @model_validator(mode="after")
    def _unique_ids(self) -> Config:
        """Display ids must be unique."""
        seen = set()
        for display in self.displays:
            if display.id in seen:
                raise ConfigError(f"duplicate display id {display.id!r}")
            seen.add(display.id)
        return self

    def display(self, display_id: str) -> DisplayConfig:
        for display in self.displays:
            if display.id == display_id:
                return display
        known = ", ".join(d.id for d in self.displays) or "none configured"
        raise KeyError(f"No display {display_id!r}. Known displays: {known}")

    @property
    def enabled_displays(self) -> list[DisplayConfig]:
        return [d for d in self.displays if d.enabled]

    @property
    def display_store_path(self) -> Path:
        """The display store's path: `displays_file`, or `<data_dir>/displays.yaml`."""
        if self.displays_file:
            return Path(self.displays_file)
        return Path(self.data_dir) / "displays.yaml"

    @property
    def displays_source(self) -> str:
        """Which file `displays` came from, in words, or "" for a config built in memory.

        Set by `load_config`, which is the only place the display store is
        consulted (`resolve_displays` in `src/maverick/store.py`).
        """
        return self._displays_source


def load_config(path: str | Path, *, use_display_store: bool = True) -> Config:
    """Load, env-expand and validate a config file.

    The displays then come from the display store rather than from the file
    itself, which is what lets the setup UI change one: `resolve_displays` in
    `src/maverick/store.py` reads the store when it exists, imports the file's
    `displays:` list into it when it does not, and says which it did in
    `Config.displays_source`. Every entry point reaches the store through here
    and nowhere else, so a `Config` built with `model_validate` never touches
    the disk.

    `use_display_store=False` skips that step and reads the file alone. It is
    for a caller that wants the parsed file and no side effect — the tests that
    load the app's starter config, whose `data_dir` is the app's `/config/data`.
    """
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} must contain a YAML mapping at the top level")
    config = Config.model_validate(expand_env(raw))
    if use_display_store:
        # Imported here rather than at the top of the module: the store is built
        # on the models above, so importing it there would be a cycle.
        from .store import resolve_displays

        resolution = resolve_displays(config, path)
        config.displays = resolution.displays
        config._displays_source = resolution.source
    return config


DisplayConfig.model_rebuild()

__all__ = [
    "Config", "DisplayConfig", "ResolvedDisplay", "HomeAssistantConfig", "MqttConfig",
    "ServerConfig", "ThemeConfig", "ImageConfig", "RenderConfig", "ScheduleConfig",
    "TransportConfig", "PackOptionsConfig", "EsphomeConfig", "PageConfig", "load_config",
    "ConfigError", "parse_duration", "page_name_for", "DEFAULT_DASHBOARD",
]
