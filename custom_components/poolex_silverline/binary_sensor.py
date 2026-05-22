"""Binary sensors for water-pump state and decoded fault bits."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.components.climate.const import HVACAction
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from pysilverline import DeviceState

from .coordinator import SilverlineConfigEntry, SilverlineCoordinator
from .entity import SilverlineEntity
from .util import compute_hvac_action

PARALLEL_UPDATES = 0


def _bit(state: DeviceState, position: int) -> bool | None:
    if state.fault is None:
        return None
    return bool(state.fault & (1 << position))


def _compressor_active(state: DeviceState) -> bool | None:
    """True iff the heat pump is actively heating or cooling right now.

    We share the hvac_action computation with the climate entity so the
    "Compressor" binary sensor flips in lockstep with the climate card.
    `last_direction` is irrelevant here — it only matters for OFF→mode
    mapping, which always reports OFF (i.e. False) regardless.
    """
    action = compute_hvac_action(state, None)
    if action is None:
        return None
    return action in (HVACAction.HEATING, HVACAction.COOLING)


@dataclass(frozen=True, kw_only=True)
class SilverlineBinarySensorDescription(BinarySensorEntityDescription):
    value_fn: Callable[[DeviceState], bool | None]
    # See SilverlineSensorDescription.dp_keys — same firmware-capability gate.
    dp_keys: tuple[str, ...]


BINARY_SENSORS: tuple[SilverlineBinarySensorDescription, ...] = (
    SilverlineBinarySensorDescription(
        key="compressor_running",
        translation_key="compressor_running",
        device_class=BinarySensorDeviceClass.RUNNING,
        value_fn=_compressor_active,
        # DP 1 (power) + DP 4 (mode) are present on every firmware we've
        # ever seen — including the minimal PC-SLP090N. The DP 108
        # (actual_frequency) refinement is opportunistic.
        dp_keys=("1", "4"),
    ),
    SilverlineBinarySensorDescription(
        key="water_pump",
        translation_key="water_pump",
        device_class=BinarySensorDeviceClass.RUNNING,
        value_fn=lambda d: d.water_pump,
        dp_keys=("111",),
    ),
    SilverlineBinarySensorDescription(
        key="fault_water_flow",
        translation_key="fault_water_flow",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _bit(d, 0),
        dp_keys=("13",),
    ),
    SilverlineBinarySensorDescription(
        key="fault_antifreeze",
        translation_key="fault_antifreeze",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _bit(d, 1),
        dp_keys=("13",),
    ),
    SilverlineBinarySensorDescription(
        key="fault_high_pressure",
        translation_key="fault_high_pressure",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _bit(d, 2),
        dp_keys=("13",),
    ),
    SilverlineBinarySensorDescription(
        key="fault_low_pressure",
        translation_key="fault_low_pressure",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _bit(d, 3),
        dp_keys=("13",),
    ),
    SilverlineBinarySensorDescription(
        key="fault_communication",
        translation_key="fault_communication",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _bit(d, 4),
        dp_keys=("13",),
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
        SilverlineBinarySensor(coordinator, description)
        for description in BINARY_SENSORS
        if set(description.dp_keys) <= supported
    )


class SilverlineBinarySensor(SilverlineEntity, BinarySensorEntity):
    entity_description: SilverlineBinarySensorDescription

    def __init__(
        self,
        coordinator: SilverlineCoordinator,
        description: SilverlineBinarySensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = (
            f"{coordinator.device_info.device_id}_{description.key}"
        )

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def available(self) -> bool:
        if not super().available or self.coordinator.data is None:
            return False
        return self.entity_description.value_fn(self.coordinator.data) is not None
