"""Transports: how a finished frame reaches a panel."""

from .base import (
    DeliveryContext,
    DeliveryResult,
    Transport,
    available_transports,
    get_transport,
    register,
)

# Importing for the side effect of registering each transport.
from .mqtt import MqttPublisher, MqttTransport, availability_topic  # noqa: F401
from .opendisplay import OpenDisplayTransport  # noqa: F401
from .pull import FileTransport, HttpPullTransport, WebhookTransport  # noqa: F401

__all__ = [
    "Transport", "DeliveryContext", "DeliveryResult", "register", "get_transport",
    "available_transports", "MqttPublisher", "availability_topic",
]
