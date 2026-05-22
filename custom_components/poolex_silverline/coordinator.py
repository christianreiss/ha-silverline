"""DataUpdateCoordinator for the Poolex Silverline."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import timedelta
from typing import Final

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from pysilverline import (
    CannotConnect,
    DeviceInfo,
    DeviceState,
    InvalidAuth,
    SilverlineClient,
    const as tuya_const,
)

from .const import DEFAULT_SCAN_INTERVAL, DOMAIN

# Fault-bit severity for Repair issues. Operational faults (water flow,
# antifreeze, pressure) need user attention now; sensor and comms faults
# are warnings — annoying but the unit usually recovers on its own.
_FAULT_SEVERITY: Final[dict[str, ir.IssueSeverity]] = {
    "E03": ir.IssueSeverity.ERROR,
    "E04": ir.IssueSeverity.ERROR,
    "E05": ir.IssueSeverity.ERROR,
    "E06": ir.IssueSeverity.ERROR,
    "E09": ir.IssueSeverity.WARNING,
    "E10": ir.IssueSeverity.WARNING,
    "P1": ir.IssueSeverity.WARNING,
    "P3": ir.IssueSeverity.WARNING,
    "P4": ir.IssueSeverity.WARNING,
    "P7": ir.IssueSeverity.WARNING,
}
_LEARN_MORE_URL: Final = (
    "https://github.com/christian-reiss/ha-silverline#troubleshooting"
)

_LOGGER = logging.getLogger(__name__)

type SilverlineConfigEntry = ConfigEntry[SilverlineCoordinator]


class SilverlineCoordinator(DataUpdateCoordinator[DeviceState]):
    """Coordinates polling and push updates from one heat pump."""

    config_entry: SilverlineConfigEntry
    device_info: DeviceInfo

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: SilverlineConfigEntry,
        client: SilverlineClient,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
            always_update=False,
        )
        self.client = client
        self._unsub_push: Callable[[], None] | None = None
        self._unsub_connection: Callable[[], None] | None = None
        # Set on first successful poll. Lets platforms skip entities whose
        # backing DP this firmware variant never reports.
        self.supported_dps: frozenset[str] = frozenset()
        # Tracks which fault codes currently have an open Repair issue so
        # we only fire create/delete when the bit actually flips.
        self._active_fault_issues: set[str] = set()

    async def _async_setup(self) -> None:
        try:
            await self.client.connect()
        except CannotConnect as err:
            raise UpdateFailed(f"connect failed: {err}") from err
        self._unsub_push = self.client.add_listener(self._handle_push)
        self._unsub_connection = self.client.add_connection_listener(
            self._handle_connection_change
        )
        self.device_info = await self.client.get_device_info()

    async def _async_update_data(self) -> DeviceState:
        try:
            state = await self.client.get_status()
        except InvalidAuth as err:
            raise ConfigEntryAuthFailed(err) from err
        except CannotConnect as err:
            raise UpdateFailed(f"poll failed: {err}") from err
        # Snapshot the DPs the firmware actually emits, once. Platforms
        # read this in their async_setup_entry to skip entities that would
        # otherwise spend their whole lifetime `unavailable`.
        if not self.supported_dps:
            self.supported_dps = frozenset(state.raw.keys())
        # Note: reconcile also runs via async_set_updated_data, but that's
        # only invoked AFTER _async_update_data returns. Calling it here
        # too so the first poll's state immediately reflects in issues.
        self._reconcile_fault_issues(state)
        return state

    @callback
    def _handle_push(self, state: DeviceState) -> None:
        self.async_set_updated_data(state)

    @callback
    def async_set_updated_data(self, data: DeviceState) -> None:
        # Reconcile Repair issues before notifying entity listeners so the
        # issue registry is consistent with what entities are about to render.
        self._reconcile_fault_issues(data)
        super().async_set_updated_data(data)

    @callback
    def _reconcile_fault_issues(self, state: DeviceState) -> None:
        """Create / delete HA Repair issues to match the fault bitmap.

        Fault DP 13 is a 30-bit field; each set bit maps to a code in
        pysilverline.const.FAULT_BIT_NAMES. We open one Repair issue per
        active code and close it the moment the device clears the bit —
        the user gets a transient, self-clearing notification stream
        without having to dismiss each one manually.
        """
        active: set[str] = set()
        fault = state.fault
        if isinstance(fault, int) and fault != 0:
            for bit, name in tuya_const.FAULT_BIT_NAMES.items():
                if fault & (1 << bit):
                    active.add(name)
        for cleared in self._active_fault_issues - active:
            ir.async_delete_issue(self.hass, DOMAIN, f"fault_{cleared}")
        for raised in active - self._active_fault_issues:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                f"fault_{raised}",
                is_fixable=False,
                is_persistent=False,
                severity=_FAULT_SEVERITY.get(raised, ir.IssueSeverity.WARNING),
                translation_key=f"fault_{raised}",
                learn_more_url=_LEARN_MORE_URL,
            )
        self._active_fault_issues = active

    @callback
    def _handle_connection_change(self, connected: bool) -> None:
        # When the socket drops, mark the last update as failed so entities
        # surface `unavailable`. On recovery, request a fresh refresh so the
        # state caught between the drop and the next 30s poll lands fast.
        if connected:
            _LOGGER.info("connection to %s restored", self.client.host)
            self.hass.async_create_task(self.async_request_refresh())
        else:
            _LOGGER.warning(
                "connection to %s lost; entities will go unavailable",
                self.client.host,
            )
            self.last_update_success = False
            self.async_update_listeners()

    async def async_shutdown(self) -> None:
        if self._unsub_push is not None:
            self._unsub_push()
            self._unsub_push = None
        if self._unsub_connection is not None:
            self._unsub_connection()
            self._unsub_connection = None
        await self.client.disconnect()
        await super().async_shutdown()
