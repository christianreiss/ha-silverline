"""Switch platform for the Poolex Silverline — DP 1 (power) as a standalone toggle."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from pysilverline import DeviceState, const as tuya_const

from .coordinator import SilverlineConfigEntry, SilverlineCoordinator
from .entity import SilverlineEntity

# Write-capable: serialize per entity so chained automation steps don't
# race a stale optimistic merge into the coordinator. pysilverline's
# _send_lock already serializes the underlying socket writes, so this
# is belt-and-braces — and brings switch in line with climate/select.
PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class SilverlineSwitchDescription(SwitchEntityDescription):
    """Switch description that pulls a bool from DeviceState."""

    value_fn: Callable[[DeviceState], bool | None]
    # See SilverlineSensorDescription.dp_keys — same firmware-capability gate.
    dp_keys: tuple[str, ...]


SWITCHES: tuple[SilverlineSwitchDescription, ...] = (
    SilverlineSwitchDescription(
        key="power",
        translation_key="power",
        value_fn=lambda d: d.power,
        dp_keys=("1",),
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
        SilverlineSwitch(coordinator, description)
        for description in SWITCHES
        if set(description.dp_keys) <= supported
    )


class SilverlineSwitch(SilverlineEntity, SwitchEntity):
    entity_description: SilverlineSwitchDescription

    def __init__(
        self,
        coordinator: SilverlineCoordinator,
        description: SilverlineSwitchDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.device_id}_{description.key}"

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

    async def async_turn_on(self, **kwargs: object) -> None:
        await self._write_dps({tuya_const.DP_POWER: True})

    async def async_turn_off(self, **kwargs: object) -> None:
        await self._write_dps({tuya_const.DP_POWER: False})
