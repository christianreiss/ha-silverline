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

from homeassistant.components.climate.const import HVACMode
from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from pysilverline import const as tuya_const

from .const import (
    AUTO_MODE_STRINGS,
    COOL_PREFIX_TO_PRESET,
    DOMAIN,
    HEAT_PREFIX_TO_PRESET,
    MODE_TRANSITION_SETTLE,
    PRESET_BOOST,
    PRESET_ECO,
    PRESET_NONE,
    PRESET_TO_COOL_DP,
    PRESET_TO_HEAT_DP,
)
from .coordinator import SilverlineConfigEntry, SilverlineCoordinator
from .entity import SilverlineEntity
from .util import derive_hvac_mode, derive_preset

PARALLEL_UPDATES = 1

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

_HVAC_MODE_TO_OPMODE: Final[dict[HVACMode, str]] = {
    HVACMode.OFF: OPMODE_OFF,
    HVACMode.HEAT: OPMODE_HEAT,
    HVACMode.COOL: OPMODE_COOL,
    HVACMode.HEAT_COOL: OPMODE_HEAT_COOL,
}


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


class SilverlinePresetSelect(SilverlineEntity, SelectEntity):
    """Flat dropdown for the inverter preset (none / boost / eco)."""

    _attr_translation_key = "preset_mode"
    _attr_options = PRESET_OPTIONS

    def __init__(self, coordinator: SilverlineCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.device_id}_preset_mode"

    @property
    def current_option(self) -> str | None:
        state = self.coordinator.data
        if state is None:
            return PRESET_NONE
        return derive_preset(state)

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
        if current_mode in AUTO_MODE_STRINGS:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="preset_not_available_in_auto",
            )
        profile = self.coordinator.profile
        if current_mode in HEAT_PREFIX_TO_PRESET:
            table = profile.preset_to_heat_dp if profile and profile.preset_to_heat_dp is not None else PRESET_TO_HEAT_DP
            mode_string = table[option]
        elif current_mode in COOL_PREFIX_TO_PRESET:
            table = profile.preset_to_cool_dp if profile and profile.preset_to_cool_dp is not None else PRESET_TO_COOL_DP
            mode_string = table[option]
        else:
            # Unknown DP-4 string — refuse rather than guess heat/cool.
            return
        await self._write_dps({tuya_const.DP_MODE: mode_string})


class SilverlineOperatingModeSelect(SilverlineEntity, SelectEntity):
    """Flat dropdown for the HVAC mode (off / heat / cool / heat_cool).

    Mirrors climate.SilverlineClimate.async_set_hvac_mode, including the
    0.7s post-write settle so chained service calls don't race the
    device's per-mode setpoint restore push.
    """

    _attr_translation_key = "operating_mode"
    _attr_options = OPMODE_OPTIONS

    def __init__(self, coordinator: SilverlineCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.device_id}_operating_mode"

    @property
    def current_option(self) -> str | None:
        state = self.coordinator.data
        if state is None:
            return None
        mode = derive_hvac_mode(state)
        if mode is None:
            return None
        return _HVAC_MODE_TO_OPMODE.get(mode)

    async def async_select_option(self, option: str) -> None:
        if option == OPMODE_OFF:
            await self._write_dps({tuya_const.DP_POWER: False})
            return

        profile = self.coordinator.profile
        if option == OPMODE_HEAT:
            heat_map = profile.preset_to_heat_dp if profile and profile.preset_to_heat_dp is not None else PRESET_TO_HEAT_DP
            mode_string = heat_map[PRESET_NONE]
        elif option == OPMODE_COOL:
            cool_map = profile.preset_to_cool_dp if profile and profile.preset_to_cool_dp is not None else PRESET_TO_COOL_DP
            mode_string = cool_map[PRESET_NONE]
        elif option == OPMODE_HEAT_COOL:
            mode_string = profile.auto_dp if profile and profile.auto_dp is not None else "Auto"
        else:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unsupported_hvac_mode",
                translation_placeholders={"mode": option},
            )
        await self._write_dps(
            {tuya_const.DP_POWER: True, tuya_const.DP_MODE: mode_string}
        )
        # See climate.py: the device pushes its per-mode-memory setpoint
        # ~430-500 ms after a mode change. Without this sleep, a chained
        # service call's set_temperature can be clobbered by that push.
        await asyncio.sleep(MODE_TRANSITION_SETTLE)
