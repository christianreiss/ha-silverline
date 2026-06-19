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
    PERCENTAGE,
    REVOLUTIONS_PER_MINUTE,
    EntityCategory,
    UnitOfFrequency,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from pysilverline import DeviceState, const as tuya_const

from .const import CONF_MODEL
from .coordinator import SilverlineConfigEntry, SilverlineCoordinator
from .entity import SilverlineEntity

PARALLEL_UPDATES = 0


def _decode_fault(raw: int | None) -> str | None:
    """Return every active fault bit as a comma-joined name list.

    - ``None`` when DP 13 hasn't been observed yet.
    - ``None`` when the fault bitmap is zero — the sensor surfaces as
      "unknown" / no state which matches the OEM controller's blank
      display when nothing is wrong.
    - Otherwise a comma-joined list of FAULT_BIT_NAMES values in bit
      order, plus ``"bit<n>"`` placeholders for any bits we don't have a
      symbolic name for so a new fault on a new firmware variant still
      surfaces instead of being silently dropped.
    """
    if raw is None or raw == 0:
        return None
    names: list[str] = []
    bit = 0
    while (1 << bit) <= raw:
        if raw & (1 << bit):
            names.append(tuya_const.FAULT_BIT_NAMES.get(bit, f"bit{bit}"))
        bit += 1
    return ", ".join(names)


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
        value_fn=lambda d: d.suction_temp,
        dp_keys=("101",),
    ),
    SilverlineSensorDescription(
        key="return_temperature",
        translation_key="return_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.ambient_temp,
        dp_keys=("102",),
    ),
    SilverlineSensorDescription(
        key="coil_temperature",
        translation_key="coil_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.pool_temp,
        dp_keys=("103",),
    ),
    SilverlineSensorDescription(
        key="ambient_temperature",
        translation_key="ambient_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.discharge_temp,
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
        # No device_class=ENUM and no options list: _decode_fault returns
        # a comma-joined list ("water_flow, low_pressure") when multiple
        # bits are active, and SensorDeviceClass.ENUM only validates
        # against a fixed string per state.
        key="fault_code",
        translation_key="fault_code",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _decode_fault(d.fault),
        dp_keys=("13",),
    ),
    SilverlineSensorDescription(
        key="condensing_temperature",
        translation_key="condensing_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.condensing_temp,
        dp_keys=("124",),
    ),
    SilverlineSensorDescription(
        key="evaporating_temperature",
        translation_key="evaporating_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.evaporating_temp,
        dp_keys=("133",),
    ),
    SilverlineSensorDescription(
        # No device_class=TEMPERATURE: superheat is a temperature difference
        # (suction gas minus saturation), not an absolute temperature. The
        # absolute-temperature unit-conversion formula (F = C*9/5+32) would
        # produce a wrong value for a delta; same reasoning as temperature_delta.
        key="superheat",
        translation_key="superheat",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.superheat,
        dp_keys=("132",),
    ),
    SilverlineSensorDescription(
        key="compressor_load",
        translation_key="compressor_load",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.compressor_load,
        dp_keys=("140",),
    ),
    SilverlineSensorDescription(
        key="total_operating_hours",
        translation_key="total_operating_hours",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfTime.HOURS,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.total_hours,
        dp_keys=("120",),
    ),
    SilverlineSensorDescription(
        # No device_class=TEMPERATURE: same reasoning as superheat — this is
        # a controller setpoint delta, not an absolute ambient temperature.
        key="target_superheat",
        translation_key="target_superheat",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.target_superheat,
        dp_keys=("137",),
    ),
    SilverlineSensorDescription(
        key="target_condensing_temperature",
        translation_key="target_condensing_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.target_condensing,
        dp_keys=("142",),
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


#: Diagnostic catalog for the Tuya v3.4 ``silverline_v34`` firmware
#: (productKey wfzeiyn1ed3axxde). The DP numbering differs from the legacy
#: layout — the client maps each DeviceState field onto the right wire DP via
#: ``LAYOUT_V34_WFZEIYN``, so the ``value_fn`` here reads the same semantic
#: fields while ``dp_keys`` gate on this firmware's actual DP numbers. The
#: numbering was contributed by Martin Čarek (@olomouckyorel) from real hardware.
V34_SENSORS: tuple[SilverlineSensorDescription, ...] = (
    SilverlineSensorDescription(
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
        key="outlet_temperature",
        translation_key="outlet_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.outlet_temp,
        dp_keys=("101",),
    ),
    SilverlineSensorDescription(
        key="return_temperature",
        translation_key="return_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.ambient_temp,
        dp_keys=("102",),
    ),
    SilverlineSensorDescription(
        key="coil_temperature",
        translation_key="coil_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.pool_temp,
        dp_keys=("103",),
    ),
    SilverlineSensorDescription(
        key="outdoor_coil_temperature",
        translation_key="outdoor_coil_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.outdoor_coil_temp,
        dp_keys=("105",),
    ),
    SilverlineSensorDescription(
        key="exhaust_temperature",
        translation_key="exhaust_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.suction_temp,
        dp_keys=("106",),
    ),
    SilverlineSensorDescription(
        key="indoor_coil_temperature",
        translation_key="indoor_coil_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.indoor_coil_temp,
        dp_keys=("108",),
    ),
    SilverlineSensorDescription(
        key="main_valve_opening",
        translation_key="main_valve_opening",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="steps",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.eev_steps,
        dp_keys=("109",),
    ),
    SilverlineSensorDescription(
        key="aux_valve_opening",
        translation_key="aux_valve_opening",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="steps",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.aux_valve_opening,
        dp_keys=("110",),
    ),
    SilverlineSensorDescription(
        key="water_pump_rpm",
        translation_key="water_pump_rpm",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=REVOLUTIONS_PER_MINUTE,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.water_pump_rpm,
        dp_keys=("111",),
    ),
    SilverlineSensorDescription(
        key="fan_speed",
        translation_key="fan_speed",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=REVOLUTIONS_PER_MINUTE,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.fan_speed,
        dp_keys=("114",),
    ),
    SilverlineSensorDescription(
        key="fault_code",
        translation_key="fault_code",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _decode_fault(d.fault),
        dp_keys=("13",),
    ),
    SilverlineSensorDescription(
        key="condensing_temperature",
        translation_key="condensing_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.condensing_temp,
        dp_keys=("124",),
    ),
    SilverlineSensorDescription(
        key="evaporating_temperature",
        translation_key="evaporating_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.evaporating_temp,
        dp_keys=("133",),
    ),
    SilverlineSensorDescription(
        key="superheat",
        translation_key="superheat",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.superheat,
        dp_keys=("132",),
    ),
    SilverlineSensorDescription(
        key="compressor_load",
        translation_key="compressor_load",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.compressor_load,
        dp_keys=("140",),
    ),
    SilverlineSensorDescription(
        key="total_operating_hours",
        translation_key="total_operating_hours",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfTime.HOURS,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.total_hours,
        dp_keys=("120",),
    ),
    SilverlineSensorDescription(
        key="target_superheat",
        translation_key="target_superheat",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.target_superheat,
        dp_keys=("137",),
    ),
    SilverlineSensorDescription(
        key="target_condensing_temperature",
        translation_key="target_condensing_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.target_condensing,
        dp_keys=("142",),
    ),
    SilverlineSensorDescription(
        key="runtime_today",
        translation_key="runtime_today",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: None,
        coord_fn=lambda c: c.runtime_today_seconds,
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
    # The v3.4 wfzeiyn firmware renumbers its DPs, so it gets a dedicated
    # catalog; every other model uses the legacy numbering.
    catalog = (
        V34_SENSORS
        if entry.data.get(CONF_MODEL) == "silverline_v34"
        else SENSORS
    )
    async_add_entities(
        SilverlineSensor(coordinator, description)
        for description in catalog
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
