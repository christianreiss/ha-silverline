"""Switch platform for the Poolex Silverline — DP 1 (power) as a standalone toggle."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from pysilverline import CannotConnect, DeviceState, InvalidAuth, const as tuya_const

from .const import DOMAIN
from .coordinator import SilverlineConfigEntry, SilverlineCoordinator
from .entity import SilverlineEntity

PARALLEL_UPDATES = 0


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

    async def async_turn_on(self, **kwargs: object) -> None:
        await self._write(True)

    async def async_turn_off(self, **kwargs: object) -> None:
        await self._write(False)

    async def _write(self, value: bool) -> None:
        try:
            await self.coordinator.client.set_dp(tuya_const.DP_POWER, value)
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
        # Optimistic merge so the entity flips immediately; the device's
        # subsequent STATUS push will overlay the authoritative value.
        if self.coordinator.data is not None:
            merged = self.coordinator.data.merge({str(tuya_const.DP_POWER): value})
            self.coordinator.async_set_updated_data(merged)
