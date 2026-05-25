"""Diagnostics support for Poolex Silverline."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant

from .const import CONF_DEVICE_ID, CONF_LOCAL_KEY
from .coordinator import SilverlineConfigEntry

TO_REDACT: set[str] = {
    CONF_DEVICE_ID,
    CONF_HOST,
    CONF_LOCAL_KEY,
    "device_id",
    "entry_id",
    "host",
    "ip",
    "local_key",
    "serial_number",
    "title",
    "unique_id",
}
# Note: ``raw`` is intentionally NOT redacted. It carries the full DP map
# from the wire (temps, modes, fault bits — no secrets), and is the only
# place an *unmapped* DP shows up. Hiding it would defeat the main reason
# a contributor would ask a user for a diagnostics dump when adding support
# for a new firmware variant.


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SilverlineConfigEntry
) -> dict[str, Any]:
    coordinator = entry.runtime_data
    data: dict[str, Any] | None = (
        asdict(coordinator.data) if coordinator.data is not None else None
    )
    return {
        "entry": async_redact_data(entry.as_dict(), TO_REDACT),
        "device_info": async_redact_data(
            {"device_id": coordinator.device_id}, TO_REDACT
        ),
        "state": async_redact_data(data or {}, TO_REDACT),
    }
