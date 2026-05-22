"""Sensor tests — value_fn results, fault decoding, availability."""

from __future__ import annotations

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pysilverline import DeviceState
from syrupy.assertion import SnapshotAssertion


async def test_diagnostic_sensors_populate(
    hass: HomeAssistant, init_integration
) -> None:
    state = hass.states.get("sensor.pool_heatpump_water_inlet_temperature")
    assert state is not None
    assert state.state == "26"

    state = hass.states.get("sensor.pool_heatpump_water_outlet_temperature")
    assert state is not None
    assert state.state == "28"

    state = hass.states.get("sensor.pool_heatpump_compressor_actual_frequency")
    assert state is not None
    assert state.state == "63"


async def test_fault_code_decoded_to_enum_state(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    coordinator = init_integration.runtime_data

    coordinator.async_set_updated_data(DeviceState.from_dps({"13": 0}))
    await hass.async_block_till_done()
    assert hass.states.get("sensor.pool_heatpump_fault_code").state == "none"

    coordinator.async_set_updated_data(DeviceState.from_dps({"13": 1}))
    await hass.async_block_till_done()
    assert hass.states.get("sensor.pool_heatpump_fault_code").state == "E03"

    coordinator.async_set_updated_data(DeviceState.from_dps({"13": 2}))
    await hass.async_block_till_done()
    assert hass.states.get("sensor.pool_heatpump_fault_code").state == "E04"

    coordinator.async_set_updated_data(DeviceState.from_dps({"13": 1 << 25}))
    await hass.async_block_till_done()
    assert hass.states.get("sensor.pool_heatpump_fault_code").state == "unknown"


async def test_sensor_unavailable_when_dp_missing(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    """If the firmware doesn't expose DPs 101–110, those sensors must
    surface as unavailable rather than blowing up the integration."""
    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": True, "4": "Heat", "3": 25, "13": 0})
    )
    await hass.async_block_till_done()
    state = hass.states.get("sensor.pool_heatpump_water_inlet_temperature")
    assert state is not None
    assert state.state == STATE_UNAVAILABLE


async def test_entity_inventory_snapshot(
    hass: HomeAssistant,
    init_integration,
    snapshot: SnapshotAssertion,
) -> None:
    """Snapshot the entity registry + canonical "powered + heating" states.

    Catches regressions where: an entity is renamed/removed, a default
    state changes shape, a unit/device_class is altered, or an attribute
    appears/disappears unexpectedly. Update with --snapshot-update if the
    change is intentional."""
    registry = er.async_get(hass)
    entries = sorted(
        (e for e in registry.entities.values() if e.config_entry_id == init_integration.entry_id),
        key=lambda e: e.entity_id,
    )
    assert {e.entity_id: registry.async_get(e.entity_id) for e in entries} == snapshot(
        name="entity_registry"
    )
    # Only entities that are actually enabled produce a state; some
    # diagnostic DPs are disabled-by-default.
    states = sorted(
        (
            s
            for e in entries
            if (s := hass.states.get(e.entity_id)) is not None
        ),
        key=lambda s: s.entity_id,
    )
    assert states == snapshot(name="entity_states")
