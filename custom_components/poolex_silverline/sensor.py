"""Diagnostic sensors for the Poolex Silverline."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_MODEL
from .coordinator import SilverlineConfigEntry, SilverlineCoordinator
from .entity import SilverlineEntity
from .sensor_descriptions import SilverlineSensorDescription, descriptions_for_model

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
