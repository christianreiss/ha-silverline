"""Pure, flow-independent validation helpers for the config flow.

These helpers are split out of ``config_flow.py`` so the HA
``ConfigFlow`` subclass stays small.  Nothing here depends on flow
instance state; they only validate supplied credentials by opening a
short-lived encrypted session against the device.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from pysilverline import CannotConnect, InvalidAuth, SilverlineClient

from .const import (
    CONF_DEVICE_ID,
    CONF_LOCAL_KEY,
    CONF_MODEL,
    CONF_PROTOCOL_VERSION,
    DEFAULT_PORT,
    DEVICE_PROFILES,
)

_LOGGER = logging.getLogger(__name__)

# Tuya UDP discovery broadcasts are signed with a publicly known key, so any
# LAN host can spoof one to redirect us to an attacker-controlled IP. Before
# rewriting an existing entry's CONF_HOST in response to a broadcast we open
# a short-lived encrypted session against the new IP with our stored
# local_key — only a device that holds the real local_key can respond, so a
# successful get_status proves the new IP is the legitimate device.
_DISCOVERY_VERIFY_TIMEOUT = 3.0

# Tuya productKeys confirmed to correspond to Poolex / Silverline heat
# pumps (from silverline-fe-specs.md plus a live capture from a PC-SLP090N
# on 2026-05-24). The productKey identifies the OEM hardware family, not
# the marketing SKU — the PC-SLP090N broadcasts the same
# `3bhylhz5zhogklel` as the JetLine Selection FI.
#
# Filter policy:
#   * productKey present AND in this set  → continue (known Poolex device).
#   * productKey present AND not in set   → abort `unsupported_product`.
#     Prevents a discovery card from popping up for every Tuya bulb / plug
#     / camera / etc. on the LAN — they all broadcast on the same UDP port.
#   * productKey missing / None           → continue, log known=False.
#     Older Tuya firmware variants may not carry the field at all; rather
#     than lock out a legitimate device that happens to predate the format,
#     we let it through. The bulb/plug flood case always carries a key in
#     practice, so this fallback does not weaken the filter for them.
_KNOWN_POOLEX_PRODUCT_KEYS: frozenset[str] = frozenset(
    {
        "3bhylhz5zhogklel",  # Poolex JetLine Selection FI + PC-SLP090N (shared)
        "wgpg4qdqg8dd3xtx",  # Brustec BR-80
        "qrlLaHWwIsZsV31f",  # Phalén Calidi XP
        "bf911310efade7bc43mzsm",  # Nulite (house-heating sibling)
        "wfzeiyn1ed3axxde",  # Poolex Silverline (Tuya v3.4 firmware, 2026) — @olomouckyorel
    }
)

# The local_key is a long-lived shared secret used to encrypt every frame
# exchanged with the device. Render it as a password field so HA masks it in
# the UI (and in screenshots/screen-shares of the setup dialog).
_LOCAL_KEY_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))

_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): cv.string,
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): cv.port,
        vol.Required(CONF_DEVICE_ID): cv.string,
        vol.Required(CONF_LOCAL_KEY): _LOCAL_KEY_SELECTOR,
    }
)

_REAUTH_SCHEMA = vol.Schema({vol.Required(CONF_LOCAL_KEY): _LOCAL_KEY_SELECTOR})

_DISCOVERY_CONFIRM_SCHEMA = vol.Schema(
    {vol.Required(CONF_LOCAL_KEY): _LOCAL_KEY_SELECTOR}
)

_MODEL_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_MODEL, default="other"): SelectSelector(
            SelectSelectorConfig(
                options=[
                    SelectOptionDict(value=k, label=v.display_name)
                    for k, v in DEVICE_PROFILES.items()
                ],
                mode=SelectSelectorMode.DROPDOWN,
            )
        )
    }
)


async def _validate(data: Mapping[str, Any]) -> str | None:
    """Open a connection with the supplied credentials and pull status once.

    Returns the detected protocol version (``"3.3"``, ``"3.4"`` or ``"3.5"``)
    on success.  Raises CannotConnect or InvalidAuth on failure.  Always closes
    the socket before returning.
    """
    client = SilverlineClient(
        host=data[CONF_HOST],
        port=data.get(CONF_PORT, DEFAULT_PORT),
        device_id=data[CONF_DEVICE_ID],
        local_key=data[CONF_LOCAL_KEY],
        protocol_version=data.get(CONF_PROTOCOL_VERSION),
    )
    try:
        await client.connect()
        await client.get_status()
        return client.detected_version
    finally:
        await client.disconnect()


async def _verify_host(host: str, entry_data: Mapping[str, Any]) -> bool:
    """Attempt a short encrypted handshake against ``host`` using the
    existing entry's credentials.

    Returns True iff ``connect()`` + ``get_status()`` both succeed
    within the discovery verify timeout — proof the responder holds
    our local_key and is therefore the genuine device, not a LAN
    attacker that minted a spoofed UDP broadcast.
    """
    client = SilverlineClient(
        host=host,
        port=entry_data.get(CONF_PORT, DEFAULT_PORT),
        device_id=entry_data[CONF_DEVICE_ID],
        local_key=entry_data[CONF_LOCAL_KEY],
        request_timeout=_DISCOVERY_VERIFY_TIMEOUT,
    )
    try:
        await client.connect()
        await client.get_status()
    except (CannotConnect, InvalidAuth, ValueError):
        return False
    except Exception:  # noqa: BLE001
        _LOGGER.exception("Unexpected error verifying discovered host")
        return False
    finally:
        await client.disconnect()
    return True


async def _try_validate(
    data: Mapping[str, Any],
) -> tuple[str | None, str | None]:
    """Run _validate and translate errors to error keys.

    Returns ``(error_key, protocol_version)``.  On success, error_key is
    None and protocol_version holds the detected value.  On failure,
    error_key is set and protocol_version is None.
    """
    try:
        version = await _validate(data)
    except CannotConnect:
        return "cannot_connect", None
    except InvalidAuth:
        return "invalid_auth", None
    except ValueError:
        return "invalid_auth", None
    except Exception:  # noqa: BLE001
        _LOGGER.exception("Unexpected error during validation")
        return "unknown", None
    return None, version
