"""Async client for Poolex Silverline / Tuya pool heat pumps (v3.3, v3.4, v3.5)."""

from __future__ import annotations

from . import const
from .client import SilverlineClient
from .discovery import DiscoveryInfo, discover, discover_once
from .exceptions import (
    CannotConnect,
    InvalidAuth,
    ProtocolError,
    SilverlineError,
)
from .models import DeviceState

__all__ = [
    "CannotConnect",
    "DeviceState",
    "DiscoveryInfo",
    "InvalidAuth",
    "ProtocolError",
    "SilverlineClient",
    "SilverlineError",
    "const",
    "discover",
    "discover_once",
]

__version__ = "0.4.3"
