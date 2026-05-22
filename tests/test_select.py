"""Standalone select entities: preset_mode and operating_mode."""

from __future__ import annotations

import asyncio

import pytest
from homeassistant.components.select import (
    ATTR_OPTION,
    DOMAIN as SELECT_DOMAIN,
    SERVICE_SELECT_OPTION,
)
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from pysilverline import DeviceState

PRESET_ENTITY = "select.pool_heatpump_preset"
OPMODE_ENTITY = "select.pool_heatpump_operating_mode"


async def test_select_entities_register_when_dps_present(
    hass: HomeAssistant, init_integration
) -> None:
    """Both selects show up in the registry on the full-firmware fixture
    (DPs 1 + 4 are present in state_pool_running)."""
    registry = er.async_get(hass)
    entity_ids = {
        e.entity_id
        for e in registry.entities.values()
        if e.config_entry_id == init_integration.entry_id and e.domain == "select"
    }
    assert entity_ids == {PRESET_ENTITY, OPMODE_ENTITY}


# ---------------------------------------------------------------------------
# operating_mode: current_option mirrors hvac_mode across every combination
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "dps,expected",
    [
        ({"1": False, "4": "Heat"}, "off"),
        ({"1": True, "4": "Heat"}, "heat"),
        ({"1": True, "4": "BoostHeat"}, "heat"),
        ({"1": True, "4": "SilentHeat"}, "heat"),
        ({"1": True, "4": "Cool"}, "cool"),
        ({"1": True, "4": "BoostCool"}, "cool"),
        ({"1": True, "4": "SilentCool"}, "cool"),
        ({"1": True, "4": "Auto"}, "heat_cool"),
    ],
)
async def test_operating_mode_current_option(
    hass: HomeAssistant,
    mock_client_factory,
    init_integration,
    dps: dict[str, str | bool],
    expected: str,
) -> None:
    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(DeviceState.from_dps({"3": 25, **dps}))
    await hass.async_block_till_done()
    state = hass.states.get(OPMODE_ENTITY)
    assert state is not None
    assert state.state == expected


# ---------------------------------------------------------------------------
# preset_mode: current_option follows DP-4 prefix; "none" when Auto/OFF
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "dps,expected",
    [
        ({"1": False, "4": "Heat"}, "none"),
        ({"1": True, "4": "Heat"}, "none"),
        ({"1": True, "4": "BoostHeat"}, "boost"),
        ({"1": True, "4": "SilentHeat"}, "eco"),
        ({"1": True, "4": "Cool"}, "none"),
        ({"1": True, "4": "BoostCool"}, "boost"),
        ({"1": True, "4": "SilentCool"}, "eco"),
        ({"1": True, "4": "Auto"}, "none"),
    ],
)
async def test_preset_mode_current_option(
    hass: HomeAssistant,
    mock_client_factory,
    init_integration,
    dps: dict[str, str | bool],
    expected: str,
) -> None:
    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(DeviceState.from_dps({"3": 25, **dps}))
    await hass.async_block_till_done()
    state = hass.states.get(PRESET_ENTITY)
    assert state is not None
    assert state.state == expected


# ---------------------------------------------------------------------------
# operating_mode: async_select_option writes the right DPs
# ---------------------------------------------------------------------------


async def test_operating_mode_select_off_writes_dp1_false(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: OPMODE_ENTITY, ATTR_OPTION: "off"},
        blocking=True,
    )
    mock_client_factory.set_multiple.assert_awaited_with({1: False})


async def test_operating_mode_select_heat_writes_dp1_true_and_mode_and_sleeps(
    hass: HomeAssistant, mock_client_factory, init_integration, monkeypatch
) -> None:
    """async_select_option('heat') writes {1:True, 4:'Heat'} and then
    awaits the _MODE_TRANSITION_SETTLE sleep so a chained call doesn't
    race the device's per-mode-memory restore push."""
    import custom_components.poolex_silverline.select as select_mod

    recorded: list[float] = []
    real_sleep = asyncio.sleep

    async def fake_sleep(delay: float) -> None:
        recorded.append(delay)
        await real_sleep(0)

    monkeypatch.setattr(select_mod.asyncio, "sleep", fake_sleep)

    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: OPMODE_ENTITY, ATTR_OPTION: "heat"},
        blocking=True,
    )
    mock_client_factory.set_multiple.assert_awaited_with({1: True, 4: "Heat"})
    assert select_mod._MODE_TRANSITION_SETTLE in recorded


async def test_operating_mode_select_cool_writes_dp1_true_and_cool(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: OPMODE_ENTITY, ATTR_OPTION: "cool"},
        blocking=True,
    )
    mock_client_factory.set_multiple.assert_awaited_with({1: True, 4: "Cool"})


async def test_operating_mode_select_heat_cool_writes_auto(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: OPMODE_ENTITY, ATTR_OPTION: "heat_cool"},
        blocking=True,
    )
    mock_client_factory.set_multiple.assert_awaited_with({1: True, 4: "Auto"})


async def test_operating_mode_select_off_does_not_sleep(
    hass: HomeAssistant, mock_client_factory, init_integration, monkeypatch
) -> None:
    """The OFF path doesn't trigger the device's per-mode restore, so
    skip the settle sleep — keep the call snappy."""
    import custom_components.poolex_silverline.select as select_mod

    recorded: list[float] = []
    real_sleep = asyncio.sleep

    async def fake_sleep(delay: float) -> None:
        recorded.append(delay)
        await real_sleep(0)

    monkeypatch.setattr(select_mod.asyncio, "sleep", fake_sleep)

    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: OPMODE_ENTITY, ATTR_OPTION: "off"},
        blocking=True,
    )
    assert select_mod._MODE_TRANSITION_SETTLE not in recorded


# ---------------------------------------------------------------------------
# preset_mode: async_select_option respects the current direction
# ---------------------------------------------------------------------------


async def test_preset_boost_during_heat_writes_boostheat(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    """While the device is in Heat, picking 'boost' writes the
    BoostHeat enum on DP 4 — exact same DP write as the climate entity."""
    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(DeviceState.from_dps({"1": True, "4": "Heat"}))
    await hass.async_block_till_done()

    mock_client_factory.set_multiple.reset_mock()
    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: PRESET_ENTITY, ATTR_OPTION: "boost"},
        blocking=True,
    )
    mock_client_factory.set_multiple.assert_awaited_with({4: "BoostHeat"})


async def test_preset_eco_during_cool_writes_silentcool(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(DeviceState.from_dps({"1": True, "4": "Cool"}))
    await hass.async_block_till_done()

    mock_client_factory.set_multiple.reset_mock()
    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: PRESET_ENTITY, ATTR_OPTION: "eco"},
        blocking=True,
    )
    mock_client_factory.set_multiple.assert_awaited_with({4: "SilentCool"})


async def test_preset_during_auto_raises(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    """In Auto, both presets are device-meaningless; the select rejects
    so the UI surfaces a clear error rather than swallowing the click."""
    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(DeviceState.from_dps({"1": True, "4": "Auto"}))
    await hass.async_block_till_done()

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            SELECT_DOMAIN,
            SERVICE_SELECT_OPTION,
            {ATTR_ENTITY_ID: PRESET_ENTITY, ATTR_OPTION: "boost"},
            blocking=True,
        )


async def test_preset_while_off_is_noop(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    """Picking a preset while OFF stores no pending state on the select
    (the climate entity owns OFF→ON memory). The select just no-ops."""
    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": False, "4": "Heat"})
    )
    await hass.async_block_till_done()
    mock_client_factory.set_multiple.reset_mock()

    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: PRESET_ENTITY, ATTR_OPTION: "boost"},
        blocking=True,
    )
    mock_client_factory.set_multiple.assert_not_called()
