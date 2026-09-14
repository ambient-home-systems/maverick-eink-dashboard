"""Configuration schema.

A display's configuration is deliberately shallow: name a panel from the
catalogue and a dashboard path, and everything else has a defensible default
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
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .devices import PanelProfile, get_panel
from .eink.dither import DitherMode
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


def expand_env(value: Any) -> Any:
    """Recursively expand ``${VAR}`` / ``${VAR:-default}``."""
    if isinstance(value, str):
        def _sub(m: re.Match[str]) -> str:
            found = os.environ.get(m.group(1))
            if found is None:
                if m.group(2) is None:
                    raise ConfigError(
                        f"Environment variable {m.group(1)} is referenced in the "
                        f"config but not set (use ${{{m.group(1)}:-default}} to "
                        "make it optional)."
                    )
                return m.group(2)
            return found
        return _ENV_PATTERN.sub(_sub, value)
    if isinstance(value, dict):
        return {k: expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [expand_env(v) for v in value]
    return value


class Base(BaseModel):
    # validate_default matters: several fields accept "5m" and normalise to
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

    Inside the add-on both values are injected automatically, so users never
    see them. ``token`` must be a long-lived access token: the supervisor token
    authenticates against the REST API but not the frontend, and rendering a
    dashboard requires a frontend session.
    """

    url: str = "http://homeassistant.local:8123"
    token: str = ""
    verify_ssl: bool = True
    #: Used only when rendering, if the frontend must be reached on a different
    #: host than the API (reverse proxies, add-on networking).
    frontend_url: str | None = None

    @field_validator("url", "frontend_url")
    @classmethod
    def _strip_slash(cls, v: str | None) -> str | None:
        return v.rstrip("/") if v else v

    @property
    def render_url(self) -> str:
        return (self.frontend_url or self.url).rstrip("/")


class MqttConfig(Base):
    """MQTT is optional, but it is how displays appear natively in HA."""

    enabled: bool = False
    host: str = "core-mosquitto"
    port: int = 1883
    username: str = ""
    password: str = ""
    client_id: str = "maverick"
    discovery_prefix: str = "homeassistant"
    base_topic: str = "maverick"
    tls: bool = False


class ServerConfig(Base):
    host: str = "0.0.0.0"  # noqa: S104 - a container needs to bind all interfaces
    port: int = 5000
    #: Advertised to devices that pull frames. Must be reachable *from them*.
    base_url: str = ""
    #: Optional bearer token for the pull/trigger endpoints.
    api_token: str = ""
    enable_ui: bool = True


# --------------------------------------------------------------------------- #
# Display configuration
# --------------------------------------------------------------------------- #

class ThemeConfig(Base):
    """Overrides for the injected e-ink stylesheet."""

    enabled: bool = True
    body_mm: float = 3.2
    scale_ratio: float = 1.25
    min_font_weight: int = Field(default=400, ge=100, le=900)
    strong_font_weight: int = Field(default=700, ge=100, le=900)
    rule_mm: float = 0.25
    radius_mm: float = 0.0
    font_stack: str | None = None
    hide_chrome: bool = True
    use_spot_colour: bool = True
    letter_spacing_em: float | None = None
    extra_css: str = ""
    #: Path to a CSS file merged after ``extra_css``.
    css_file: str | None = None


class ImageConfig(Base):
    """Overrides for the image pipeline."""

    dither: DitherMode = DitherMode.AUTO
    fit: FitMode = FitMode.CONTAIN
    serpentine: bool = True
    exposure: float = 1.0
    contrast: float = 1.08
    gamma: float = 1.0
    saturation: float = 1.0
    sharpen: float = 0.6
    black_level: int = Field(default=0, ge=0, le=255)
    white_level: int = Field(default=255, ge=0, le=255)
    invert: bool = False
    palette_overrides: dict[str, tuple[int, int, int]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _levels(self) -> ImageConfig:
        if self.black_level >= self.white_level:
            raise ConfigError("black_level must be below white_level")
        return self


class RenderConfig(Base):
    """How the browser should capture the dashboard."""

    #: Extra wait after load. Cards that fetch history need a moment.
    settle: str | float = "2s"
    timeout: str | float = "45s"
    #: Render at N times panel resolution then downsample. 2 gives markedly
    #: better text on low-dpi panels; costs memory and time.
    supersample: int = Field(default=2, ge=1, le=4)
    #: Browser viewport, if it should differ from the panel's logical size.
    viewport_width: int | None = None
    viewport_height: int | None = None
    #: Zoom the page before capture (HA's own layout breakpoints respond to it).
    zoom: float = 1.0
    #: CSS selector to wait for, and optionally to crop to.
    wait_for_selector: str | None = None
    crop_to_selector: str | None = None
    #: Wait until every <img> has decoded. Weather icons are usually the
    #: slowest thing on the page.
    wait_for_images: bool = True
    #: Keep the pre-quantisation screenshot next to the frame for debugging.
    debug_artifacts: bool = False

    @field_validator("settle", "timeout")
    @classmethod
    def _duration(cls, v: str | float) -> float:
        return parse_duration(v)


class ScheduleConfig(Base):
    """When to re-render.

    ``every`` and ``cron`` are mutually exclusive. ``on_change`` subscribes to
    Home Assistant's state stream, which is how a render follows the data
    instead of a clock.
    """

    enabled: bool = True
    every: str | float | None = None
    cron: str | None = None
    #: Skip scheduled renders in this window. Format "23:00-06:30".
    quiet_hours: str | None = None
    #: Re-render when any of these entities changes state.
    on_change: list[str] = Field(default_factory=list)
    #: Ignore on_change bursts closer together than this.
    debounce: str | float = "10s"
    #: Force a flashing full refresh every N frames to clear ghosting.
    #: 0 uses the panel profile's recommendation.
    full_refresh_every: int = 0
    #: Skip delivery when the frame is byte-identical to the last one.
    skip_unchanged: bool = True
    #: Render once at startup.
    render_on_start: bool = True

    @field_validator("debounce")
    @classmethod
    def _debounce(cls, v: str | float) -> float:
        return parse_duration(v)

    @model_validator(mode="after")
    def _exclusive(self) -> ScheduleConfig:
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

    type: str = "http_pull"


class EsphomeConfig(Base):
    """Inputs for the generated ESPHome configuration.

    Only needed if you want Maverick to write the device firmware config for
    you. Pin defaults match the most common wiring for an ESP32 devkit driving
    a Waveshare panel via the vendor's HAT.
    """

    board: str = "esp32dev"
    #: Wiring. Override per board; the defaults are the Waveshare ESP32 driver
    #: board pinout, which is what most people actually have.
    clk_pin: str = "GPIO13"
    mosi_pin: str = "GPIO14"
    cs_pin: str = "GPIO15"
    dc_pin: str = "GPIO27"
    busy_pin: str = "GPIO25"
    reset_pin: str = "GPIO26"
    #: Sleep between fetches instead of staying awake. Essential on battery,
    #: and it means the device is unreachable between wakes.
    deep_sleep: bool = False
    #: Bytes reserved for the downloaded frame. Too small and the fetch fails;
    #: generated from the panel size when left at 0.
    buffer_size: int = 0
    verify_ssl: bool = False
    node_name: str | None = None


class DisplayConfig(Base):
    """One physical panel."""

    id: str
    name: str = ""
    panel: str = "generic-mono"
    dashboard: str = "/lovelace/0"
    enabled: bool = True

    # Panel overrides — all default to the catalogue entry.
    width: int | None = None
    height: int | None = None
    color_scheme: ColorScheme | None = None
    dpi: int | None = None
    rotation: int | None = None
    frame_format: FrameFormat | None = None

    theme: ThemeConfig = Field(default_factory=ThemeConfig)
    image: ImageConfig = Field(default_factory=ImageConfig)
    render: RenderConfig = Field(default_factory=RenderConfig)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    transport: TransportConfig = Field(default_factory=TransportConfig)
    pack: PackOptionsConfig = Field(default_factory=lambda: PackOptionsConfig())
    esphome: EsphomeConfig = Field(default_factory=lambda: EsphomeConfig())

    @field_validator("id")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not re.match(r"^[a-z0-9][a-z0-9_-]*$", v):
            raise ConfigError(
                f"display id {v!r} must be lowercase alphanumeric with - or _ "
                "(it becomes a URL path and an MQTT topic)"
            )
        return v

    @model_validator(mode="after")
    def _defaults(self) -> DisplayConfig:
        if not self.name:
            object.__setattr__(self, "name", self.id.replace("-", " ").replace("_", " ").title())
        # Validate the panel now so a typo fails at load, not at first render.
        get_panel(self.panel)
        if self.rotation is not None and self.rotation not in (0, 90, 180, 270):
            raise ConfigError("rotation must be 0, 90, 180 or 270")
        return self

    @property
    def profile(self) -> PanelProfile:
        return get_panel(self.panel)

    def resolved(self) -> ResolvedDisplay:
        """Merge catalogue defaults with user overrides."""
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

    msb_first: bool = True
    invert: bool = False
    plane_order: list[str] | None = None
    plane_active_low: bool = False

    def to_options(self) -> PackOptions:
        return PackOptions(
            msb_first=self.msb_first,
            invert=self.invert,
            plane_order=tuple(self.plane_order) if self.plane_order else None,
            plane_active_low=self.plane_active_low,
        )


def _default_format_for(transport_type: str) -> FrameFormat:
    """Pick the wire format a transport actually wants."""
    return {
        "opendisplay": FrameFormat.PNG,  # the library takes a PIL image
        "mqtt": FrameFormat.PNG,
        "file": FrameFormat.PNG,
        "http_pull": FrameFormat.PNG,
        "webhook": FrameFormat.PNG,
        "esphome": FrameFormat.PNG,
    }.get(transport_type, FrameFormat.PACKED)


class ResolvedDisplay(BaseModel):
    """A display with catalogue defaults and overrides merged."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    config: DisplayConfig
    profile: PanelProfile
    width: int
    height: int
    color_scheme: ColorScheme
    dpi: int
    rotation: int
    frame_format: FrameFormat
    full_refresh_every: int

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
    home_assistant: HomeAssistantConfig = Field(default_factory=HomeAssistantConfig)
    mqtt: MqttConfig = Field(default_factory=MqttConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    displays: list[DisplayConfig] = Field(default_factory=list)
    #: Directory for rendered frames, previews and state.
    data_dir: str = "./data"
    log_level: Literal["debug", "info", "warning", "error"] = "info"
    #: Refuse to deliver a frame whose lint report has errors.
    block_on_lint_error: bool = True

    @model_validator(mode="after")
    def _unique_ids(self) -> Config:
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


def load_config(path: str | Path) -> Config:
    """Load, env-expand and validate a config file."""
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} must contain a YAML mapping at the top level")
    return Config.model_validate(expand_env(raw))


DisplayConfig.model_rebuild()

__all__ = [
    "Config", "DisplayConfig", "ResolvedDisplay", "HomeAssistantConfig", "MqttConfig",
    "ServerConfig", "ThemeConfig", "ImageConfig", "RenderConfig", "ScheduleConfig",
    "TransportConfig", "PackOptionsConfig", "EsphomeConfig", "load_config", "ConfigError",
    "parse_duration",
]
