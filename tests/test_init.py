"""Setup / unload / reauth-trigger tests."""

from __future__ import annotations

from unittest.mock import AsyncMock

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pysilverline import CannotConnect, DeviceState, InvalidAuth
from pytest_homeassistant_custom_component.common import MockConfigEntry


async def test_setup_and_unload(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    assert init_integration.state is ConfigEntryState.LOADED
    assert await hass.config_entries.async_unload(init_integration.entry_id)
    await hass.async_block_till_done()
    assert init_integration.state is ConfigEntryState.NOT_LOADED


async def test_setup_retry_on_connect_failure(
    hass: HomeAssistant, mock_client_factory, config_entry: MockConfigEntry
) -> None:
    mock_client_factory.connect.side_effect = CannotConnect("offline")
    config_entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_setup_triggers_reauth_on_invalid_key(
    hass: HomeAssistant, mock_client_factory, config_entry: MockConfigEntry
) -> None:
    mock_client_factory.get_status = AsyncMock(side_effect=InvalidAuth("bad"))
    config_entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(config_entry.domain)
    assert any(flow["context"].get("source") == "reauth" for flow in flows)


async def test_firmware_capability_filter_skips_missing_dps(
    hass: HomeAssistant,
    mock_client_factory,
    config_entry: MockConfigEntry,
    state_minimal_firmware: DeviceState,
) -> None:
    """A firmware that only emits DPs 1,2,3,4,13 (verified live on
    PC-SLP090N) should produce: 1 climate, 1 power switch, 1 fault-code
    sensor, and 5 fault binary sensors — and nothing else. The 10
    diagnostic temperature/frequency/eev/fan sensors and the water-pump
    binary sensor (DPs 101-111) must NOT register."""
    mock_client_factory.get_status = AsyncMock(return_value=state_minimal_firmware)
    mock_client_factory.state = state_minimal_firmware
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    entity_ids = sorted(
        e.entity_id
        for e in registry.entities.values()
        if e.config_entry_id == config_entry.entry_id
    )
    assert entity_ids == [
        "binary_sensor.pool_heatpump_antifreeze_fault",
        "binary_sensor.pool_heatpump_communication_fault",
        "binary_sensor.pool_heatpump_high_pressure_fault",
        "binary_sensor.pool_heatpump_low_pressure_fault",
        "binary_sensor.pool_heatpump_water_flow_fault",
        "climate.pool_heatpump",
        "sensor.pool_heatpump_fault_code",
        "switch.pool_heatpump_power",
    ]


async def test_full_firmware_registers_everything(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """When the device exposes the full DP set (state_pool_running has
    1-13 + 101-111), all 19 entities register. Guards against the
    capability filter accidentally dropping entities on full firmware."""
    registry = er.async_get(hass)
    entity_ids = sorted(
        e.entity_id
        for e in registry.entities.values()
        if e.config_entry_id == init_integration.entry_id
    )
    assert len(entity_ids) == 19


async def test_async_setup_starts_discovery_task(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """async_setup spawns a background discovery listener and tracks it
    on hass.data[DOMAIN] so duplicate setup_entry calls don't re-spawn it."""
    from custom_components.poolex_silverline.const import DOMAIN
    task = hass.data[DOMAIN]["_discovery_task"]
    assert task is not None
    assert not task.done()
