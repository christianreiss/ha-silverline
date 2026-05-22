"""Setup / unload / reauth-trigger tests."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pysilverline import CannotConnect, DeviceState, InvalidAuth
from pysilverline.discovery import DiscoveryInfo
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
    PC-SLP090N) should produce: 1 climate, 1 power switch, 1 target-
    temperature number, 2 selects (preset + operating_mode), 1 fault-
    code sensor, 1 temperature-delta sensor (depends only on DPs 2+3),
    the compressor-running binary sensor (DPs 1+4 always present), and
    5 fault binary sensors — and nothing else. The 10 diagnostic
    temperature/frequency/eev/fan sensors and the water-pump binary
    sensor (DPs 101-111) must NOT register."""
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
        "binary_sensor.pool_heatpump_compressor",
        "binary_sensor.pool_heatpump_high_pressure_fault",
        "binary_sensor.pool_heatpump_low_pressure_fault",
        "binary_sensor.pool_heatpump_water_flow_fault",
        "climate.pool_heatpump",
        "number.pool_heatpump_target_temperature",
        "select.pool_heatpump_operating_mode",
        "select.pool_heatpump_preset",
        "sensor.pool_heatpump_fault_code",
        # Runtime accumulator: only needs DPs 1+4 (both present on the
        # minimal firmware), so it registers even on PC-SLP090N.
        "sensor.pool_heatpump_runtime_today",
        "sensor.pool_heatpump_temperature_delta",
        "switch.pool_heatpump_power",
    ]


async def test_full_firmware_registers_everything(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """When the device exposes the full DP set (state_pool_running has
    1-13 + 101-111), all 25 entities register. Guards against the
    capability filter accidentally dropping entities on full firmware.

    The 25 count: 1 climate, 13 sensors (10 diagnostic + fault_code +
    temperature_delta + runtime_today), 7 binary_sensors (water_pump +
    5 fault bits + compressor_running), 1 switch (power), 1 number
    (target_temperature), 2 selects (preset_mode + operating_mode)."""
    registry = er.async_get(hass)
    entity_ids = sorted(
        e.entity_id
        for e in registry.entities.values()
        if e.config_entry_id == init_integration.entry_id
    )
    assert len(entity_ids) == 25


async def test_async_setup_starts_discovery_task(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """async_setup spawns a background discovery listener and tracks it
    on hass.data[DOMAIN] so duplicate setup_entry calls don't re-spawn it."""
    from custom_components.poolex_silverline.const import DOMAIN
    task = hass.data[DOMAIN]["_discovery_task"]
    assert task is not None
    assert not task.done()


async def test_discovery_loop_suppresses_duplicate_ip_but_refires_on_change(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A repeat broadcast from a known device on the same IP must be
    suppressed (devices announce every ~25s and HA does not need to be
    told twice). But a broadcast from the same device on a NEW IP must
    re-fire the discovery flow so the existing entry's CONF_HOST can
    be rewritten — that is the whole purpose of the IP-update path,
    and the original unbounded-set dedup gated it behind the very
    first sighting per HA process."""
    from custom_components.poolex_silverline import async_setup
    from custom_components.poolex_silverline.const import DOMAIN

    queue: asyncio.Queue[DiscoveryInfo] = asyncio.Queue()

    async def _mock_discover():
        while True:
            yield await queue.get()

    monkeypatch.setattr(
        "custom_components.poolex_silverline.discover", _mock_discover
    )

    init_calls: list[dict] = []

    async def _spy_init(domain, *, context=None, data=None):
        init_calls.append(data)
        return {"type": "abort", "reason": "test"}

    monkeypatch.setattr(hass.config_entries.flow, "async_init", _spy_init)

    assert await async_setup(hass, {})

    async def _drain(info: DiscoveryInfo) -> None:
        await queue.put(info)
        # Yield to the loop a few times so the discovery task picks the
        # item up, fires the (spied) flow.async_init, and the resulting
        # task settles before we inspect init_calls.
        for _ in range(3):
            await asyncio.sleep(0)
        await hass.async_block_till_done()

    try:
        await _drain(DiscoveryInfo(device_id="dev1", ip="10.0.0.1"))
        assert len(init_calls) == 1
        assert init_calls[0]["ip"] == "10.0.0.1"

        # Repeat on the same IP → suppressed.
        await _drain(DiscoveryInfo(device_id="dev1", ip="10.0.0.1"))
        assert len(init_calls) == 1

        # New IP for the known device → re-fires.
        await _drain(DiscoveryInfo(device_id="dev1", ip="10.0.0.2"))
        assert len(init_calls) == 2
        assert init_calls[1]["ip"] == "10.0.0.2"

        # A different device on its own IP fires independently.
        await _drain(DiscoveryInfo(device_id="dev2", ip="10.0.0.3"))
        assert len(init_calls) == 3
        assert init_calls[2]["device_id"] == "dev2"
    finally:
        task = hass.data[DOMAIN]["_discovery_task"]
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
