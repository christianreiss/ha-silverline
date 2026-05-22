"""Binary sensor tests — water pump and decoded fault bits."""

from __future__ import annotations

from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from pysilverline import DeviceState

COMPRESSOR = "binary_sensor.pool_heatpump_compressor"


async def test_water_pump(hass: HomeAssistant, init_integration) -> None:
    assert hass.states.get("binary_sensor.pool_heatpump_water_pump").state == STATE_ON


async def test_fault_bits(hass: HomeAssistant, init_integration) -> None:
    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(DeviceState.from_dps({"13": 0b00010101}))
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.pool_heatpump_water_flow_fault").state == STATE_ON
    assert hass.states.get("binary_sensor.pool_heatpump_antifreeze_fault").state == STATE_OFF
    assert hass.states.get("binary_sensor.pool_heatpump_high_pressure_fault").state == STATE_ON
    assert hass.states.get("binary_sensor.pool_heatpump_low_pressure_fault").state == STATE_OFF
    assert hass.states.get("binary_sensor.pool_heatpump_communication_fault").state == STATE_ON


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
        DeviceState.from_dps(
            {"1": True, "4": "Heat", "2": 28, "3": 22, "108": 0}
        )
    )
    await hass.async_block_till_done()
    assert hass.states.get(COMPRESSOR).state == STATE_OFF


async def test_compressor_on_when_actual_frequency_positive(
    hass: HomeAssistant, init_integration
) -> None:
    """DP 108 > 0 wins over the temp-delta heuristic — even at the setpoint."""
    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(
        DeviceState.from_dps(
            {"1": True, "4": "Heat", "2": 26, "3": 26, "108": 50}
        )
    )
    await hass.async_block_till_done()
    assert hass.states.get(COMPRESSOR).state == STATE_ON
