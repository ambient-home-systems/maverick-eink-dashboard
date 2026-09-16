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


@dataclass(frozen=True)
class OptionField:
    """How a form should offer one transport option.

    ``options_doc`` says what an option *means*; this says how to ask for it.
    Every key here is optional, and a transport that declares nothing gets a
    plain text box per documented option, which is what the setup UI drew for
    every transport before this existed. The reason it exists is the
    ``opendisplay`` transport: twelve options of which a user in the normal
    case needs exactly one, and which one depends on ``mode``.

    * ``modes``: the values of the transport's ``mode_option`` this option
      applies under; empty means always. The form hides the rest.
    * ``required``: the option must be set for delivery to work in those
      modes. The form marks it and the transport's ``probe`` reports it.
    * ``advanced``: right far more often than not; the form folds it away.
    * ``kind``: what control to draw. ``text`` (the default), ``select`` with
      ``choices``, ``number``, ``secret`` (masked), ``mac``, ``path``, or
      ``ha_device`` — a picker fed by Home Assistant's device registry, for an
      id nobody should have to copy out of a URL.
    * ``default``: shown as the placeholder, so an empty box says what it means.
    * ``label``: a human title; the key itself when empty.
    """

    modes: tuple[str, ...] = ()
    required: bool = False
    advanced: bool = False
    kind: str = "text"
    choices: tuple[str, ...] = ()
    default: Any = None
    label: str = ""
    #: For ``ha_device``: the integration domain whose devices to offer.
    integration: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "modes": list(self.modes),
            "required": self.required,
            "advanced": self.advanced,
            "kind": self.kind,
            "choices": list(self.choices),
            "default": self.default,
            "label": self.label,
            "integration": self.integration,
        }


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
    #: How a form asks for each option: see :class:`OptionField`. Keys not
    #: listed here are offered as plain text boxes. ``GET /api/schema/display``
    #: serves it beside ``options_doc`` (`src/maverick/server/api.py`).
    option_fields: ClassVar[dict[str, OptionField]] = {}
    #: The option whose value gates the others through ``OptionField.modes``,
    #: or empty when every option always applies.
    mode_option: ClassVar[str] = ""

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
    "Transport", "DeliveryContext", "DeliveryResult", "OptionField",
    "register", "get_transport", "available_transports",
]
