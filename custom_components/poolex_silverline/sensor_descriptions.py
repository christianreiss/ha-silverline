"""Per-firmware diagnostic sensor catalogs for the Poolex Silverline.

These descriptions wrap Home Assistant's ``SensorEntityDescription`` and so
live integration-side — they must NOT move into the pysilverline library.

The standard (legacy) and Tuya v3.4 (``silverline_v34``) firmwares expose the
same semantic readings but renumber many of their wire DPs. Most descriptions
are byte-identical between the two catalogs; only a handful differ in their
``dp_keys`` gate, and each firmware adds a few of its own. To keep the two
catalogs provably in lock-step the shared descriptions are defined once and
referenced by both, the few that differ only in ``dp_keys`` are derived with
``dataclasses.replace``, and ``descriptions_for_model`` returns the right
catalog for a given model key.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    REVOLUTIONS_PER_MINUTE,
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfFrequency,
    UnitOfTemperature,
    UnitOfTime,
)
from pysilverline import DeviceState
from pysilverline.devices import MODEL_NANO_FI_3KW, MODEL_SILVERLINE_V34

from ._faults import _decode_fault

if TYPE_CHECKING:
    from .coordinator import SilverlineCoordinator


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


# Several ``key`` / ``translation_key`` values below are legacy entity-id
# misnomers that do NOT match what their ``value_fn`` reads (e.g.
# _RETURN_TEMPERATURE reads d.ambient_temp, _COIL_TEMPERATURE reads
# d.pool_temp). The keys are frozen for entity_id stability — renaming one
# would silently change every existing user's entity_id. ``value_fn`` is the
# authoritative source of what each sensor actually measures; human-readable
# names live in translations/<lang>.json. Do NOT rename the keys to "match"
# the lambdas.

# ---- shared descriptions (byte-identical in both catalogs) ------------------

_TEMPERATURE_DELTA = SilverlineSensorDescription(
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
)

_RETURN_TEMPERATURE = SilverlineSensorDescription(
    key="return_temperature",
    translation_key="return_temperature",
    device_class=SensorDeviceClass.TEMPERATURE,
    state_class=SensorStateClass.MEASUREMENT,
    native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    entity_category=EntityCategory.DIAGNOSTIC,
    value_fn=lambda d: d.ambient_temp,
    dp_keys=("102",),
)

_COIL_TEMPERATURE = SilverlineSensorDescription(
    key="coil_temperature",
    translation_key="coil_temperature",
    device_class=SensorDeviceClass.TEMPERATURE,
    state_class=SensorStateClass.MEASUREMENT,
    native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    entity_category=EntityCategory.DIAGNOSTIC,
    value_fn=lambda d: d.pool_temp,
    dp_keys=("103",),
)

_FAULT_CODE = SilverlineSensorDescription(
    # No device_class=ENUM and no options list: _decode_fault returns
    # a comma-joined list ("water_flow, low_pressure") when multiple
    # bits are active, and SensorDeviceClass.ENUM only validates
    # against a fixed string per state.
    key="fault_code",
    translation_key="fault_code",
    entity_category=EntityCategory.DIAGNOSTIC,
    value_fn=lambda d: _decode_fault(d.fault),
    dp_keys=("13",),
)

_CONDENSING_TEMPERATURE = SilverlineSensorDescription(
    key="condensing_temperature",
    translation_key="condensing_temperature",
    device_class=SensorDeviceClass.TEMPERATURE,
    state_class=SensorStateClass.MEASUREMENT,
    native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    entity_category=EntityCategory.DIAGNOSTIC,
    entity_registry_enabled_default=False,
    value_fn=lambda d: d.condensing_temp,
    dp_keys=("124",),
)

_EVAPORATING_TEMPERATURE = SilverlineSensorDescription(
    key="evaporating_temperature",
    translation_key="evaporating_temperature",
    device_class=SensorDeviceClass.TEMPERATURE,
    state_class=SensorStateClass.MEASUREMENT,
    native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    entity_category=EntityCategory.DIAGNOSTIC,
    entity_registry_enabled_default=False,
    value_fn=lambda d: d.evaporating_temp,
    dp_keys=("133",),
)

_SUPERHEAT = SilverlineSensorDescription(
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
)

_COMPRESSOR_LOAD = SilverlineSensorDescription(
    key="compressor_load",
    translation_key="compressor_load",
    state_class=SensorStateClass.MEASUREMENT,
    native_unit_of_measurement=PERCENTAGE,
    entity_category=EntityCategory.DIAGNOSTIC,
    entity_registry_enabled_default=False,
    value_fn=lambda d: d.compressor_load,
    dp_keys=("140",),
)

_TOTAL_OPERATING_HOURS = SilverlineSensorDescription(
    key="total_operating_hours",
    translation_key="total_operating_hours",
    device_class=SensorDeviceClass.DURATION,
    state_class=SensorStateClass.TOTAL_INCREASING,
    native_unit_of_measurement=UnitOfTime.HOURS,
    entity_category=EntityCategory.DIAGNOSTIC,
    entity_registry_enabled_default=False,
    value_fn=lambda d: d.total_hours,
    dp_keys=("120",),
)

_TARGET_SUPERHEAT = SilverlineSensorDescription(
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
)

_TARGET_CONDENSING_TEMPERATURE = SilverlineSensorDescription(
    key="target_condensing_temperature",
    translation_key="target_condensing_temperature",
    device_class=SensorDeviceClass.TEMPERATURE,
    state_class=SensorStateClass.MEASUREMENT,
    native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    entity_category=EntityCategory.DIAGNOSTIC,
    entity_registry_enabled_default=False,
    value_fn=lambda d: d.target_condensing,
    dp_keys=("142",),
)

_RUNTIME_TODAY = SilverlineSensorDescription(
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
)


# ---- descriptions that differ only in their dp_keys per firmware ------------
# Defined once with the standard numbering; the v3.4 variants reuse every
# attribute and only override the wire DP the reading lives on.

_OUTLET_TEMPERATURE = SilverlineSensorDescription(
    key="outlet_temperature",
    translation_key="outlet_temperature",
    device_class=SensorDeviceClass.TEMPERATURE,
    state_class=SensorStateClass.MEASUREMENT,
    native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    entity_category=EntityCategory.DIAGNOSTIC,
    value_fn=lambda d: d.outlet_temp,
    dp_keys=("106",),
)

_EXHAUST_TEMPERATURE = SilverlineSensorDescription(
    key="exhaust_temperature",
    translation_key="exhaust_temperature",
    device_class=SensorDeviceClass.TEMPERATURE,
    state_class=SensorStateClass.MEASUREMENT,
    native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    entity_category=EntityCategory.DIAGNOSTIC,
    value_fn=lambda d: d.suction_temp,
    dp_keys=("101",),
)

_FAN_SPEED = SilverlineSensorDescription(
    key="fan_speed",
    translation_key="fan_speed",
    state_class=SensorStateClass.MEASUREMENT,
    native_unit_of_measurement=REVOLUTIONS_PER_MINUTE,
    entity_category=EntityCategory.DIAGNOSTIC,
    entity_registry_enabled_default=False,
    value_fn=lambda d: d.fan_speed,
    dp_keys=("110",),
)


# ---- standard-only descriptions --------------------------------------------

_AMBIENT_TEMPERATURE = SilverlineSensorDescription(
    key="ambient_temperature",
    translation_key="ambient_temperature",
    device_class=SensorDeviceClass.TEMPERATURE,
    state_class=SensorStateClass.MEASUREMENT,
    native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    entity_category=EntityCategory.DIAGNOSTIC,
    value_fn=lambda d: d.discharge_temp,
    dp_keys=("104",),
)

_INLET_TEMPERATURE = SilverlineSensorDescription(
    key="inlet_temperature",
    translation_key="inlet_temperature",
    device_class=SensorDeviceClass.TEMPERATURE,
    state_class=SensorStateClass.MEASUREMENT,
    native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    entity_category=EntityCategory.DIAGNOSTIC,
    value_fn=lambda d: d.inlet_temp,
    dp_keys=("105",),
)

_TARGET_FREQUENCY = SilverlineSensorDescription(
    key="target_frequency",
    translation_key="target_frequency",
    device_class=SensorDeviceClass.FREQUENCY,
    state_class=SensorStateClass.MEASUREMENT,
    native_unit_of_measurement=UnitOfFrequency.HERTZ,
    entity_category=EntityCategory.DIAGNOSTIC,
    entity_registry_enabled_default=False,
    value_fn=lambda d: d.target_frequency,
    dp_keys=("107",),
)

_ACTUAL_FREQUENCY = SilverlineSensorDescription(
    key="actual_frequency",
    translation_key="actual_frequency",
    device_class=SensorDeviceClass.FREQUENCY,
    state_class=SensorStateClass.MEASUREMENT,
    native_unit_of_measurement=UnitOfFrequency.HERTZ,
    entity_category=EntityCategory.DIAGNOSTIC,
    value_fn=lambda d: d.actual_frequency,
    dp_keys=("108",),
)

_EEV_STEPS = SilverlineSensorDescription(
    key="eev_steps",
    translation_key="eev_steps",
    state_class=SensorStateClass.MEASUREMENT,
    native_unit_of_measurement="steps",
    entity_category=EntityCategory.DIAGNOSTIC,
    entity_registry_enabled_default=False,
    value_fn=lambda d: d.eev_steps,
    dp_keys=("109",),
)


# ---- v3.4-only descriptions ------------------------------------------------

_OUTDOOR_COIL_TEMPERATURE = SilverlineSensorDescription(
    key="outdoor_coil_temperature",
    translation_key="outdoor_coil_temperature",
    device_class=SensorDeviceClass.TEMPERATURE,
    state_class=SensorStateClass.MEASUREMENT,
    native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    entity_category=EntityCategory.DIAGNOSTIC,
    value_fn=lambda d: d.outdoor_coil_temp,
    dp_keys=("105",),
)

_INDOOR_COIL_TEMPERATURE = SilverlineSensorDescription(
    key="indoor_coil_temperature",
    translation_key="indoor_coil_temperature",
    device_class=SensorDeviceClass.TEMPERATURE,
    state_class=SensorStateClass.MEASUREMENT,
    native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    entity_category=EntityCategory.DIAGNOSTIC,
    value_fn=lambda d: d.indoor_coil_temp,
    dp_keys=("108",),
)

_MAIN_VALVE_OPENING = SilverlineSensorDescription(
    key="main_valve_opening",
    translation_key="main_valve_opening",
    state_class=SensorStateClass.MEASUREMENT,
    native_unit_of_measurement="steps",
    entity_category=EntityCategory.DIAGNOSTIC,
    entity_registry_enabled_default=False,
    value_fn=lambda d: d.eev_steps,
    dp_keys=("109",),
)

_AUX_VALVE_OPENING = SilverlineSensorDescription(
    key="aux_valve_opening",
    translation_key="aux_valve_opening",
    state_class=SensorStateClass.MEASUREMENT,
    native_unit_of_measurement="steps",
    entity_category=EntityCategory.DIAGNOSTIC,
    entity_registry_enabled_default=False,
    value_fn=lambda d: d.aux_valve_opening,
    dp_keys=("110",),
)

_WATER_PUMP_RPM = SilverlineSensorDescription(
    key="water_pump_rpm",
    translation_key="water_pump_rpm",
    state_class=SensorStateClass.MEASUREMENT,
    native_unit_of_measurement=REVOLUTIONS_PER_MINUTE,
    entity_category=EntityCategory.DIAGNOSTIC,
    entity_registry_enabled_default=False,
    value_fn=lambda d: d.water_pump_rpm,
    dp_keys=("111",),
)


# ---- electrical diagnostics (Nano Fi 3kW / any future model exposing them) --

_AC_VOLTAGE = SilverlineSensorDescription(
    key="ac_voltage",
    translation_key="ac_voltage",
    device_class=SensorDeviceClass.VOLTAGE,
    state_class=SensorStateClass.MEASUREMENT,
    native_unit_of_measurement=UnitOfElectricPotential.VOLT,
    entity_category=EntityCategory.DIAGNOSTIC,
    value_fn=lambda d: d.ac_voltage,
    dp_keys=("120",),
)

_AC_CURRENT = SilverlineSensorDescription(
    # NOTE: surfaces the raw wire integer. The Tuya schema for this DP
    # declares scale=1 (tenths of an amp) but pysilverline has no per-field
    # divisor for this reading yet — confirm against a clamp meter before
    # trusting the displayed value.
    key="ac_current",
    translation_key="ac_current",
    device_class=SensorDeviceClass.CURRENT,
    state_class=SensorStateClass.MEASUREMENT,
    native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
    entity_category=EntityCategory.DIAGNOSTIC,
    entity_registry_enabled_default=False,
    value_fn=lambda d: d.ac_current,
    dp_keys=("121",),
)


SENSORS: tuple[SilverlineSensorDescription, ...] = (
    _TEMPERATURE_DELTA,
    _EXHAUST_TEMPERATURE,
    _RETURN_TEMPERATURE,
    _COIL_TEMPERATURE,
    _AMBIENT_TEMPERATURE,
    _INLET_TEMPERATURE,
    _OUTLET_TEMPERATURE,
    _TARGET_FREQUENCY,
    _ACTUAL_FREQUENCY,
    _EEV_STEPS,
    _FAN_SPEED,
    _FAULT_CODE,
    _CONDENSING_TEMPERATURE,
    _EVAPORATING_TEMPERATURE,
    _SUPERHEAT,
    _COMPRESSOR_LOAD,
    _TOTAL_OPERATING_HOURS,
    _TARGET_SUPERHEAT,
    _TARGET_CONDENSING_TEMPERATURE,
    _RUNTIME_TODAY,
)


#: Diagnostic catalog for the Tuya v3.4 ``silverline_v34`` firmware
#: (productKey wfzeiyn1ed3axxde). The DP numbering differs from the legacy
#: layout — the client maps each DeviceState field onto the right wire DP via
#: ``LAYOUT_V34_WFZEIYN``, so the ``value_fn`` here reads the same semantic
#: fields while ``dp_keys`` gate on this firmware's actual DP numbers. The
#: numbering was contributed by Martin Čarek (@olomouckyorel) from real hardware.
V34_SENSORS: tuple[SilverlineSensorDescription, ...] = (
    _TEMPERATURE_DELTA,
    replace(_OUTLET_TEMPERATURE, dp_keys=("101",)),
    _RETURN_TEMPERATURE,
    _COIL_TEMPERATURE,
    _OUTDOOR_COIL_TEMPERATURE,
    replace(_EXHAUST_TEMPERATURE, dp_keys=("106",)),
    _INDOOR_COIL_TEMPERATURE,
    _MAIN_VALVE_OPENING,
    _AUX_VALVE_OPENING,
    _WATER_PUMP_RPM,
    replace(_FAN_SPEED, dp_keys=("114",)),
    _FAULT_CODE,
    _CONDENSING_TEMPERATURE,
    _EVAPORATING_TEMPERATURE,
    _SUPERHEAT,
    _COMPRESSOR_LOAD,
    _TOTAL_OPERATING_HOURS,
    _TARGET_SUPERHEAT,
    _TARGET_CONDENSING_TEMPERATURE,
    _RUNTIME_TODAY,
)


#: Diagnostic catalog for the Poolex Nano Fi 3kW (PC-NANO-B3N), Tuya pid
#: am4nomaadnhwvekq. DP numbering cross-checked against the official Tuya
#: cloud product schema (see ``devices/nano_fi.py`` for the full per-DP
#: reasoning). Sensors with no working data source on this firmware
#: (fan speed, EEV steps, main/aux valve steps, target frequency, the
#: refrigeration-circuit diagnostics, and total operating hours — DP 120
#: is AC line voltage on this unit, not a runtime counter) are simply
#: omitted from the catalog rather than included and left permanently
#: "unavailable".
NANO_FI_SENSORS: tuple[SilverlineSensorDescription, ...] = (
    _TEMPERATURE_DELTA,
    replace(_INLET_TEMPERATURE, dp_keys=("103",)),
    replace(_OUTLET_TEMPERATURE, dp_keys=("104",)),
    replace(_RETURN_TEMPERATURE, dp_keys=("106",)),  # reads d.ambient_temp
    replace(_COIL_TEMPERATURE, dp_keys=("3",)),  # reads d.pool_temp (aliased to DP 3)
    _OUTDOOR_COIL_TEMPERATURE,  # dp_keys=("105",) — matches this firmware as-is
    _INDOOR_COIL_TEMPERATURE,  # dp_keys=("108",) — matches this firmware as-is
    replace(_EXHAUST_TEMPERATURE, dp_keys=("117",)),  # reads d.suction_temp
    replace(_ACTUAL_FREQUENCY, dp_keys=("110",)),
    _WATER_PUMP_RPM,  # dp_keys=("111",) — matches this firmware as-is
    _FAULT_CODE,
    _RUNTIME_TODAY,
    _AC_VOLTAGE,
    _AC_CURRENT,
)


def descriptions_for_model(model_key: str) -> tuple[SilverlineSensorDescription, ...]:
    """Return the diagnostic sensor catalog for ``model_key``.

    The v3.4 wfzeiyn firmware and the Nano Fi 3kW both renumber their DPs
    relative to the legacy layout, so each gets a dedicated catalog;
    every other model uses the legacy numbering.
    """
    if model_key == MODEL_SILVERLINE_V34:
        return V34_SENSORS
    if model_key == MODEL_NANO_FI_3KW:
        return NANO_FI_SENSORS
    return SENSORS
