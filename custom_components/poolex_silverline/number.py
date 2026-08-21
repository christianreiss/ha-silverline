"""Number platform: DP 2 (target temperature).

The climate entity already owns DP 2 via its ``target_temperature``
attribute. This standalone entity is added on top so automations can
adjust the setpoint with simple arithmetic (e.g. ``+ 1``) without having
to call ``climate.set_temperature`` and reconstruct the rest of the
service-call payload. Min/max track the same mode-aware ranges the
climate entity exposes so the slider can't write out-of-range values.

DP 2 is the only writable setpoint on any supported firmware. The Nano Fi
installer-menu block (DP 124-145, issue #19) briefly lived here as ten
writable numbers on the strength of tuya-local's schema; hardware then
showed the unit silently declines those writes and re-asserts its own
value a few seconds later, so they moved to read-only diagnostic sensors
— see ``NANO_FI_CONFIG_SENSORS`` in ``sensor_descriptions.py``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.climate.const import HVACMode
from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from pysilverline import DeviceState

from .coordinator import SilverlineConfigEntry, SilverlineCoordinator
from .entity import SilverlineEntity
from .util import derive_hvac_mode, mode_temp_range

# Write-capable setpoint: serialize per entity so back-to-back automation
# writes don't race the optimistic merge. pysilverline serializes the
# underlying socket writes via _send_lock already; this matches the
# convention used for climate/select.
PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class SilverlineNumberDescription(NumberEntityDescription):
    """Number description that pulls a value from DeviceState."""

    value_fn: Callable[[DeviceState], float | None]
    # See SilverlineSensorDescription.dp_keys — same firmware-capability gate.
    # Every description here maps to exactly one DP; async_set_native_value
    # writes to dp_keys[0].
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
    """Standalone number for a heat pump setpoint."""

    entity_description: SilverlineNumberDescription

    def __init__(
        self,
        coordinator: SilverlineCoordinator,
        description: SilverlineNumberDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.device_id}_{description.key}"

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
        # Only target_temperature is mode-aware; every other description
        # carries a fixed native_min_value the base NumberEntity already
        # reads from entity_description.
        if self.entity_description.key == "target_temperature":
            return float(self._mode_temp_range()[0])
        return super().native_min_value

    @property
    def native_max_value(self) -> float:
        if self.entity_description.key == "target_temperature":
            return float(self._mode_temp_range()[1])
        return super().native_max_value

    def _mode_temp_range(self) -> tuple[int, int]:
        """Return ``(min, max)`` matching the device's per-mode clamping.

        Heat: 15..40, Cool: 8..28, Auto: 8..40. When the unit is OFF or
        the mode string is unknown we fall back to the Heat range — it's
        the most common operating mode for a pool heatpump and keeps the
        slider usable until the next state push tells us otherwise.
        """
        state = self.coordinator.data
        profile = self.coordinator.profile
        if state is None or not state.power:
            return mode_temp_range(HVACMode.HEAT, profile)
        return mode_temp_range(derive_hvac_mode(state), profile)

    async def async_set_native_value(self, value: float) -> None:
        # Every description maps to exactly one DP (dp_keys[0]); values are
        # integer on the wire on every model seen so far. HA's NumberEntity
        # already enforces native_min_value/native_max_value before
        # delegating here.
        dp = int(self.entity_description.dp_keys[0])
        await self._write_dps({dp: int(round(value))})
