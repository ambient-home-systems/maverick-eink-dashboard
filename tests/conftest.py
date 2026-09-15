"""Shared fixtures.

The suite never touches a browser, a broker or a real Home Assistant: every
test here drives the public surface of one component with the rest stubbed, so
`pytest -q` runs in a bare checkout.

Two of those stubs are shared. :class:`FakeTransport` is a registered transport
that records frames instead of reaching a panel, and :class:`FakeRenderer`
stands in for the browser by handing the engine an image someone built with
Pillow. Together they let a test drive `Engine.render` end to end — lint gate,
skip-unchanged guard, delivery — with nothing but the project's own code.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

import pytest
from PIL import Image

from maverick import engine as engine_module
from maverick.config import Config, ResolvedDisplay
from maverick.engine import Engine
from maverick.render.dashboard import RenderResult
from maverick.transports import DeliveryContext, DeliveryResult, Transport, register

if TYPE_CHECKING:  # pragma: no cover
    from maverick.eink import Frame


@pytest.fixture
def config(tmp_path) -> Config:
    """A two-display config with the data directory pointed at ``tmp_path``."""
    return Config.model_validate(
        {
            "data_dir": str(tmp_path / "data"),
            "server": {"base_url": "http://maverick.local:5000"},
            "displays": [
                {
                    "id": "kitchen",
                    "name": "Kitchen",
                    "panel": "trmnl-7in5",
                    "transport": {"type": "http_pull", "mac": "AA:BB:CC:DD:EE:FF"},
                },
                {
                    "id": "hallway",
                    "name": "Hallway",
                    "panel": "trmnl-7in5",
                    "transport": {"type": "http_pull"},
                },
            ],
        }
    )


# --------------------------------------------------------------------------- #
# Test doubles
# --------------------------------------------------------------------------- #

@register
class FakeTransport(Transport):
    """Records what it was asked to deliver instead of reaching a panel.

    ``pushes`` is a class-level flag on every real transport, because a real
    transport is one kind or the other. This one is both, chosen per instance
    from its ``pushes`` option, which is what lets a single double stand on
    each side of the servable guard in ``Engine.render``.
    """

    name = "fake"
    pushes = True
    description = "Test double: records frames instead of delivering them"
    options_doc: ClassVar[dict[str, str]] = {
        "pushes": "Whether the double behaves as a push transport (the default) or a pull one.",
    }

    def __init__(self, options: dict[str, Any]) -> None:
        super().__init__(options)
        self.pushes = bool(self.option("pushes", True))
        self.deliveries: list[Frame] = []
        self.contexts: list[DeliveryContext] = []
        self.started = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.started = False

    async def deliver(self, frame: Frame, context: DeliveryContext) -> DeliveryResult:
        self.deliveries.append(frame)
        self.contexts.append(context)
        if not self.pushes:
            return DeliveryResult.awaiting_pull("recorded, awaiting collection")
        return DeliveryResult.success("recorded")


class FakeRenderer:
    """Stands in for ``DashboardRenderer``: no browser, one prepared image."""

    def __init__(self, image: Image.Image) -> None:
        self.image = image
        self.calls = 0

    async def render(self, display: ResolvedDisplay) -> RenderResult:
        self.calls += 1
        return RenderResult(
            image=self.image,
            url=f"fake://{display.id}",
            duration_s=0.0,
            viewport=(display.width, display.height),
            scale=1.0,
        )


@dataclass
class Harness:
    """An engine and the two doubles wired into it."""

    engine: Engine
    renderer: FakeRenderer
    transport: FakeTransport

    @property
    def delivered(self) -> int:
        return len(self.transport.deliveries)

    @property
    def state(self) -> Any:
        return self.engine.states["kitchen"]


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

DISPLAY_ID = "kitchen"
PANEL_SIZE = (160, 120)


@pytest.fixture
def minimal_config(tmp_path) -> Config:
    """One small mono display delivering through the ``fake`` transport."""
    width, height = PANEL_SIZE
    return Config.model_validate(
        {
            "data_dir": str(tmp_path / "data"),
            "displays": [
                {
                    "id": DISPLAY_ID,
                    "name": "Kitchen",
                    "panel": "generic-mono",
                    "width": width,
                    "height": height,
                    "frame_format": "png",
                    "transport": {"type": "fake", "pushes": True},
                }
            ],
        }
    )


@pytest.fixture
def make_engine(minimal_config: Config, monkeypatch):
    """Build a started :class:`Engine` that renders ``image`` and records delivery.

    ``Engine.start`` is the real one: it builds the transport through the
    registry, so the ``fake`` entry is exercised the way a configured transport
    would be. Only the renderer is swapped out, because the real one needs
    Chromium. Reaching into ``_transports`` afterwards is how the test gets
    hold of the instance ``start`` created.
    """

    async def _make(image: Image.Image, *, pushes: bool = True) -> Harness:
        minimal_config.display(DISPLAY_ID).transport.pushes = pushes
        renderer = FakeRenderer(image)
        monkeypatch.setattr(
            engine_module, "DashboardRenderer", lambda ha, pool, tokens=None: renderer
        )
        engine = Engine(minimal_config)
        await engine.start()
        return Harness(engine=engine, renderer=renderer, transport=engine._transports[DISPLAY_ID])

    return _make


@pytest.fixture
def blank_image() -> Image.Image:
    """What a dashboard that failed to load screenshots as: nothing at all."""
    return Image.new("RGB", PANEL_SIZE, (255, 255, 255))


@pytest.fixture
def legible_image() -> Image.Image:
    """A frame with enough ink on it to pass every lint check."""
    image = Image.new("RGB", PANEL_SIZE, (255, 255, 255))
    image.paste(Image.new("RGB", (60, 40), (0, 0, 0)), (20, 20))
    return image
