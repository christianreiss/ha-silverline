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


def _fault_binary_sensor(
    bit: int, name: str, *, dp: int
) -> SilverlineBinarySensorDescription:
    """Build one fault-bit binary sensor description for fault DP ``dp``.

    Called per-model from async_setup_entry against that model's own
    ``DpLayout.fault_table``, so the entity set follows the firmware's bit
    layout rather than a module-level default. It cannot be a static tuple:
    the classic family and Full Inverter firmware both report on DP 13 and
    disagree about what bit 8 means, so selecting a table by DP number
    registers both and collides on unique_id (issue #19).
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
    )


#: Model-independent binary sensors. The fault-bit entities are NOT here —
#: they are built per-model in async_setup_entry from the layout's fault table.
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
        # Gated through the layout, not the raw DP, for the same reason as
        # compressor_running above: DP 111 is the circulation pump on the
        # standard/v3.4 families but the main EEV opening on the Nano Fi
        # (issue #19), where `water_pump` is unmapped. A raw "111" gate would
        # register a "Water pump" that is really "EEV is open".
        key="water_pump",
        translation_key="water_pump",
        device_class=BinarySensorDeviceClass.RUNNING,
        value_fn=lambda d: d.water_pump,
        dp_keys=(),
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

    pump_dp = coordinator.client.dp_layout.water_pump

    def _is_supported(description: SilverlineBinarySensorDescription) -> bool:
        if description.key == "compressor_running":
            return freq_dp is not None and str(freq_dp) in supported
        if description.key == "water_pump":
            return pump_dp is not None and str(pump_dp) in supported
        return set(description.dp_keys) <= supported

    # One fault-bit entity per bit this firmware's own table names. The table
    # travels with the layout because the fault DP number does not identify it
    # — see _fault_binary_sensor.
    layout = coordinator.client.dp_layout
    fault_descriptions: tuple[SilverlineBinarySensorDescription, ...] = ()
    if layout.fault is not None:
        fault_descriptions = tuple(
            _fault_binary_sensor(bit, name, dp=layout.fault)
            for bit, name in sorted(layout.fault_table.names.items())
        )

    async_add_entities(
        SilverlineBinarySensor(coordinator, description)
        for description in (*BINARY_SENSORS, *fault_descriptions)
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
