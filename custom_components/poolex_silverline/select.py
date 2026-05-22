"""Standalone select entities mirroring the climate state-machine.

Some dashboards want flat dropdowns for preset and operating-mode instead
of going through HA's climate card. These selects are thin shims over the
same DP-1 (power) / DP-4 (mode enum) plumbing that ``climate.py`` already
implements — they don't carry their own memory so they can stay simple
and stateless. Power/mode memory across OFF→ON transitions still lives
on the climate entity.
"""

from __future__ import annotations

import asyncio
from typing import Final

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from pysilverline import CannotConnect, InvalidAuth, const as tuya_const

from .const import (
    COOL_PREFIX_TO_PRESET,
    DOMAIN,
    HEAT_PREFIX_TO_PRESET,
    PRESET_BOOST,
    PRESET_ECO,
    PRESET_TO_COOL_DP,
    PRESET_TO_HEAT_DP,
)
from .coordinator import SilverlineConfigEntry, SilverlineCoordinator
from .entity import SilverlineEntity

PARALLEL_UPDATES = 1

PRESET_NONE = "none"
PRESET_OPTIONS: Final[list[str]] = [PRESET_NONE, PRESET_BOOST, PRESET_ECO]

OPMODE_OFF = "off"
OPMODE_HEAT = "heat"
OPMODE_COOL = "cool"
OPMODE_HEAT_COOL = "heat_cool"
OPMODE_OPTIONS: Final[list[str]] = [
    OPMODE_OFF,
    OPMODE_HEAT,
    OPMODE_COOL,
    OPMODE_HEAT_COOL,
]

# Keep in sync with climate.py — entering a non-OFF mode triggers a
# device-side per-mode setpoint restore push ~430-500 ms later, so we
# block briefly to avoid racing a chained set_temperature against it.
_MODE_TRANSITION_SETTLE: Final = 0.7


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SilverlineConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    supported = coordinator.supported_dps
    entities: list[SilverlineEntity] = []
    # Both selects require DPs 1 (power) and 4 (mode enum). preset_mode
    # only strictly needs DP 4, but we hide it on firmware lacking DP 1
    # too — a heat pump where you can't read power state is effectively
    # broken for our purposes and the climate entity wouldn't surface
    # either.
    preset_keys = {"4"}
    opmode_keys = {"1", "4"}
    if preset_keys <= supported:
        entities.append(SilverlinePresetSelect(coordinator))
    if opmode_keys <= supported:
        entities.append(SilverlineOperatingModeSelect(coordinator))
    async_add_entities(entities)


class _SilverlineSelectBase(SilverlineEntity, SelectEntity):
    """Shared write helper that mirrors climate.SilverlineClimate._write."""

    async def _write(self, dps: dict[int, bool | int | str]) -> None:
        try:
            await self.coordinator.client.set_multiple(dps)
        except InvalidAuth as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="auth_failed",
            ) from err
        except CannotConnect as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="set_failed",
                translation_placeholders={"reason": str(err)},
            ) from err
        # Optimistic merge so the entity reflects the change immediately;
        # the device's STATUS push within ~200 ms will overlay on top.
        if self.coordinator.data is not None:
            merged = self.coordinator.data.merge({str(k): v for k, v in dps.items()})
            self.coordinator.async_set_updated_data(merged)


class SilverlinePresetSelect(_SilverlineSelectBase):
    """Flat dropdown for the inverter preset (none / boost / eco)."""

    _attr_translation_key = "preset_mode"
    _attr_options = PRESET_OPTIONS
    # Used by HA's capability filter — DP 4 is the only DP we read/write here.
    dp_keys = ("4",)

    def __init__(self, coordinator: SilverlineCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.device_info.device_id}_preset_mode"

    @property
    def current_option(self) -> str | None:
        state = self.coordinator.data
        if state is None or not state.power or not state.mode:
            return PRESET_NONE
        if state.mode in HEAT_PREFIX_TO_PRESET:
            return HEAT_PREFIX_TO_PRESET[state.mode]
        if state.mode in COOL_PREFIX_TO_PRESET:
            return COOL_PREFIX_TO_PRESET[state.mode]
        # "Auto" or any unknown string — no preset is active.
        return PRESET_NONE

    async def async_select_option(self, option: str) -> None:
        if option not in PRESET_OPTIONS:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unsupported_preset_mode",
                translation_placeholders={"preset": option},
            )
        state = self.coordinator.data
        # Match climate.py: presets are device-meaningful only in Heat/Cool.
        # Auto explicitly rejects so the UI surfaces a clear error rather
        # than silently swallowing the click. While OFF the climate entity
        # is the source of truth for the pending preset; we no-op here so
        # the user has to power on (then re-select) — keeps this entity
        # stateless.
        current_mode = state.mode if state is not None else None
        if state is None or not state.power:
            return
        if current_mode == "Auto":
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="preset_not_available_in_auto",
            )
        if current_mode in HEAT_PREFIX_TO_PRESET:
            mode_string = PRESET_TO_HEAT_DP[option]
        elif current_mode in COOL_PREFIX_TO_PRESET:
            mode_string = PRESET_TO_COOL_DP[option]
        else:
            # Unknown DP-4 string — refuse rather than guess heat/cool.
            return
        await self._write({tuya_const.DP_MODE: mode_string})


class SilverlineOperatingModeSelect(_SilverlineSelectBase):
    """Flat dropdown for the HVAC mode (off / heat / cool / heat_cool).

    Mirrors climate.SilverlineClimate.async_set_hvac_mode, including the
    0.7s post-write settle so chained service calls don't race the
    device's per-mode setpoint restore push.
    """

    _attr_translation_key = "operating_mode"
    _attr_options = OPMODE_OPTIONS
    dp_keys = ("1", "4")

    def __init__(self, coordinator: SilverlineCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.device_info.device_id}_operating_mode"

    @property
    def current_option(self) -> str | None:
        state = self.coordinator.data
        if state is None or state.power is None:
            return None
        if not state.power:
            return OPMODE_OFF
        mode = state.mode or ""
        if mode == "Auto":
            return OPMODE_HEAT_COOL
        if mode in HEAT_PREFIX_TO_PRESET:
            return OPMODE_HEAT
        if mode in COOL_PREFIX_TO_PRESET:
            return OPMODE_COOL
        return None

    async def async_select_option(self, option: str) -> None:
        if option == OPMODE_OFF:
            await self._write({tuya_const.DP_POWER: False})
            return

        if option == OPMODE_HEAT:
            mode_string = "Heat"
        elif option == OPMODE_COOL:
            mode_string = "Cool"
        elif option == OPMODE_HEAT_COOL:
            mode_string = "Auto"
        else:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unsupported_hvac_mode",
                translation_placeholders={"mode": option},
            )
        await self._write(
            {tuya_const.DP_POWER: True, tuya_const.DP_MODE: mode_string}
        )
        # See climate.py: the device pushes its per-mode-memory setpoint
        # ~430-500 ms after a mode change. Without this sleep, a chained
        # service call's set_temperature can be clobbered by that push.
        await asyncio.sleep(_MODE_TRANSITION_SETTLE)
