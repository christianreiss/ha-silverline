"""Climate platform for the Poolex Silverline."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import (
    ATTR_HVAC_MODE,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, PRECISION_WHOLE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from pysilverline import const as tuya_const

from .const import (
    DOMAIN,
    MODE_TRANSITION_SETTLE,
    PRESET_BOOST,
    PRESET_ECO,
    PRESET_NONE,
    PRESET_TO_COOL_DP,
    PRESET_TO_HEAT_DP,
)
from .coordinator import SilverlineConfigEntry, SilverlineCoordinator
from .entity import SilverlineEntity
from .util import compute_hvac_action, derive_hvac_mode, derive_preset, mode_temp_range

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 1

PRESETS: list[str] = [PRESET_NONE, PRESET_BOOST, PRESET_ECO]

HVAC_MODES = [HVACMode.OFF, HVACMode.HEAT, HVACMode.COOL, HVACMode.HEAT_COOL]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SilverlineConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([SilverlineClimate(entry.runtime_data)])


class SilverlineClimate(SilverlineEntity, ClimateEntity, RestoreEntity):
    """Single climate entity that maps DP 1 (power) and DP 4 (mode) onto
    HVAC mode + preset."""

    _attr_translation_key = "heatpump"
    _attr_name = None
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 1
    _attr_precision = PRECISION_WHOLE
    _attr_hvac_modes = HVAC_MODES
    _attr_preset_modes = PRESETS
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )

    def __init__(self, coordinator: SilverlineCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.device_id}_climate"
        # When the device is off we can't read DP 4 to know the user's last
        # intent. Persist it so HA-restart while-off still remembers heat-vs-cool.
        self._last_direction: HVACMode = HVACMode.HEAT
        self._last_preset: str = PRESET_NONE

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last_state := await self.async_get_last_state()) is not None:
            attrs = last_state.attributes
            stored_dir = attrs.get("last_direction")
            if stored_dir in (HVACMode.HEAT, HVACMode.COOL, HVACMode.HEAT_COOL):
                self._last_direction = HVACMode(stored_dir)
            stored_preset = attrs.get("last_preset")
            if stored_preset in PRESETS:
                self._last_preset = stored_preset
        self._sync_from_state()

    def _handle_coordinator_update(self) -> None:
        self._sync_from_state()
        super()._handle_coordinator_update()

    def _sync_from_state(self) -> None:
        """Mirror the active hvac/preset into _last_* whenever the device
        is not off, so an OFF→ON transition can later restore them."""
        current_hvac = self.hvac_mode
        if current_hvac in (HVACMode.HEAT, HVACMode.COOL, HVACMode.HEAT_COOL):
            self._last_direction = current_hvac
        # Only capture the preset while the unit is actively heating/cooling;
        # in OFF the preset_mode property collapses to "none" and would
        # otherwise clobber the user's last intent across an off→on cycle.
        if current_hvac in (HVACMode.HEAT, HVACMode.COOL):
            current_preset = self.preset_mode
            if current_preset in PRESETS:
                self._last_preset = current_preset

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "last_direction": self._last_direction.value,
            "last_preset": self._last_preset,
        }

    @property
    def hvac_mode(self) -> HVACMode | None:
        state = self.coordinator.data
        if state is None:
            return None
        return derive_hvac_mode(state)

    @property
    def preset_mode(self) -> str | None:
        state = self.coordinator.data
        if state is None:
            return PRESET_NONE
        return derive_preset(state)

    @property
    def current_temperature(self) -> float | None:
        state = self.coordinator.data
        return None if state is None else state.temp_current

    @property
    def target_temperature(self) -> float | None:
        state = self.coordinator.data
        return None if state is None else state.temp_set

    @property
    def hvac_action(self) -> HVACAction | None:
        """Current operation state — what HA uses to colorize the icon.

        Without this, HA can't distinguish "in heat mode and actively
        heating" from "in heat mode but target reached, idle now" — both
        render the same. The computation lives in `util.compute_hvac_action`
        so binary_sensor.compressor_running and sensor.runtime_today can
        reuse the same decision tree.
        """
        state = self.coordinator.data
        if state is None:
            return None
        # Preserve the "unknown mode → unknown action" semantic the entity
        # owns: our hvac_mode property returns None when DP 4 carries a
        # string we don't decode; surface that as None too, not IDLE.
        if state.power and self.hvac_mode is None:
            return None
        return compute_hvac_action(state)

    @property
    def min_temp(self) -> float:
        return self._mode_temp_range()[0]

    @property
    def max_temp(self) -> float:
        return self._mode_temp_range()[1]

    def _mode_temp_range(self) -> tuple[int, int]:
        # Device clamps differently per mode (Heat 15-40, Cool 8-28, Auto 8-40).
        # When OFF we use _last_direction so the slider still bounds sensibly.
        mode = self.hvac_mode
        if mode == HVACMode.OFF:
            mode = self._last_direction
        return mode_temp_range(mode)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode == HVACMode.OFF:
            await self._write_dps({tuya_const.DP_POWER: False})
            return

        if hvac_mode in (HVACMode.HEAT, HVACMode.COOL):
            mode_string = self._mode_string_for(hvac_mode, self._last_preset)
            self._last_direction = hvac_mode
        elif hvac_mode == HVACMode.HEAT_COOL:
            profile = self.coordinator.profile
            mode_string = profile.auto_dp if profile and profile.auto_dp is not None else "Auto"
            self._last_direction = HVACMode.HEAT_COOL
            self._last_preset = PRESET_NONE
        else:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unsupported_hvac_mode",
                translation_placeholders={"mode": str(hvac_mode)},
            )

        await self._write_dps(
            {tuya_const.DP_POWER: True, tuya_const.DP_MODE: mode_string}
        )
        # Device has per-mode setpoint memory: entering a mode triggers a
        # restore-push for that mode's last temp ~430-500 ms later, which
        # would overwrite any setpoint a chained service call writes too soon.
        await asyncio.sleep(MODE_TRANSITION_SETTLE)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        if preset_mode not in PRESETS:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unsupported_preset_mode",
                translation_placeholders={"preset": preset_mode},
            )
        current = self.hvac_mode
        if current == HVACMode.HEAT_COOL:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="preset_not_available_in_auto",
            )
        if current not in (HVACMode.HEAT, HVACMode.COOL):
            self._last_preset = preset_mode
            return

        self._last_preset = preset_mode
        mode_string = self._mode_string_for(current, preset_mode)
        await self._write_dps({tuya_const.DP_MODE: mode_string})

    async def async_set_temperature(self, **kwargs: Any) -> None:
        target = kwargs.get(ATTR_TEMPERATURE)
        if target is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="missing_target_temperature",
            )
        # HA's set_temperature service can bundle a mode change with the
        # target. Sequence mode-first via async_set_hvac_mode so its
        # MODE_TRANSITION_SETTLE delay lets the device's per-mode-memory
        # restore push land before our setpoint write.
        hvac_mode = kwargs.get(ATTR_HVAC_MODE)
        if hvac_mode is not None and hvac_mode != self.hvac_mode:
            await self.async_set_hvac_mode(hvac_mode)
        # HA's climate service guards min_temp/max_temp before we get here,
        # and our properties are mode-aware, so the value is in range. We
        # just round to int (DP 2 is integer °C) and write.
        value = int(round(float(target)))
        await self._write_dps({tuya_const.DP_TEMP_SET: value})

    async def async_turn_on(self) -> None:
        await self.async_set_hvac_mode(self._last_direction)

    async def async_turn_off(self) -> None:
        await self.async_set_hvac_mode(HVACMode.OFF)

    def _mode_string_for(self, direction: HVACMode, preset: str) -> str:
        profile = self.coordinator.profile
        if direction == HVACMode.HEAT:
            table = (profile.preset_to_heat_dp if profile and profile.preset_to_heat_dp is not None else PRESET_TO_HEAT_DP)
        else:
            table = (profile.preset_to_cool_dp if profile and profile.preset_to_cool_dp is not None else PRESET_TO_COOL_DP)
        return table.get(preset, table[PRESET_NONE])
