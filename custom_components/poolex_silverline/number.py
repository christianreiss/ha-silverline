"""Number platform: exposes DP 2 (target temperature) as a standalone slider.

The climate entity already owns DP 2 via its ``target_temperature``
attribute. This standalone entity is added on top so automations can
adjust the setpoint with simple arithmetic (e.g. ``+ 1``) without having
to call ``climate.set_temperature`` and reconstruct the rest of the
service-call payload. Min/max track the same mode-aware ranges the
climate entity exposes so the slider can't write out-of-range values.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from pysilverline import CannotConnect, DeviceState, InvalidAuth, const as tuya_const

from .const import (
    AUTO_TEMP_MAX,
    AUTO_TEMP_MIN,
    COOL_PREFIX_TO_PRESET,
    COOL_TEMP_MAX,
    COOL_TEMP_MIN,
    DOMAIN,
    HEAT_PREFIX_TO_PRESET,
    HEAT_TEMP_MAX,
    HEAT_TEMP_MIN,
)
from .coordinator import SilverlineConfigEntry, SilverlineCoordinator
from .entity import SilverlineEntity

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class SilverlineNumberDescription(NumberEntityDescription):
    """Number description that pulls a value from DeviceState."""

    value_fn: Callable[[DeviceState], float | None]
    # See SilverlineSensorDescription.dp_keys — same firmware-capability gate.
    dp_keys: tuple[str, ...]


NUMBERS: tuple[SilverlineNumberDescription, ...] = (
    SilverlineNumberDescription(
        key="target_temperature",
        translation_key="target_temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        native_step=1.0,
        mode=NumberMode.SLIDER,
        value_fn=lambda d: d.temp_set,
        dp_keys=("2",),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SilverlineConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    supported = coordinator.supported_dps
    async_add_entities(
        SilverlineNumber(coordinator, description)
        for description in NUMBERS
        if set(description.dp_keys) <= supported
    )


class SilverlineNumber(SilverlineEntity, NumberEntity):
    """Standalone number for the heat pump setpoint with mode-aware bounds."""

    entity_description: SilverlineNumberDescription

    def __init__(
        self,
        coordinator: SilverlineCoordinator,
        description: SilverlineNumberDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = (
            f"{coordinator.device_info.device_id}_{description.key}"
        )

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def available(self) -> bool:
        if not super().available or self.coordinator.data is None:
            return False
        return self.entity_description.value_fn(self.coordinator.data) is not None

    @property
    def native_min_value(self) -> float:
        return float(self._mode_temp_range()[0])

    @property
    def native_max_value(self) -> float:
        return float(self._mode_temp_range()[1])

    def _mode_temp_range(self) -> tuple[int, int]:
        """Return ``(min, max)`` matching the device's per-mode clamping.

        Heat: 15..40, Cool: 8..28, Auto: 8..40. When the unit is OFF or
        the mode string is unknown we fall back to the Heat range — it's
        the most common operating mode for a pool heatpump and keeps the
        slider usable until the next state push tells us otherwise.
        """
        state = self.coordinator.data
        if state is None or not state.power:
            return HEAT_TEMP_MIN, HEAT_TEMP_MAX
        mode = state.mode or ""
        if mode == "Auto":
            return AUTO_TEMP_MIN, AUTO_TEMP_MAX
        if mode in COOL_PREFIX_TO_PRESET:
            return COOL_TEMP_MIN, COOL_TEMP_MAX
        if mode in HEAT_PREFIX_TO_PRESET:
            return HEAT_TEMP_MIN, HEAT_TEMP_MAX
        return HEAT_TEMP_MIN, HEAT_TEMP_MAX

    async def async_set_native_value(self, value: float) -> None:
        # DP 2 is integer Celsius; HA's NumberEntity already enforces our
        # native_min_value/native_max_value before delegating here.
        int_value = int(round(value))
        try:
            await self.coordinator.client.set_dp(
                tuya_const.DP_TEMP_SET, int_value
            )
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
        # Optimistic merge so the slider doesn't snap back to the old value
        # while we wait for the device's STATUS push (~200 ms).
        if self.coordinator.data is not None:
            merged = self.coordinator.data.merge(
                {str(tuya_const.DP_TEMP_SET): int_value}
            )
            self.coordinator.async_set_updated_data(merged)
