"""The Poolex Silverline integration."""

from __future__ import annotations

import asyncio
import logging

from homeassistant.config_entries import SOURCE_INTEGRATION_DISCOVERY
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType

from pysilverline import SilverlineClient, discover

from .const import CONF_DEVICE_ID, CONF_LOCAL_KEY, DOMAIN
from .coordinator import SilverlineConfigEntry, SilverlineCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.CLIMATE,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SWITCH,
]

_DISCOVERY_TASK_KEY = "_discovery_task"


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Start the background UDP discovery listener once per HA process.

    Every Tuya device broadcasts a JSON announcement on UDP/6667 every
    ~25s. For each new ``device_id`` we see, fire an
    ``integration_discovery`` flow so HA shows a "Discovered" card.
    Already-configured devices' discovery handler aborts with
    ``already_configured`` after pushing any new IP into the existing
    entry — covers the Gold ``discovery-update-info`` rule for free.
    """
    if DOMAIN in hass.data and _DISCOVERY_TASK_KEY in hass.data[DOMAIN]:
        return True
    hass.data.setdefault(DOMAIN, {})

    async def _discovery_loop() -> None:
        seen_recent: set[str] = set()
        try:
            async for info in discover():
                if info.device_id in seen_recent:
                    # The same device broadcasts every ~25s; don't spam
                    # the flow system. Existing-entry IP updates still
                    # flow because async_step_integration_discovery
                    # delegates to _abort_if_unique_id_configured.
                    continue
                seen_recent.add(info.device_id)
                hass.async_create_task(
                    hass.config_entries.flow.async_init(
                        DOMAIN,
                        context={"source": SOURCE_INTEGRATION_DISCOVERY},
                        data={
                            "device_id": info.device_id,
                            "ip": info.ip,
                            "version": info.version,
                        },
                    )
                )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            _LOGGER.exception("discovery listener crashed")

    task = hass.async_create_background_task(
        _discovery_loop(), name="poolex_silverline_discovery"
    )
    hass.data[DOMAIN][_DISCOVERY_TASK_KEY] = task
    return True


async def async_setup_entry(hass: HomeAssistant, entry: SilverlineConfigEntry) -> bool:
    """Set up Poolex Silverline from a config entry."""
    client = SilverlineClient(
        host=entry.data[CONF_HOST],
        port=entry.data[CONF_PORT],
        device_id=entry.data[CONF_DEVICE_ID],
        local_key=entry.data[CONF_LOCAL_KEY],
    )
    coordinator = SilverlineCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SilverlineConfigEntry) -> bool:
    """Tear down a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.async_shutdown()
    return unload_ok


async def _async_reload_entry(hass: HomeAssistant, entry: SilverlineConfigEntry) -> None:
    """Reload on options or data changes."""
    await hass.config_entries.async_reload(entry.entry_id)
