"""Switch tests — standalone DP 1 (power) toggle."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from homeassistant.components.switch import (
    DOMAIN as SWITCH_DOMAIN,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
)
from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pysilverline import CannotConnect, DeviceState, InvalidAuth
from pytest_homeassistant_custom_component.common import MockConfigEntry

ENTITY_ID = "switch.pool_heatpump_power"


async def test_switch_registers_when_dp1_supported(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """DP 1 is in the default state fixture, so the switch entity exists."""
    registry = er.async_get(hass)
    entry = registry.async_get(ENTITY_ID)
    assert entry is not None
    assert entry.config_entry_id == init_integration.entry_id


async def test_is_on_reflects_power(
    hass: HomeAssistant, mock_client_factory, init_integration: MockConfigEntry
) -> None:
    """is_on tracks DeviceState.power across coordinator updates."""
    # state_pool_running has DP 1 == True
    assert hass.states.get(ENTITY_ID).state == STATE_ON

    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": False, "4": "Heat", "13": 0})
    )
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY_ID).state == STATE_OFF


async def test_turn_on_writes_dp1_true(
    hass: HomeAssistant, mock_client_factory, init_integration: MockConfigEntry
) -> None:
    coordinator = init_integration.runtime_data
    # Start OFF so the turn_on transition is observable.
    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": False, "4": "Heat", "13": 0})
    )
    await hass.async_block_till_done()

    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: ENTITY_ID},
        blocking=True,
    )
    mock_client_factory.set_dp.assert_awaited_with(1, True)
    # Optimistic merge should flip the state immediately.
    assert hass.states.get(ENTITY_ID).state == STATE_ON


async def test_turn_off_writes_dp1_false(
    hass: HomeAssistant, mock_client_factory, init_integration: MockConfigEntry
) -> None:
    # state_pool_running starts ON.
    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_OFF,
        {ATTR_ENTITY_ID: ENTITY_ID},
        blocking=True,
    )
    mock_client_factory.set_dp.assert_awaited_with(1, False)
    assert hass.states.get(ENTITY_ID).state == STATE_OFF


async def test_switch_unavailable_when_power_none(
    hass: HomeAssistant, mock_client_factory, init_integration: MockConfigEntry
) -> None:
    """If the device emits a state without DP 1, the switch goes unavailable."""
    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(DeviceState.from_dps({"4": "Heat", "13": 0}))
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY_ID).state == STATE_UNAVAILABLE


async def test_switch_missing_when_dp1_not_supported(
    hass: HomeAssistant,
    mock_client_factory,
    config_entry: MockConfigEntry,
) -> None:
    """If the firmware's first poll doesn't include DP 1, the switch must not register."""
    # Build a state that omits DP 1 entirely.
    no_power_state = DeviceState.from_dps({"4": "Heat", "13": 0})
    mock_client_factory.get_status = AsyncMock(return_value=no_power_state)
    mock_client_factory.state = no_power_state
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    assert registry.async_get(ENTITY_ID) is None


async def test_turn_on_surfaces_cannot_connect_as_homeassistant_error(
    hass: HomeAssistant, mock_client_factory, init_integration: MockConfigEntry
) -> None:
    mock_client_factory.set_dp.side_effect = CannotConnect("offline")
    with pytest.raises(HomeAssistantError) as exc:
        await hass.services.async_call(
            SWITCH_DOMAIN,
            SERVICE_TURN_ON,
            {ATTR_ENTITY_ID: ENTITY_ID},
            blocking=True,
        )
    assert exc.value.translation_key == "set_failed"


async def test_turn_off_surfaces_invalid_auth_as_homeassistant_error(
    hass: HomeAssistant, mock_client_factory, init_integration: MockConfigEntry
) -> None:
    mock_client_factory.set_dp.side_effect = InvalidAuth("rotated")
    with pytest.raises(HomeAssistantError) as exc:
        await hass.services.async_call(
            SWITCH_DOMAIN,
            SERVICE_TURN_OFF,
            {ATTR_ENTITY_ID: ENTITY_ID},
            blocking=True,
        )
    assert exc.value.translation_key == "auth_failed"
