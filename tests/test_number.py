"""Number tests — standalone target_temperature entity with mode-aware min/max."""

from __future__ import annotations

from unittest.mock import AsyncMock

from homeassistant.components.number import (
    ATTR_MAX,
    ATTR_MIN,
    ATTR_VALUE,
    DOMAIN as NUMBER_DOMAIN,
    SERVICE_SET_VALUE,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    STATE_UNAVAILABLE,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pysilverline import DeviceState
from pytest_homeassistant_custom_component.common import MockConfigEntry

ENTITY_ID = "number.pool_heatpump_target_temperature"


async def test_entity_registers_when_dp2_present(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """state_pool_running has DP 2, so the number entity must register."""
    registry = er.async_get(hass)
    entry = registry.async_get(ENTITY_ID)
    assert entry is not None
    assert entry.config_entry_id == init_integration.entry_id


async def test_entity_skipped_when_dp2_absent(
    hass: HomeAssistant, mock_client_factory, config_entry: MockConfigEntry
) -> None:
    """A firmware variant that never reports DP 2 should not register the
    number entity at all (rather than landing it as a permanent
    `unavailable` ghost in the registry)."""
    mock_client_factory.get_status = AsyncMock(
        return_value=DeviceState.from_dps(
            {"1": True, "3": 25, "4": "Heat", "13": 0}
        )
    )
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    assert registry.async_get(ENTITY_ID) is None


async def test_native_value_reads_temp_set(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """state_pool_running sets DP 2 = 28."""
    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert float(state.state) == 28.0


async def test_native_value_updates_on_push(
    hass: HomeAssistant, mock_client_factory, init_integration: MockConfigEntry
) -> None:
    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": True, "2": 32, "3": 26, "4": "Heat", "13": 0})
    )
    await hass.async_block_till_done()
    assert float(hass.states.get(ENTITY_ID).state) == 32.0


async def test_min_max_in_heat_mode(
    hass: HomeAssistant, mock_client_factory, init_integration: MockConfigEntry
) -> None:
    """Heat: 15..40."""
    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": True, "2": 26, "4": "Heat", "13": 0})
    )
    await hass.async_block_till_done()
    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.attributes[ATTR_MIN] == 15
    assert state.attributes[ATTR_MAX] == 40


async def test_min_max_in_cool_mode(
    hass: HomeAssistant, mock_client_factory, init_integration: MockConfigEntry
) -> None:
    """Cool: 8..28."""
    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": True, "2": 20, "4": "Cool", "13": 0})
    )
    await hass.async_block_till_done()
    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.attributes[ATTR_MIN] == 8
    assert state.attributes[ATTR_MAX] == 28


async def test_min_max_in_auto_mode(
    hass: HomeAssistant, mock_client_factory, init_integration: MockConfigEntry
) -> None:
    """Auto: 8..40 (union of heat/cool)."""
    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": True, "2": 22, "4": "Auto", "13": 0})
    )
    await hass.async_block_till_done()
    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.attributes[ATTR_MIN] == 8
    assert state.attributes[ATTR_MAX] == 40


async def test_min_max_defaults_to_heat_when_off(
    hass: HomeAssistant, mock_client_factory, init_integration: MockConfigEntry
) -> None:
    """OFF: default to Heat range so the slider remains usable."""
    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": False, "2": 26, "4": "Heat", "13": 0})
    )
    await hass.async_block_till_done()
    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.attributes[ATTR_MIN] == 15
    assert state.attributes[ATTR_MAX] == 40


async def test_min_max_defaults_to_heat_on_unknown_mode(
    hass: HomeAssistant, mock_client_factory, init_integration: MockConfigEntry
) -> None:
    """A mode string the integration doesn't know about (firmware
    extension, partial push) should fall back to Heat rather than
    leaving the slider in an unbounded state."""
    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": True, "2": 26, "4": "Mystery", "13": 0})
    )
    await hass.async_block_till_done()
    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.attributes[ATTR_MIN] == 15
    assert state.attributes[ATTR_MAX] == 40


async def test_set_value_rounds_to_int_and_writes_dp2(
    hass: HomeAssistant, mock_client_factory, init_integration: MockConfigEntry
) -> None:
    """async_set_native_value rounds float → int and writes DP 2."""
    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: ENTITY_ID, ATTR_VALUE: 25.7},
        blocking=True,
    )
    mock_client_factory.set_dp.assert_awaited_with(2, 26)

    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: ENTITY_ID, ATTR_VALUE: 25.4},
        blocking=True,
    )
    mock_client_factory.set_dp.assert_awaited_with(2, 25)


async def test_set_value_integer_passes_through(
    hass: HomeAssistant, mock_client_factory, init_integration: MockConfigEntry
) -> None:
    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: ENTITY_ID, ATTR_VALUE: 30},
        blocking=True,
    )
    mock_client_factory.set_dp.assert_awaited_with(2, 30)


async def test_unavailable_when_temp_set_none(
    hass: HomeAssistant, mock_client_factory, init_integration: MockConfigEntry
) -> None:
    """When the coordinator's state omits DP 2 (push of a partial frame),
    the entity must surface as unavailable rather than rendering ``None``
    or 0 as a real setpoint."""
    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": True, "4": "Heat", "13": 0})
    )
    await hass.async_block_till_done()
    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.state == STATE_UNAVAILABLE
