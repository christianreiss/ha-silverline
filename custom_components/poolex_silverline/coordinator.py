"""DataUpdateCoordinator for the Poolex Silverline."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Final

from homeassistant.components.climate.const import HVACAction
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from pysilverline import (
    CannotConnect,
    DeviceState,
    InvalidAuth,
    SilverlineClient,
    SilverlineError,
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
    "https://github.com/christianreiss/ha-silverline#troubleshooting"
)

_LOGGER = logging.getLogger(__name__)

type SilverlineConfigEntry = ConfigEntry[SilverlineCoordinator]


class SilverlineCoordinator(DataUpdateCoordinator[DeviceState]):
    """Coordinates polling and push updates from one heat pump."""

    config_entry: SilverlineConfigEntry

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
        self.device_id: str = client.device_id
        self._unsub_push: Callable[[], None] | None = None
        self._unsub_connection: Callable[[], None] | None = None
        # Set on first successful poll. Lets platforms skip entities whose
        # backing DP this firmware variant never reports.
        self.supported_dps: frozenset[str] = frozenset()
        # Tracks which fault codes currently have an open Repair issue so
        # we only fire create/delete when the bit actually flips.
        self._active_fault_issues: set[str] = set()
        # Runtime-today accumulator state — see _tick_runtime. Stored on
        # the coordinator (not the sensor) so it survives entity reloads
        # and is reachable from diagnostics without entity lookups.
        self._runtime_today_seconds: float = 0.0
        self._runtime_last_tick: datetime | None = None
        self._runtime_local_date: date | None = None

    @property
    def runtime_today_seconds(self) -> float:
        """Read-only accessor for the today's-runtime accumulator.

        Exists so sensor/diagnostics callers don't have to reach into
        ``_runtime_today_seconds`` directly. The setter side stays
        internal — only ``_tick_runtime`` may mutate it.
        """
        return self._runtime_today_seconds

    async def _async_setup(self) -> None:
        try:
            await self.client.connect()
        except CannotConnect as err:
            raise UpdateFailed(f"connect failed: {err}") from err
        self._unsub_push = self.client.add_listener(self._handle_push)
        self._unsub_connection = self.client.add_connection_listener(
            self._handle_connection_change
        )

    async def _async_update_data(self) -> DeviceState:
        try:
            state = await self.client.get_status()
        except InvalidAuth as err:
            raise ConfigEntryAuthFailed(err) from err
        except CannotConnect as err:
            raise UpdateFailed(f"poll failed: {err}") from err
        except SilverlineError as err:
            # Device-side rejection (non-zero retcode that isn't auth). The
            # socket is healthy; the firmware refused the query for some
            # other reason — surface as UpdateFailed so HA keeps the entry
            # loaded and retries on the next tick.
            raise UpdateFailed(f"poll rejected: {err}") from err
        # Snapshot the DPs the firmware actually emits, once. Platforms
        # read this in their async_setup_entry to skip entities that would
        # otherwise spend their whole lifetime `unavailable`.
        if not self.supported_dps:
            self.supported_dps = frozenset(state.raw.keys())
        # The DataUpdateCoordinator base assigns the return value to
        # self.data directly without going through async_set_updated_data,
        # so the poll path needs to invoke the side effects itself.
        self._process_state(state)
        return state

    @callback
    def _handle_push(self, state: DeviceState) -> None:
        self.async_set_updated_data(state)

    @callback
    def async_set_updated_data(self, data: DeviceState) -> None:
        self._process_state(data)
        super().async_set_updated_data(data)

    @callback
    def _process_state(self, state: DeviceState) -> None:
        # Single chokepoint for every fresh state, push or poll. Keeps the
        # issue registry and runtime accumulator consistent regardless of
        # which path delivered the state.
        self._reconcile_fault_issues(state)
        self._tick_runtime(state)

    @callback
    def _tick_runtime(self, state: DeviceState) -> None:
        """Accumulate seconds while hvac_action is HEATING or COOLING.

        The accumulator resets to 0 at local midnight (so today's value
        reflects exactly today, not a rolling 24h). Each tick measures
        the gap since the previous tick — push-driven, so the granularity
        is the device push rate. A polite under-count is preferred over
        the alternative (sampling at midnight crossing and double-billing
        across the boundary), so the first tick after a midnight reset
        only starts the new day's clock.
        """
        # Imported lazily to avoid a circular-import path
        # (coordinator → util → climate.const is fine; this just keeps
        # the runtime accumulator self-contained).
        from .util import compute_hvac_action

        now = dt_util.utcnow()
        local_today = dt_util.as_local(now).date()

        if self._runtime_last_tick is None:
            # First observation: just anchor the clock; can't accumulate
            # an interval without a prior timestamp.
            self._runtime_last_tick = now
            self._runtime_local_date = local_today
            return

        if self._runtime_local_date != local_today:
            # Day boundary crossed since the last tick. Zero the counter
            # and re-anchor — under-counts the few seconds between the
            # last pre-midnight tick and the actual midnight instant, but
            # avoids attributing any of that time to "today".
            self._runtime_today_seconds = 0.0
            self._runtime_local_date = local_today
            self._runtime_last_tick = now
            return

        action = compute_hvac_action(state)
        if action in (HVACAction.HEATING, HVACAction.COOLING):
            delta = (now - self._runtime_last_tick).total_seconds()
            if delta > 0:
                self._runtime_today_seconds += delta
        self._runtime_last_tick = now

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
            # Bind to the config entry so the refresh is cancelled on unload —
            # without this, a recovery callback that fires between unload and
            # platform teardown would run async_request_refresh against a
            # half-torn-down coordinator.
            self.config_entry.async_create_task(
                self.hass,
                self.async_request_refresh(),
                name=f"{DOMAIN}_refresh_on_reconnect",
            )
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
