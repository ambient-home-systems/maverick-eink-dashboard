"""The panel catalogue: pick a model, get every parameter right.

The single biggest source of friction in DIY e-ink dashboards is that a user
has to know their panel's resolution, colour capability, native rotation, byte
format and refresh characteristics before anything works. Maverick ships that
knowledge so the user writes ``panel: waveshare-7in5-mono`` and stops thinking
about it.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from importlib import resources

import yaml

from ..eink.palette import ColorScheme


@dataclass(frozen=True)
class PanelProfile:
    """Everything Maverick needs to know about a physical panel."""

    id: str
    name: str
    vendor: str
    width: int
    height: int
    color_scheme: ColorScheme
    dpi: int = 124
    native_rotation: int = 0
    default_transport: str = "http_pull"
    default_format: str | None = None
    measured_palette: str | None = None
    #: The `model:` ESPHome's display component knows this panel by, or None
    #: when ESPHome ships no driver for it. `scripts/check_esphome.py` runs
    #: `esphome config` over a generated configuration for every panel that
    #: names one, so a value here has been validated against ESPHome's schema.
    esphome_model: str | None = None
    #: The display platform that model belongs to. Almost every catalogued
    #: panel is a `waveshare_epaper` one; the Spectra 6 panels live under
    #: ESPHome's newer `epaper_spi` component instead.
    esphome_platform: str | None = None
    supports_partial: bool = False
    #: Do a full (flashing) refresh every N frames to clear accumulated ghosting.
    full_refresh_every: int = 0
    notes: str = ""

    @property
    def megapixels(self) -> float:
        return self.width * self.height / 1_000_000

    @property
    def is_colour(self) -> bool:
        return self.color_scheme.is_colour

    def describe(self) -> str:
        orientation = "portrait" if self.height > self.width else "landscape"
        return (
            f"{self.name} — {self.width}x{self.height} {orientation}, "
            f"{self.color_scheme.value}, ~{self.dpi} dpi"
        )


@functools.lru_cache(maxsize=1)
def _load() -> dict[str, PanelProfile]:
    raw = resources.files(__package__).joinpath("panels.yaml").read_text(encoding="utf-8")
    data = yaml.safe_load(raw) or {}
    profiles: dict[str, PanelProfile] = {}
    for entry in data.get("panels", []):
        entry = dict(entry)
        entry["color_scheme"] = ColorScheme(entry["color_scheme"])
        profile = PanelProfile(**entry)
        profiles[profile.id] = profile
    return profiles


def get_panel(panel_id: str) -> PanelProfile:
    """Look up a panel, with a helpful error listing near matches."""
    profiles = _load()
    if panel_id in profiles:
        return profiles[panel_id]
    tokens = [t for t in panel_id.replace("_", "-").split("-") if t]
    near = [p for p in profiles if any(t in p for t in tokens)]
    suggestion = f" Did you mean: {', '.join(sorted(near)[:5])}?" if near else ""
    raise KeyError(
        f"Unknown panel {panel_id!r}. Run `maverick panels` to list all "
        f"{len(profiles)} supported panels.{suggestion}"
    )


def all_panels() -> list[PanelProfile]:
    return sorted(_load().values(), key=lambda p: (p.vendor, p.id))


def panels_by_vendor() -> dict[str, list[PanelProfile]]:
    grouped: dict[str, list[PanelProfile]] = {}
    for profile in all_panels():
        grouped.setdefault(profile.vendor, []).append(profile)
    return grouped


__all__ = ["PanelProfile", "get_panel", "all_panels", "panels_by_vendor"]
