"""Number platform: DP 2 (target temperature) plus Nano Fi 3kW config setpoints.

The climate entity already owns DP 2 via its ``target_temperature``
attribute. This standalone entity is added on top so automations can
adjust the setpoint with simple arithmetic (e.g. ``+ 1``) without having
to call ``climate.set_temperature`` and reconstruct the rest of the
service-call payload. Min/max track the same mode-aware ranges the
climate entity exposes so the slider can't write out-of-range values.

The Nano Fi 3kW additionally exposes ten installer-menu setpoints (DP
124-145, issue #19) as CONFIG-category, disabled-by-default number
entities — heating time, defrost timing/temperature, heating/cooling
hysteresis, and the max/min temperature clamps. tuya-local's independent
device schema for this exact productId (``am4nomaadnhwvekq``,
``poolex_icespa51_heatpump.yaml``) models every one of these as a plain
writable ``number`` entity, so a normal Tuya CONTROL write is expected to
work — but nobody has confirmed a write actually takes on this repo's own
hardware yet. Disabled-by-default + CONFIG category keeps them out of the
way until someone opts in and tests.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.climate.const import HVACMode
from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import EntityCategory, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from pysilverline.devices import MODEL_NANO_FI_3KW

from pysilverline import DeviceState

from .const import CONF_MODEL
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


# DP 124/132/142 collide with LAYOUT_V34_WFZEIYN's condensing_temp/superheat/
# target_condensing — real telemetry on a completely different product family
# sharing the same wire numbers. A raw dp_keys gate (set(dp_keys) <= supported)
# would silently attach "Heating time" to a v34_wfzeiyn device's condensing-temp
# DP, so these are model-gated via numbers_for_model instead — same reasoning
# as sensor_descriptions.py's per-model catalogs.
NANO_FI_CONFIG_NUMBERS: tuple[SilverlineNumberDescription, ...] = (
    SilverlineNumberDescription(
        key="heating_time",
        translation_key="heating_time",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        device_class=NumberDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        native_min_value=30,
        native_max_value=120,
        native_step=1.0,
        mode=NumberMode.BOX,
        value_fn=lambda d: d.heating_time,
        dp_keys=("124",),
    ),
    SilverlineNumberDescription(
        key="defrost_time_limit",
        translation_key="defrost_time_limit",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        device_class=NumberDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        native_min_value=1,
        native_max_value=25,
        native_step=1.0,
        mode=NumberMode.BOX,
        value_fn=lambda d: d.defrost_time_limit,
        dp_keys=("125",),
    ),
    SilverlineNumberDescription(
        key="defrost_cutout_temperature",
        translation_key="defrost_cutout_temperature",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        device_class=NumberDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        native_min_value=-20,
        native_max_value=20,
        native_step=1.0,
        mode=NumberMode.BOX,
        value_fn=lambda d: d.defrost_cutout_temp,
        dp_keys=("126",),
    ),
    SilverlineNumberDescription(
        key="heating_start_hysteresis",
        translation_key="heating_start_hysteresis",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        device_class=NumberDeviceClass.TEMPERATURE_DELTA,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        native_min_value=0,
        native_max_value=18,
        native_step=1.0,
        mode=NumberMode.BOX,
        value_fn=lambda d: d.heating_start_hysteresis,
        dp_keys=("127",),
    ),
    SilverlineNumberDescription(
        key="heating_end_hysteresis",
        translation_key="heating_end_hysteresis",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        device_class=NumberDeviceClass.TEMPERATURE_DELTA,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        native_min_value=0,
        native_max_value=18,
        native_step=1.0,
        mode=NumberMode.BOX,
        value_fn=lambda d: d.heating_end_hysteresis,
        dp_keys=("128",),
    ),
    SilverlineNumberDescription(
        key="cooling_start_hysteresis",
        translation_key="cooling_start_hysteresis",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        device_class=NumberDeviceClass.TEMPERATURE_DELTA,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        native_min_value=0,
        native_max_value=18,
        native_step=1.0,
        mode=NumberMode.BOX,
        value_fn=lambda d: d.cooling_start_hysteresis,
        dp_keys=("130",),
    ),
    SilverlineNumberDescription(
        key="cooling_end_hysteresis",
        translation_key="cooling_end_hysteresis",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        device_class=NumberDeviceClass.TEMPERATURE_DELTA,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        native_min_value=0,
        native_max_value=18,
        native_step=1.0,
        mode=NumberMode.BOX,
        value_fn=lambda d: d.cooling_end_hysteresis,
        dp_keys=("131",),
    ),
    SilverlineNumberDescription(
        key="defrost_temperature",
        translation_key="defrost_temperature",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        device_class=NumberDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        native_min_value=-20,
        native_max_value=20,
        native_step=1.0,
        mode=NumberMode.BOX,
        value_fn=lambda d: d.defrost_temp,
        dp_keys=("132",),
    ),
    SilverlineNumberDescription(
        key="maximum_temperature_limit",
        translation_key="maximum_temperature_limit",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        device_class=NumberDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        native_min_value=35,
        native_max_value=60,
        native_step=1.0,
        mode=NumberMode.BOX,
        value_fn=lambda d: d.max_temp_limit,
        dp_keys=("142",),
    ),
    SilverlineNumberDescription(
        key="minimum_temperature_limit",
        translation_key="minimum_temperature_limit",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        device_class=NumberDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        native_min_value=2,
        native_max_value=10,
        native_step=1.0,
        mode=NumberMode.BOX,
        value_fn=lambda d: d.min_temp_limit,
        dp_keys=("145",),
    ),
)


def numbers_for_model(model_key: str) -> tuple[SilverlineNumberDescription, ...]:
    """Return the number catalog for ``model_key``.

    Only the Nano Fi 3kW gets the config-setpoint block — see
    NANO_FI_CONFIG_NUMBERS for why that can't be a plain dp_keys gate.
    """
    if model_key == MODEL_NANO_FI_3KW:
        return NUMBERS + NANO_FI_CONFIG_NUMBERS
    return NUMBERS


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SilverlineConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    supported = coordinator.supported_dps
    model_key = entry.data.get(CONF_MODEL, "")
    async_add_entities(
        SilverlineNumber(coordinator, description)
        for description in numbers_for_model(model_key)
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
