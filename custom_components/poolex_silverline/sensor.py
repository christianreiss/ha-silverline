"""Diagnostic sensors for the Poolex Silverline."""

from __future__ import annotations

from homeassistant.components.sensor import RestoreSensor, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_MODEL
from .coordinator import SilverlineConfigEntry, SilverlineCoordinator
from .entity import SilverlineEntity
from .sensor_descriptions import (
    ENERGY_CONSUMPTION_KEY,
    SilverlineSensorDescription,
    descriptions_for_model,
)

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SilverlineConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    supported = coordinator.supported_dps
    catalog = descriptions_for_model(entry.data.get(CONF_MODEL, ""))
    async_add_entities(
        _entity_class(description)(coordinator, description)
        for description in catalog
        if set(description.dp_keys) <= supported
    )


def _entity_class(
    description: SilverlineSensorDescription,
) -> type[SilverlineSensor]:
    """Pick the entity class for a description.

    Only the lifetime energy counter needs RestoreSensor; giving the mixin
    to every diagnostic sensor would have HA persist and reload state for
    two dozen entities that recompute themselves on the next push anyway.
    """
    if description.key == ENERGY_CONSUMPTION_KEY:
        return SilverlineEnergySensor
    return SilverlineSensor


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


class SilverlineEnergySensor(SilverlineSensor, RestoreSensor):
    """Energy counter that survives restarts and reloads.

    The kWh total is integrated in memory on the coordinator, so without
    restoring it the counter would return to zero on every HA restart. For
    a TOTAL_INCREASING sensor that reads as a meter reset and puts a false
    spike into the Energy Dashboard's long-term statistics.
    """

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_data = await self.async_get_last_sensor_data()
        if last_data is None:
            return
        value = last_data.native_value
        # RestoreSensor round-trips the native value, but a state written by
        # an older version (or corrupted on disk) can still come back as a
        # non-numeric; the coordinator rejects anything it can't use.
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            self.coordinator.restore_energy_consumption_kwh(float(value))
