"""Binary sensor tests — water pump and decoded fault bits."""

from __future__ import annotations

from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pysilverline import DeviceState

COMPRESSOR = "binary_sensor.pool_heatpump_compressor"


async def test_water_pump(hass: HomeAssistant, init_integration) -> None:
    assert hass.states.get("binary_sensor.pool_heatpump_water_pump").state == STATE_ON


async def test_fault_bits(hass: HomeAssistant, init_integration) -> None:
    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(DeviceState.from_dps({"13": 0b00010101}))
    await hass.async_block_till_done()
    assert (
        hass.states.get("binary_sensor.pool_heatpump_water_flow_fault").state
        == STATE_ON
    )
    assert (
        hass.states.get("binary_sensor.pool_heatpump_antifreeze_fault").state
        == STATE_OFF
    )
    assert (
        hass.states.get("binary_sensor.pool_heatpump_high_pressure_fault").state
        == STATE_ON
    )
    assert (
        hass.states.get("binary_sensor.pool_heatpump_low_pressure_fault").state
        == STATE_OFF
    )
    assert (
        hass.states.get("binary_sensor.pool_heatpump_communication_fault").state
        == STATE_ON
    )


async def test_compressor_on_when_heating_below_target(
    hass: HomeAssistant, init_integration
) -> None:
    """Heat mode, current<target, no DP 108 → infer HEATING from temp delta."""
    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": True, "4": "Heat", "2": 28, "3": 26})
    )
    await hass.async_block_till_done()
    assert hass.states.get(COMPRESSOR).state == STATE_ON


async def test_compressor_off_when_idle_at_target(
    hass: HomeAssistant, init_integration
) -> None:
    """Heat mode, current>=target, no DP 108 → IDLE → compressor off."""
    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": True, "4": "Heat", "2": 26, "3": 28})
    )
    await hass.async_block_till_done()
    assert hass.states.get(COMPRESSOR).state == STATE_OFF


async def test_compressor_off_when_power_false(
    hass: HomeAssistant, init_integration
) -> None:
    """Device off → hvac_action OFF → compressor off, no matter the temps."""
    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": False, "4": "Heat", "2": 28, "3": 22})
    )
    await hass.async_block_till_done()
    assert hass.states.get(COMPRESSOR).state == STATE_OFF


async def test_compressor_off_when_actual_frequency_zero(
    hass: HomeAssistant, init_integration
) -> None:
    """DP 108 == 0 is authoritative even if temp delta would say heating."""
    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": True, "4": "Heat", "2": 28, "3": 22, "108": 0})
    )
    await hass.async_block_till_done()
    assert hass.states.get(COMPRESSOR).state == STATE_OFF


async def test_compressor_on_when_actual_frequency_positive(
    hass: HomeAssistant, init_integration
) -> None:
    """DP 108 > 0 wins over the temp-delta heuristic — even at the setpoint."""
    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": True, "4": "Heat", "2": 26, "3": 26, "108": 50})
    )
    await hass.async_block_till_done()
    assert hass.states.get(COMPRESSOR).state == STATE_ON


async def test_all_ten_fault_bits_have_entities(
    hass: HomeAssistant, init_integration
) -> None:
    """All 10 bits in FAULT_BIT_NAMES get a registry entry: the first 5
    are enabled by default (common operational faults), the remaining 5
    are disabled by default (rarely-fired sensor / inverter faults the
    user can opt-in to)."""
    registry = er.async_get(hass)
    by_uid = {
        e.unique_id: e
        for e in registry.entities.values()
        if e.config_entry_id == init_integration.entry_id
        and e.platform == "poolex_silverline"
        and e.domain == "binary_sensor"
    }
    device = init_integration.unique_id
    enabled = {
        "fault_water_flow",
        "fault_antifreeze",
        "fault_high_pressure",
        "fault_low_pressure",
        "fault_communication",
    }
    disabled = {
        "fault_inverter_comms",
        "fault_inlet_sensor",
        "fault_outlet_sensor",
        "fault_defrost_sensor",
        "fault_coil_sensor",
    }
    for key in enabled:
        entry = by_uid.get(f"{device}_{key}")
        assert entry is not None, f"missing registry entry for {key}"
        assert entry.disabled_by is None, f"{key} should be enabled by default"
    for key in disabled:
        entry = by_uid.get(f"{device}_{key}")
        assert entry is not None, f"missing registry entry for {key}"
        assert entry.disabled_by is not None, f"{key} should be disabled by default"
