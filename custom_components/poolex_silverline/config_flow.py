"""Config and reauth/reconfigure flows for Poolex Silverline."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PORT

from .const import (
    CONF_DEVICE_ID,
    CONF_LOCAL_KEY,
    CONF_MODEL,
    CONF_PROTOCOL_VERSION,
    DEFAULT_PORT,
    DOMAIN,
)
from ._config_validation import (
    _DISCOVERY_CONFIRM_SCHEMA,
    _KNOWN_POOLEX_PRODUCT_KEYS,
    _MODEL_SCHEMA,
    _REAUTH_SCHEMA,
    _USER_SCHEMA,
    _try_validate,
    _verify_host,
)
from .util import mask_device_id

_LOGGER = logging.getLogger(__name__)


class SilverlineConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the user, reauth, reconfigure, and discovery flows."""

    VERSION = 1
    MINOR_VERSION = 3

    def __init__(self) -> None:
        super().__init__()
        self._discovery_host: str | None = None
        self._discovery_device_id: str | None = None
        # Validated connection data stashed between the credentials step and
        # the model-selection step (cleared in __init__ and reset on each new
        # credentials submission so back-navigation is safe).
        self._pending_data: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_DEVICE_ID])
            self._abort_if_unique_id_configured()
            error, version = await _try_validate(user_input)
            if error is None:
                self._pending_data = dict(user_input)
                if version is not None:
                    self._pending_data[CONF_PROTOCOL_VERSION] = version
                return await self.async_step_model()
            errors["base"] = error

        return self.async_show_form(
            step_id="user", data_schema=_USER_SCHEMA, errors=errors
        )

    async def async_step_model(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Second step: user picks their device model."""
        if user_input is not None:
            is_reconfigure = self._pending_data.pop("_reconfigure", False)
            data = {**self._pending_data, CONF_MODEL: user_input[CONF_MODEL]}
            if is_reconfigure:
                entry = self._get_reconfigure_entry()
                return self.async_update_reload_and_abort(entry, data_updates=data)
            host = data.get(CONF_HOST, "")
            return self.async_create_entry(
                title=f"Pool Heatpump ({host})",
                data=data,
            )
        suggested = self._pending_data.get(CONF_MODEL, "other")
        return self.async_show_form(
            step_id="model",
            data_schema=self.add_suggested_values_to_schema(
                _MODEL_SCHEMA, {CONF_MODEL: suggested}
            ),
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
            error, version = await _try_validate(candidate)
            if error is None:
                updates: dict[str, Any] = {CONF_LOCAL_KEY: user_input[CONF_LOCAL_KEY]}
                if version is not None:
                    updates[CONF_PROTOCOL_VERSION] = version
                return self.async_update_reload_and_abort(entry, data_updates=updates)
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
            error, version = await _try_validate(user_input)
            if error is None:
                self._pending_data = dict(user_input)
                if version is not None:
                    self._pending_data[CONF_PROTOCOL_VERSION] = version
                # Carry existing model choice as the default suggestion.
                self._pending_data.setdefault(
                    CONF_MODEL, entry.data.get(CONF_MODEL, "other")
                )
                # Mark this as a reconfigure so async_step_model can update
                # (not create) the entry.
                self._pending_data["_reconfigure"] = True
                return await self.async_step_model()
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
            if not await _verify_host(host, existing.data):
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
            error, version = await _try_validate(candidate)
            if error is None:
                if version is not None:
                    candidate[CONF_PROTOCOL_VERSION] = version
                self._pending_data = candidate
                return await self.async_step_model()
            errors["base"] = error
        return self.async_show_form(
            step_id="discovery_confirm",
            data_schema=_DISCOVERY_CONFIRM_SCHEMA,
            description_placeholders={"host": self._discovery_host},
            errors=errors,
        )
