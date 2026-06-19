"""The Poolex Silverline integration."""

from __future__ import annotations

import asyncio
import logging
import sys
from collections import OrderedDict
from pathlib import Path

# Vendored pysilverline fork (v3.4) — not yet on PyPI.
_vendor = Path(__file__).resolve().parent / "vendor" / "pysilverline" / "src"
if _vendor.is_dir():
    sys.path.insert(0, str(_vendor))

from homeassistant.config_entries import SOURCE_INTEGRATION_DISCOVERY
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from pysilverline import SilverlineClient, discover
from pysilverline.layouts import layout_for_model

from .const import CONF_DEVICE_ID, CONF_LOCAL_KEY, CONF_MODEL, CONF_PROTOCOL_VERSION, DOMAIN
from .coordinator import SilverlineConfigEntry, SilverlineCoordinator

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS: list[Platform] = [
    Platform.CLIMATE,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SWITCH,
    Platform.NUMBER,
    Platform.SELECT,
]

_DISCOVERY_TASK_KEY = "_discovery_task"

# Cap the per-loop dedup map so a hostile LAN peer can't flood it by
# minting endless fake device_ids in spoofed broadcasts (the Tuya
# discovery key is public). 256 is well above any plausible site count.
_DISCOVERY_SEEN_MAX = 256


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
        # Track the last IP we forwarded per device_id. A repeat broadcast
        # on the same IP is suppressed (the device announces every ~25s
        # and HA does not need to be told twice). A broadcast with a new
        # IP for a known device_id is forwarded so the discovery flow can
        # rewrite CONF_HOST on the existing entry — the original code's
        # unbounded set never let any second flow fire, so DHCP-driven
        # IP changes mid-session went unpropagated until HA restart.
        # OrderedDict so we can evict the oldest entry when the cap is
        # hit. Insertion order is the eviction key — we deliberately do
        # NOT move-to-end on duplicate hits, so a flooder repeating one
        # spoofed device_id cannot pin themselves at the front and push
        # real devices out.
        seen_ips: OrderedDict[str, str] = OrderedDict()
        try:
            async for info in discover():
                if seen_ips.get(info.device_id) == info.ip:
                    continue
                seen_ips[info.device_id] = info.ip
                while len(seen_ips) > _DISCOVERY_SEEN_MAX:
                    seen_ips.popitem(last=False)
                hass.async_create_task(
                    hass.config_entries.flow.async_init(
                        DOMAIN,
                        context={"source": SOURCE_INTEGRATION_DISCOVERY},
                        data={
                            "device_id": info.device_id,
                            "ip": info.ip,
                            "version": info.version,
                            # Forwarded so the config flow can decide whether
                            # to treat this broadcast as a known Poolex device
                            # or a co-resident Tuya bulb/plug/etc. The Tuya
                            # broadcast format guarantees the field at the
                            # JSON layer; pysilverline drops it to None only
                            # if the value is missing or not a string.
                            "product_key": info.product_key,
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
        protocol_version=entry.data.get(CONF_PROTOCOL_VERSION),
        dp_layout=layout_for_model(entry.data.get(CONF_MODEL, "")),
    )
    coordinator = SilverlineCoordinator(hass, entry, client)
    # _async_setup opens the TCP socket and registers push + connection
    # listeners; the first refresh that follows can still raise (auth
    # rejected after a successful connect, for example). Without an
    # explicit shutdown on that path, the socket and the background
    # reader/heartbeat/reconnect tasks would survive the failed setup
    # — entry.runtime_data is never assigned, so async_unload_entry
    # cannot reach the coordinator to clean it up.
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        await coordinator.async_shutdown()
        raise

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SilverlineConfigEntry) -> bool:
    """Tear down a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.async_shutdown()
    return unload_ok


async def _async_reload_entry(
    hass: HomeAssistant, entry: SilverlineConfigEntry
) -> None:
    """Reload on options or data changes."""
    await hass.config_entries.async_reload(entry.entry_id)
