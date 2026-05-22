"""Async client for Poolex Silverline / Tuya v3.3 pool heat pumps."""

from __future__ import annotations

from . import const
from .client import SilverlineClient
from .discovery import DiscoveryInfo, discover, discover_once
from .exceptions import (
    CannotConnect,
    DeviceLocked,
    InvalidAuth,
    ProtocolError,
    SilverlineError,
)
from .models import DeviceInfo, DeviceState

__all__ = [
    "CannotConnect",
    "DeviceInfo",
    "DeviceLocked",
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

__version__ = "0.2.0"
