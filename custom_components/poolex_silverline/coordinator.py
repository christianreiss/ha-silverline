"""DataUpdateCoordinator for the Poolex Silverline."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from pysilverline import (
    CannotConnect,
    DeviceState,
    InvalidAuth,
    SilverlineClient,
    SilverlineError,
)

from ._faults import FaultReconciler
from ._runtime import accumulate_runtime
from .const import (
    CONF_MODEL,
    DEFAULT_SCAN_INTERVAL,
    DEVICE_PROFILES,
    DOMAIN,
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
        # Pre-populated from the model profile if the user selected a known
        # model; otherwise populated on first successful poll. Lets platforms
        # skip entities whose backing DP this firmware variant never reports.
        model_key = config_entry.data.get(CONF_MODEL, "")
        profile = DEVICE_PROFILES.get(model_key)
        if profile is not None and profile.known_dps is not None:
            self.supported_dps: frozenset[str] = frozenset(
                str(dp) for dp in profile.known_dps
            )
        else:
            self.supported_dps = frozenset()
        # Owns the fault → Repair-issue state and reconciliation logic.
        self._faults = FaultReconciler()
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

    @property
    def active_fault_codes(self) -> frozenset[str]:
        """Read-only view of the OEM codes with an open Repair issue.

        Lets diagnostics surface the current fault set without reaching into
        the reconciler — the reconcile logic stays the sole writer.
        """
        return self._faults.active_codes

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
        #
        # monotonic() is read HERE (not inside the reconciler) so the E03
        # debounce clock stays patchable as
        # coordinator.time.monotonic in tests.
        self._faults.reconcile(self.hass, state, now=time.monotonic())
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

        # utcnow() is read HERE (not inside accumulate_runtime) so the
        # runtime clock stays patchable as coordinator.dt_util.utcnow in
        # tests.
        now = dt_util.utcnow()
        action = compute_hvac_action(state)
        (
            self._runtime_today_seconds,
            self._runtime_last_tick,
            self._runtime_local_date,
        ) = accumulate_runtime(
            now=now,
            last_tick=self._runtime_last_tick,
            local_date=self._runtime_local_date,
            today_seconds=self._runtime_today_seconds,
            action=action,
        )

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
