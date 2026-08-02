"""Diagnostics support for Poolex Silverline."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

import pysilverline

from .const import CONF_DEVICE_ID, CONF_LOCAL_KEY, CONF_MODEL, DOMAIN
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
    client = coordinator.client
    data: dict[str, Any] | None = (
        asdict(coordinator.data) if coordinator.data is not None else None
    )
    # Version skew (integration vs. bundled pysilverline) is the first thing
    # a maintainer asks a reporter for, so surface both up front.
    integration = await async_get_integration(hass, DOMAIN)
    return {
        "versions": {
            "integration": str(integration.version) if integration.version else None,
            "pysilverline": pysilverline.__version__,
        },
        "entry": async_redact_data(entry.as_dict(), TO_REDACT),
        "device_info": async_redact_data(
            {
                "device_id": coordinator.device_id,
                "model": entry.data.get(CONF_MODEL),
            },
            TO_REDACT,
        ),
        "connection": async_redact_data(
            {
                "host": client.host,
                "port": client.port,
                # Live negotiated version; preferred over CONF_PROTOCOL_VERSION
                # in ``entry`` which can be unset or stale.
                "detected_version": client.detected_version,
                "connected": client.connected,
            },
            TO_REDACT,
        ),
        "coordinator": {
            # DPs this firmware variant actually emits — tells a maintainer
            # which entities the unit supports without guessing.
            "supported_dps": sorted(coordinator.supported_dps),
            "runtime_today_seconds": coordinator.runtime_today_seconds,
            "energy_consumption_kwh": coordinator.energy_consumption_kwh,
            "last_update_success": coordinator.last_update_success,
            # Failure *type* only — the exception message embeds the device
            # host (see pysilverline CannotConnect), and async_redact_data
            # scrubs by key, not by substring, so the full string would leak
            # the IP we redact everywhere else.
            "last_exception": (
                type(coordinator.last_exception).__name__
                if coordinator.last_exception is not None
                else None
            ),
            # Already-decoded OEM service codes (E03, ...) with an open Repair issue.
            "active_fault_codes": sorted(coordinator.active_fault_codes),
        },
        "state": async_redact_data(data or {}, TO_REDACT),
    }
