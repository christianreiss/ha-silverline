"""Diagnostic sensors for the Poolex Silverline."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    REVOLUTIONS_PER_MINUTE,
    EntityCategory,
    UnitOfFrequency,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from pysilverline import DeviceState, const as tuya_const

from .coordinator import SilverlineConfigEntry, SilverlineCoordinator
from .entity import SilverlineEntity

PARALLEL_UPDATES = 0

_FAULT_OPTIONS: list[str] = [
    "none",
    *(name.lower() for name in tuya_const.FAULT_BIT_NAMES.values()),
    "unknown",
]


def _decode_fault(raw: int | None) -> str | None:
    if raw is None:
        return None
    if raw == 0:
        return "none"
    for bit, name in tuya_const.FAULT_BIT_NAMES.items():
        if raw & (1 << bit):
            return name.lower()
    return "unknown"


@dataclass(frozen=True, kw_only=True)
class SilverlineSensorDescription(SensorEntityDescription):
    """Sensor description that pulls a value from DeviceState."""

    value_fn: Callable[[DeviceState], float | int | str | None]
    # DPs (as wire-string keys) the value_fn depends on. The sensor is
    # only registered if every key is present in the device's first
    # DP_QUERY response, so firmware variants that don't expose a DP
    # never leak `unavailable` entities into the registry.
    dp_keys: tuple[str, ...]
    # Optional alternative source: sensors whose value lives on the
    # coordinator itself (accumulators, derived counters) set this and
    # SilverlineSensor.native_value will read from here in preference
    # to value_fn. value_fn must still be supplied for the dataclass
    # contract but is ignored when coord_fn is set.
    coord_fn: Callable[[SilverlineCoordinator], float | int | str | None] | None = None


SENSORS: tuple[SilverlineSensorDescription, ...] = (
    SilverlineSensorDescription(
        # Deliberately no device_class=TEMPERATURE: HA's automatic unit
        # conversion for that class applies the absolute-temperature
        # formula F = C * 9/5 + 32 to every value, which is wrong for a
        # difference (a 5 °C delta should be a 9 °F delta, not 41 °F).
        # No SensorDeviceClass.TEMPERATURE_DELTA exists in HA today, so
        # the safest choice is to leave the class off and present the
        # raw °C number regardless of the user's unit system.
        key="temperature_delta",
        translation_key="temperature_delta",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda d: (
            (d.temp_set - d.temp_current)
            if (d.temp_set is not None and d.temp_current is not None)
            else None
        ),
        dp_keys=("2", "3"),
    ),
    SilverlineSensorDescription(
        key="exhaust_temperature",
        translation_key="exhaust_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.exhaust_temp,
        dp_keys=("101",),
    ),
    SilverlineSensorDescription(
        key="return_temperature",
        translation_key="return_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.return_temp,
        dp_keys=("102",),
    ),
    SilverlineSensorDescription(
        key="coil_temperature",
        translation_key="coil_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.coil_temp,
        dp_keys=("103",),
    ),
    SilverlineSensorDescription(
        key="ambient_temperature",
        translation_key="ambient_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.ambient_temp,
        dp_keys=("104",),
    ),
    SilverlineSensorDescription(
        key="inlet_temperature",
        translation_key="inlet_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.inlet_temp,
        dp_keys=("105",),
    ),
    SilverlineSensorDescription(
        key="outlet_temperature",
        translation_key="outlet_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.outlet_temp,
        dp_keys=("106",),
    ),
    SilverlineSensorDescription(
        key="target_frequency",
        translation_key="target_frequency",
        device_class=SensorDeviceClass.FREQUENCY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.target_frequency,
        dp_keys=("107",),
    ),
    SilverlineSensorDescription(
        key="actual_frequency",
        translation_key="actual_frequency",
        device_class=SensorDeviceClass.FREQUENCY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.actual_frequency,
        dp_keys=("108",),
    ),
    SilverlineSensorDescription(
        key="eev_steps",
        translation_key="eev_steps",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="steps",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.eev_steps,
        dp_keys=("109",),
    ),
    SilverlineSensorDescription(
        key="fan_speed",
        translation_key="fan_speed",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=REVOLUTIONS_PER_MINUTE,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.fan_speed,
        dp_keys=("110",),
    ),
    SilverlineSensorDescription(
        key="fault_code",
        translation_key="fault_code",
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        options=_FAULT_OPTIONS,
        value_fn=lambda d: _decode_fault(d.fault),
        dp_keys=("13",),
    ),
    SilverlineSensorDescription(
        key="runtime_today",
        translation_key="runtime_today",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        entity_category=EntityCategory.DIAGNOSTIC,
        # value_fn is unused — coord_fn takes precedence — but the
        # dataclass requires it, so provide a None-returning stub.
        value_fn=lambda d: None,
        coord_fn=lambda c: c.runtime_today_seconds,
        # DPs 1 + 4 are what compute_hvac_action depends on to decide
        # HEATING/COOLING vs IDLE/OFF. Gating on these matches the
        # climate entity's minimum-firmware contract.
        dp_keys=("1", "4"),
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
        SilverlineSensor(coordinator, description)
        for description in SENSORS
        if set(description.dp_keys) <= supported
    )


class SilverlineSensor(SilverlineEntity, SensorEntity):
    entity_description: SilverlineSensorDescription

    def __init__(
        self,
        coordinator: SilverlineCoordinator,
        description: SilverlineSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.device_id}_{description.key}"

    @property
    def native_value(self) -> float | int | str | None:
        if self.entity_description.coord_fn is not None:
            return self.entity_description.coord_fn(self.coordinator)
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def available(self) -> bool:
        if not super().available or self.coordinator.data is None:
            return False
        # Coordinator-sourced sensors track an accumulator that is always
        # well-defined (starts at 0) — they're available whenever the
        # coordinator itself is healthy.
        if self.entity_description.coord_fn is not None:
            return True
        return self.entity_description.value_fn(self.coordinator.data) is not None
