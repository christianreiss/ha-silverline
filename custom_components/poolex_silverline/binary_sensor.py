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
from pysilverline import DeviceState, const as tuya_const

from .coordinator import SilverlineConfigEntry, SilverlineCoordinator
from .entity import SilverlineEntity
from .util import compute_hvac_action

PARALLEL_UPDATES = 0

# Which fault bits are common enough to want on the dashboard out of the
# box. The remaining bits in FAULT_BIT_NAMES become disabled-by-default
# entities the user can turn on if they care about that specific fault.
_DEFAULT_ENABLED_FAULT_BITS: frozenset[int] = frozenset({0, 1, 2, 3, 4})


def _bit(state: DeviceState, position: int) -> bool | None:
    if state.fault is None:
        return None
    return bool(state.fault & (1 << position))


def _compressor_active(state: DeviceState) -> bool | None:
    """True iff the heat pump is actively heating or cooling right now.

    Shares compute_hvac_action with the climate entity so the
    "Compressor" binary sensor flips in lockstep with the climate card.
    """
    action = compute_hvac_action(state)
    if action is None:
        return None
    return action in (HVACAction.HEATING, HVACAction.COOLING)


@dataclass(frozen=True, kw_only=True)
class SilverlineBinarySensorDescription(BinarySensorEntityDescription):
    value_fn: Callable[[DeviceState], bool | None]
    # See SilverlineSensorDescription.dp_keys — same firmware-capability gate.
    dp_keys: tuple[str, ...]


def _fault_binary_sensor(bit: int, name: str) -> SilverlineBinarySensorDescription:
    """Build one fault-bit binary sensor description.

    Keeping this as a helper keeps the BINARY_SENSORS tuple in lock-step
    with FAULT_BIT_NAMES — adding a new bit to the library mapping
    automatically registers a corresponding entity.
    """

    def _value_fn(state: DeviceState) -> bool | None:
        # ``bit`` is closed over by reference rather than cell-bound via a
        # default arg — wrapping the call in a def fixes the type so the
        # SilverlineBinarySensorDescription field's Callable matches strictly.
        return _bit(state, bit)

    return SilverlineBinarySensorDescription(
        key=f"fault_{name}",
        translation_key=f"fault_{name}",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=bit in _DEFAULT_ENABLED_FAULT_BITS,
        value_fn=_value_fn,
        dp_keys=("13",),
    )


BINARY_SENSORS: tuple[SilverlineBinarySensorDescription, ...] = (
    SilverlineBinarySensorDescription(
        key="compressor_running",
        translation_key="compressor_running",
        device_class=BinarySensorDeviceClass.RUNNING,
        value_fn=_compressor_active,
        # Gated semantically in async_setup_entry, not via dp_keys: the only
        # authoritative compressor telemetry is actual_frequency, whose wire DP
        # is firmware-specific (108 on the standard layout; unmapped on
        # LAYOUT_V34_WFZEIYN, where DP 108 is indoor_coil_temp). On firmware
        # that never exposes it (e.g. PC-SLP090N, JetLine Selection FI 95 —
        # issue #6) _compressor_active would fall back to the
        # temp-delta-vs-setpoint heuristic, which reads "running" the instant
        # there's heating demand — including the unit's startup delay, before
        # the physical compressor has actually spun up. That made the
        # "Compressor" sensor a demand indicator masquerading as telemetry, so
        # we don't register it at all unless the model's layout maps
        # actual_frequency AND the firmware reports that DP. The climate
        # entity's hvac_action still uses the heuristic to colour the card —
        # that's demand, and acceptable there.
        dp_keys=(),
    ),
    SilverlineBinarySensorDescription(
        key="water_pump",
        translation_key="water_pump",
        device_class=BinarySensorDeviceClass.RUNNING,
        value_fn=lambda d: d.water_pump,
        dp_keys=("111",),
    ),
    *(
        _fault_binary_sensor(bit, name)
        for bit, name in sorted(tuya_const.FAULT_BIT_NAMES.items())
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SilverlineConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    supported = coordinator.supported_dps
    # actual_frequency's wire DP is firmware-specific, so the compressor
    # sensor's gate resolves through the model's layout — a raw "108" gate
    # would match LAYOUT_V34_WFZEIYN's indoor_coil_temp and register a demand
    # indicator as telemetry (see the description's comment).
    freq_dp = coordinator.client.dp_layout.actual_frequency

    def _is_supported(description: SilverlineBinarySensorDescription) -> bool:
        if description.key == "compressor_running":
            return freq_dp is not None and str(freq_dp) in supported
        return set(description.dp_keys) <= supported

    async_add_entities(
        SilverlineBinarySensor(coordinator, description)
        for description in BINARY_SENSORS
        if _is_supported(description)
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
