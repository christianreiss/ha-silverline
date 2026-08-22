"""Shared base entity for Poolex Silverline platforms."""

from __future__ import annotations

import asyncio

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from pysilverline import InvalidAuth, SilverlineError
from pysilverline import const as tuya_const

from .const import (
    CONF_MODEL,
    DEVICE_PROFILES,
    DOMAIN,
    MANUFACTURER,
    MODE_TRANSITION_SETTLE,
    MODEL,
)
from .coordinator import SilverlineCoordinator


class SilverlineEntity(CoordinatorEntity[SilverlineCoordinator]):
    """Base entity that wires up DeviceInfo from coordinator state."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: SilverlineCoordinator) -> None:
        super().__init__(coordinator)
        device_id = coordinator.device_id
        model_key = coordinator.config_entry.data.get(CONF_MODEL, "")
        profile = DEVICE_PROFILES.get(model_key)
        model_name = profile.display_name if profile is not None else MODEL
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            manufacturer=MANUFACTURER,
            model=model_name,
            name="Pool Heatpump",
            serial_number=device_id,
        )

    async def _write_dps(self, dps: dict[int, bool | int | str]) -> None:
        """Write one or more DPs, translating wire errors to HA errors.

        Shared by every write-capable platform (climate, select, switch,
        number). On success, the optimistic merge pushes the new values
        into the coordinator so entities reflect the change immediately —
        the device's STATUS push within ~200 ms overlays the authoritative
        state on top.
        """
        try:
            await self.coordinator.client.set_multiple(dps)
        except InvalidAuth as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="auth_failed",
            ) from err
        # Catch-all for the remaining wire errors — CannotConnect, a
        # device-side write rejection (non-zero CONTROL ack, a bare
        # SilverlineError), and ProtocolError all subclass SilverlineError.
        # Must stay AFTER the InvalidAuth clause, which does too.
        except SilverlineError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="set_failed",
                translation_placeholders={"reason": str(err)},
            ) from err
        if self.coordinator.data is not None:
            merged = self.coordinator.data.merge(
                {str(k): v for k, v in dps.items()},
                layout=self.coordinator.client.dp_layout,
            )
            self.coordinator.async_set_updated_data(merged)

    async def _write_mode(self, mode_string: str) -> None:
        """Write the DP-4 operating mode, powering the unit on first if off.

        Never bundles DP 1 and DP 4 into one frame. Full Inverter firmware
        acknowledges such a frame and then reverts to off with no HA write
        in between — first seen in issue #7 (device confirms 1=true, pushes
        1=false ~7 s later) and reproduced step by step in issue #19, where
        selecting a mode on a powered-off unit left HA showing ON, the
        vendor app showing OFF, and the unit switching itself back off a few
        minutes later. ``climate.async_turn_on`` has sent power alone since
        issue #7 for exactly this reason; the two mode-change paths kept
        bundling, which is why the bug survived there.

        When the unit is already running, DP 1 is not written at all — the
        Tuya app doesn't either, and a redundant power write is one more
        chance for the firmware to take exception.

        The cost of splitting: two writes are two failure points. A power
        write that succeeds followed by a mode write that fails now leaves
        the unit ON in its previous mode, where a rejected bundle left it
        OFF. Both surface the same HomeAssistantError.

        The settle gap is also a window a poll can land in, which became a
        real possibility only once pushes stopped starving the poll. The
        state it publishes there — powered on, still in the previous mode —
        is true rather than torn, and it cannot affect this write: callers
        resolve ``mode_string`` before calling, and each ``_write_dps``
        awaits its own acknowledgement. climate's ``_last_preset`` may latch
        the retained preset from that interim state, which is the same value
        both write paths deliberately carry forward anyway.
        """
        state = self.coordinator.data
        if state is None or not state.power:
            await self._write_dps({tuya_const.DP_POWER: True})
            # Let the power-on settle before the mode frame, rather than
            # racing the device's own startup state push.
            await asyncio.sleep(MODE_TRANSITION_SETTLE)
        await self._write_dps({tuya_const.DP_MODE: mode_string})
