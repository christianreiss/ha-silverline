"""Config and reauth/reconfigure flows for Poolex Silverline."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from pysilverline import CannotConnect, InvalidAuth, SilverlineClient

from .const import CONF_DEVICE_ID, CONF_LOCAL_KEY, DEFAULT_PORT, DOMAIN
from .util import mask_device_id

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


async def _validate(data: Mapping[str, Any]) -> None:
    """Open a connection with the supplied credentials and pull status once.

    Raises CannotConnect or InvalidAuth on failure; returns silently on
    success. Always closes the socket before returning.
    """
    client = SilverlineClient(
        host=data[CONF_HOST],
        port=data.get(CONF_PORT, DEFAULT_PORT),
        device_id=data[CONF_DEVICE_ID],
        local_key=data[CONF_LOCAL_KEY],
    )
    try:
        await client.connect()
        await client.get_status()
    finally:
        await client.disconnect()


class SilverlineConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the user, reauth, reconfigure, and discovery flows."""

    VERSION = 1
    MINOR_VERSION = 1

    def __init__(self) -> None:
        super().__init__()
        self._discovery_host: str | None = None
        self._discovery_device_id: str | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_DEVICE_ID])
            self._abort_if_unique_id_configured()
            error = await self._try_validate(user_input)
            if error is None:
                return self.async_create_entry(
                    title=f"Pool Heatpump ({user_input[CONF_HOST]})",
                    data=user_input,
                )
            errors["base"] = error

        return self.async_show_form(
            step_id="user", data_schema=_USER_SCHEMA, errors=errors
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()

        if user_input is not None:
            candidate = {**entry.data, CONF_LOCAL_KEY: user_input[CONF_LOCAL_KEY]}
            error = await self._try_validate(candidate)
            if error is None:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_LOCAL_KEY: user_input[CONF_LOCAL_KEY]}
                )
            errors["base"] = error

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_REAUTH_SCHEMA,
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reconfigure_entry()

        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_DEVICE_ID])
            self._abort_if_unique_id_mismatch(reason="device_id_mismatch")
            error = await self._try_validate(user_input)
            if error is None:
                return self.async_update_reload_and_abort(
                    entry, data_updates=user_input
                )
            errors["base"] = error

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(_USER_SCHEMA, entry.data),
            errors=errors,
        )

    async def async_step_discovery(
        self, discovery_info: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Hassfest's discovery quality_scale validator only recognises
        a fixed set of step names (async_step_discovery / _zeroconf /
        _dhcp / _ssdp / etc.); async_step_integration_discovery is not
        on that list even though SOURCE_INTEGRATION_DISCOVERY routes to
        it at runtime. Delegate here so the static check sees a
        recognised step name without changing the actual flow source.
        """
        return await self.async_step_integration_discovery(discovery_info)

    async def async_step_integration_discovery(
        self, discovery_info: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Triggered by the background UDP listener when a Tuya broadcast
        names a device on the LAN.

        discovery_info carries ``device_id`` and ``ip`` straight from
        the Tuya broadcast JSON. Two cases:

        * Brand-new device → ask the user for the local_key and create
          the entry (host + device_id come from the broadcast).
        * Already-configured device announcing a new IP → satisfies Gold
          ``discovery-update-info`` by rewriting CONF_HOST in place. But
          because the Tuya broadcast key is publicly known, we cannot
          trust the announced IP blindly; we first verify the new host
          actually answers our stored local_key (see ``_verify_host``).
        """
        device_id = discovery_info["device_id"]
        host = discovery_info["ip"]
        product_key = discovery_info.get("product_key")

        # Reject co-resident Tuya devices (bulbs, plugs, cameras, …) before
        # anyone sees a "Pool Heatpump" discovery card for them. Skip the
        # check when productKey is missing entirely — older firmware may
        # not broadcast the field, and the bulb/plug flood always carries
        # one in practice.
        if product_key is not None and product_key not in _KNOWN_POOLEX_PRODUCT_KEYS:
            _LOGGER.info(
                "Silverline discovery: ignoring non-Poolex Tuya device"
                " device=%s host=%s productKey=%s",
                mask_device_id(device_id),
                host,
                product_key,
            )
            _LOGGER.debug("Silverline discovery (full device_id): %s", device_id)
            return self.async_abort(reason="unsupported_product")

        await self.async_set_unique_id(device_id)

        existing = self.hass.config_entries.async_entry_for_domain_unique_id(
            self.handler, device_id
        )
        if existing is not None:
            if existing.data.get(CONF_HOST) == host:
                # Same IP we already have → nothing to do.
                return self.async_abort(reason="already_configured")
            # New IP — only rewrite if a quick encrypted handshake with
            # our stored local_key succeeds at that IP. This stops a LAN
            # attacker who minted a spoofed broadcast (the Tuya UDP key
            # is public) from rerouting our encrypted traffic to them.
            if not await self._verify_host(host, existing.data):
                _LOGGER.warning(
                    "Ignoring discovery for %s at %s: host did not"
                    " authenticate with the stored local_key",
                    mask_device_id(device_id),
                    host,
                )
                _LOGGER.debug(
                    "Unverified discovery host (full device_id): %s", device_id
                )
                return self.async_abort(reason="unverified_host")
            self._abort_if_unique_id_configured(updates={CONF_HOST: host})

        # Brand-new device path. Log the productKey so operators can tell
        # at a glance whether the broadcast is a known Poolex heat pump or
        # some other Tuya device on the LAN that happened to broadcast at
        # the same time. Permissive by design — see _KNOWN_POOLEX_PRODUCT_KEYS.
        product_key = discovery_info.get("product_key")
        _LOGGER.info(
            "Silverline discovery: device=%s host=%s productKey=%s known=%s",
            mask_device_id(device_id),
            host,
            product_key,
            product_key in _KNOWN_POOLEX_PRODUCT_KEYS if product_key else False,
        )
        _LOGGER.debug("Silverline discovery (full device_id): %s", device_id)

        self._discovery_host = host
        self._discovery_device_id = device_id
        self.context["title_placeholders"] = {"name": f"Pool Heatpump ({host})"}
        return await self.async_step_discovery_confirm()

    @staticmethod
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

    async def async_step_discovery_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Second step of the discovery flow: ask for the local_key only,
        validate, and create the entry."""
        assert self._discovery_host is not None
        assert self._discovery_device_id is not None
        errors: dict[str, str] = {}
        if user_input is not None:
            candidate = {
                CONF_HOST: self._discovery_host,
                CONF_PORT: DEFAULT_PORT,
                CONF_DEVICE_ID: self._discovery_device_id,
                CONF_LOCAL_KEY: user_input[CONF_LOCAL_KEY],
            }
            error = await self._try_validate(candidate)
            if error is None:
                return self.async_create_entry(
                    title=f"Pool Heatpump ({self._discovery_host})",
                    data=candidate,
                )
            errors["base"] = error
        return self.async_show_form(
            step_id="discovery_confirm",
            data_schema=_DISCOVERY_CONFIRM_SCHEMA,
            description_placeholders={"host": self._discovery_host},
            errors=errors,
        )

    @staticmethod
    async def _try_validate(data: Mapping[str, Any]) -> str | None:
        """Run _validate and translate errors to error keys.

        Returns ``None`` on success, or a translation key on failure.
        """
        try:
            await _validate(data)
        except CannotConnect:
            return "cannot_connect"
        except InvalidAuth:
            return "invalid_auth"
        except ValueError:
            # local_key length / format issue (must be 16 ASCII bytes)
            return "invalid_auth"
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Unexpected error during validation")
            return "unknown"
        return None
