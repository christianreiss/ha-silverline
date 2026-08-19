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
from pysilverline import const as tuya_const

from .coordinator import SilverlineConfigEntry, SilverlineCoordinator
from .entity import SilverlineEntity
from .util import compute_hvac_action

PARALLEL_UPDATES = 0

# Which fault names are common enough to want on the dashboard out of the
# box, keyed by symbolic name (not bit position) since the same name can
# sit on a different bit across fault tables — e.g. "water_flow" is bit 0
# in FAULT_BIT_NAMES (DP 13) but bit 8 in NANO_5KW_FAULT_BIT_NAMES (DP 21).
# The remaining names become disabled-by-default entities the user can turn
# on if they care about that specific fault.
_DEFAULT_ENABLED_FAULT_NAMES: frozenset[str] = frozenset(
    {"water_flow", "antifreeze", "high_pressure", "low_pressure", "communication"}
)


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
    # Fault-bit descriptions only: the wire DP their bit table assumes
    # (13 for FAULT_BIT_NAMES, 21 for NANO_5KW_FAULT_BIT_NAMES). None for
    # non-fault descriptions. Gated in async_setup_entry against the
    # model's actual dp_layout.fault so two models that both happen to
    # expose a DP numerically equal to this one (e.g. a future firmware
    # with an unrelated DP 21) can never mis-instantiate each other's bit
    # table — dp_keys alone can't tell "has this DP" from "this DP means
    # what I think it means".
    required_fault_dp: int | None = None


def _fault_binary_sensor(
    bit: int, name: str, *, dp: int
) -> SilverlineBinarySensorDescription:
    """Build one fault-bit binary sensor description for fault DP ``dp``.

    Keeping this as a helper keeps the BINARY_SENSORS tuple in lock-step
    with the bit-name tables — adding a new bit to either mapping
    automatically registers a corresponding entity.
    """

    def _value_fn(state: DeviceState) -> bool | None:
        # ``bit`` is closed over by reference rather than cell-bound via a
        # default arg — wrapping the call in a def fixes the type so the
        # SilverlineBinarySensorDescription field's Callable matches strictly.
        # state.fault is sourced from whichever DP the model's layout maps
        # to `fault` (13 or 21), so the same _bit() helper works for both.
        return _bit(state, bit)

    return SilverlineBinarySensorDescription(
        key=f"fault_{name}",
        translation_key=f"fault_{name}",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=name in _DEFAULT_ENABLED_FAULT_NAMES,
        value_fn=_value_fn,
        dp_keys=(str(dp),),
        required_fault_dp=dp,
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
    SilverlineBinarySensorDescription(
        key="defrosting",
        translation_key="defrosting",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.defrosting,
        # Nano Fi 3kW/5kW only (DP 115, issue #19) — no other layout maps
        # this field yet, so dp_keys alone is a safe gate.
        dp_keys=("115",),
    ),
    *(
        _fault_binary_sensor(bit, name, dp=tuya_const.DP_FAULT)
        for bit, name in sorted(tuya_const.FAULT_BIT_NAMES.items())
    ),
    *(
        _fault_binary_sensor(bit, name, dp=21)
        for bit, name in sorted(tuya_const.NANO_5KW_FAULT_BIT_NAMES.items())
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
        if description.required_fault_dp is not None:
            return (
                coordinator.client.dp_layout.fault == description.required_fault_dp
                and set(description.dp_keys) <= supported
            )
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
