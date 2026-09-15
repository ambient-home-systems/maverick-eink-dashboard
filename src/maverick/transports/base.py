"""Transport interface and registry.

A transport takes a finished frame and gets it onto a panel. The split between
*push* and *pull* matters more than it looks:

* **Push** transports (OpenDisplay BLE, MQTT) reach out to a device that is
  listening. Delivery succeeds or fails now, and the caller learns which.
* **Pull** transports (HTTP) cannot deliver anything. A battery ESP32 sleeps
  for ten minutes, wakes, fetches and sleeps again. The transport's job is to
  publish the frame and let the device collect it, so "delivered" means
  "available", and the real confirmation arrives later as a fetch.

Conflating the two produces a scheduler that thinks it has updated a panel that
is still asleep, so ``DeliveryResult.pending`` exists to say "published, not yet
collected".
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:  # pragma: no cover
    from ..config import Config, ResolvedDisplay
    from ..eink import Frame

log = logging.getLogger(__name__)


@dataclass
class DeliveryContext:
    """Everything a transport may need beyond the frame itself."""

    display: ResolvedDisplay
    config: Config
    #: True when the scheduler wants a flashing full refresh to clear ghosting.
    full_refresh: bool = False
    #: Sequence number of this frame for the display, from 0.
    sequence: int = 0
    #: Set when the render was triggered by something other than the schedule.
    trigger: str = "schedule"
    services: dict[str, Any] = field(default_factory=dict)


@dataclass
class DeliveryResult:
    ok: bool
    detail: str = ""
    #: Published but not yet collected — normal for pull transports.
    pending: bool = False
    bytes_sent: int = 0
    duration_s: float = 0.0
    at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def success(cls, detail: str = "", **kwargs: Any) -> DeliveryResult:
        return cls(ok=True, detail=detail, **kwargs)

    @classmethod
    def awaiting_pull(cls, detail: str = "", **kwargs: Any) -> DeliveryResult:
        return cls(ok=True, pending=True, detail=detail, **kwargs)

    @classmethod
    def failure(cls, detail: str, **kwargs: Any) -> DeliveryResult:
        return cls(ok=False, detail=detail, **kwargs)


class Transport(ABC):
    """Base class for everything that can put a frame on a panel."""

    #: Registry key, matched against ``transport.type`` in config.
    name: ClassVar[str] = ""
    #: False for transports that publish and wait to be collected.
    pushes: ClassVar[bool] = True
    #: Human-readable, shown by ``maverick transports``.
    description: ClassVar[str] = ""
    #: Option name -> description, for every key this transport reads from the
    #: ``transport:`` section. ``TransportConfig`` allows extra keys, so this is
    #: the only description of them there is; scripts/gen_docs.py turns it into
    #: the reference page and fails if a ``self.option("x")`` call is missing
    #: from it.
    options_doc: ClassVar[dict[str, str]] = {}

    def __init__(self, options: dict[str, Any]) -> None:
        self.options = options

    @abstractmethod
    async def deliver(self, frame: Frame, context: DeliveryContext) -> DeliveryResult:
        """Get ``frame`` onto the panel, or publish it for collection."""

    # start/stop are optional hooks, not part of the contract a subclass has to
    # implement: most transports hold nothing that needs opening, so the empty
    # body is the default, not an oversight.
    async def start(self) -> None:  # noqa: B027
        """Open long-lived resources. Called once at startup."""

    async def stop(self) -> None:  # noqa: B027
        """Release resources. Called once at shutdown."""

    async def probe(self, context: DeliveryContext) -> DeliveryResult:
        """Check the device is reachable without sending a frame."""
        return DeliveryResult.success("probe not implemented for this transport")

    def option(self, key: str, default: Any = None, required: bool = False) -> Any:
        value = self.options.get(key, default)
        if required and value in (None, ""):
            raise ValueError(
                f"transport '{self.name}' requires the '{key}' option to be set"
            )
        return value


_REGISTRY: dict[str, type[Transport]] = {}


def register(cls: type[Transport]) -> type[Transport]:
    """Class decorator adding a transport to the registry."""
    if not cls.name:
        raise ValueError(f"{cls.__name__} must define a name")
    _REGISTRY[cls.name] = cls
    return cls


def get_transport(name: str, options: dict[str, Any] | None = None) -> Transport:
    if name not in _REGISTRY:
        known = ", ".join(sorted(_REGISTRY)) or "none registered"
        raise KeyError(f"Unknown transport {name!r}. Available: {known}")
    return _REGISTRY[name](options or {})


def available_transports() -> dict[str, type[Transport]]:
    return dict(_REGISTRY)


__all__ = [
    "Transport", "DeliveryContext", "DeliveryResult",
    "register", "get_transport", "available_transports",
]
