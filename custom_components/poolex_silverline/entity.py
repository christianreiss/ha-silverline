"""Shared base entity for Poolex Silverline platforms."""

from __future__ import annotations

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from pysilverline import InvalidAuth, SilverlineError

from .const import CONF_MODEL, DEVICE_PROFILES, DOMAIN, MANUFACTURER, MODEL
from .coordinator import SilverlineCoordinator


class SilverlineEntity(CoordinatorEntity[SilverlineCoordinator]):
    """Base entity that wires up DeviceInfo from coordinator state."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: SilverlineCoordinator) -> None:
        super().__init__(coordinator)
        device_id = coordinator.device_id
        model_key = coordinator.config_entry.data.get(CONF_MODEL, "")
        profile = DEVICE_PROFILES.get(model_key)
        model_name = profile.display_name if profile is not None else MODEL
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            manufacturer=MANUFACTURER,
            model=model_name,
            name="Pool Heatpump",
            serial_number=device_id,
        )

    async def _write_dps(self, dps: dict[int, bool | int | str]) -> None:
        """Write one or more DPs, translating wire errors to HA errors.

        Shared by every write-capable platform (climate, select, switch,
        number). On success, the optimistic merge pushes the new values
        into the coordinator so entities reflect the change immediately —
        the device's STATUS push within ~200 ms overlays the authoritative
        state on top.
        """
        try:
            await self.coordinator.client.set_multiple(dps)
        except InvalidAuth as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="auth_failed",
            ) from err
        # Catch-all for the remaining wire errors — CannotConnect, a
        # device-side write rejection (non-zero CONTROL ack, a bare
        # SilverlineError), and ProtocolError all subclass SilverlineError.
        # Must stay AFTER the InvalidAuth clause, which does too.
        except SilverlineError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="set_failed",
                translation_placeholders={"reason": str(err)},
            ) from err
        if self.coordinator.data is not None:
            merged = self.coordinator.data.merge(
                {str(k): v for k, v in dps.items()},
                layout=self.coordinator.client.dp_layout,
            )
            self.coordinator.async_set_updated_data(merged)
