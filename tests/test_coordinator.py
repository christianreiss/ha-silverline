"""Coordinator behavior: push, refresh, error mapping."""

from __future__ import annotations

import logging
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pysilverline import CannotConnect, DeviceState, InvalidAuth, SilverlineError
from pytest_homeassistant_custom_component.common import async_fire_time_changed


async def test_push_callback_updates_state(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    coordinator = init_integration.runtime_data
    listeners = mock_client_factory.listeners
    assert listeners, "coordinator should have registered exactly one listener"

    new_state = DeviceState.from_dps({"1": True, "3": 35, "4": "BoostHeat", "13": 0})
    listeners[0](new_state)
    await hass.async_block_till_done()
    assert coordinator.data is new_state


async def test_invalid_auth_during_poll_marks_auth_failed(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    mock_client_factory.get_status = AsyncMock(side_effect=InvalidAuth("rotated"))
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=60))
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress_by_handler(init_integration.domain)
    assert any(flow["context"].get("source") == "reauth" for flow in flows)


async def test_cannot_connect_during_poll_keeps_entry_loaded(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    mock_client_factory.get_status = AsyncMock(side_effect=CannotConnect("timeout"))
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=60))
    await hass.async_block_till_done()
    coordinator = init_integration.runtime_data
    assert coordinator.last_update_success is False


async def test_silverline_error_during_poll_keeps_entry_loaded(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    """A device-side rejection (non-zero retcode that isn't auth) must
    surface as a soft poll failure: last_update_success goes False so
    entities flip to unavailable, but no reauth flow is triggered — the
    socket is healthy, the firmware just refused this query."""
    mock_client_factory.get_status = AsyncMock(
        side_effect=SilverlineError("retcode 0x42")
    )
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=60))
    await hass.async_block_till_done()
    coordinator = init_integration.runtime_data
    assert coordinator.last_update_success is False
    assert not any(
        flow["context"].get("source") == "reauth"
        for flow in hass.config_entries.flow.async_progress()
    )


async def test_connection_listener_registered(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    """Coordinator registers exactly one connection listener at setup."""
    assert mock_client_factory.connection_listeners, (
        "coordinator should have registered a connection listener"
    )


async def test_entities_unavailable_on_disconnect(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    """Firing the connection listener with False flips last_update_success
    so CoordinatorEntity.available returns False — entities surface
    `unavailable` immediately, not at the next 30s poll."""
    coordinator = init_integration.runtime_data
    assert coordinator.last_update_success is True

    on_change = mock_client_factory.connection_listeners[0]
    on_change(False)
    await hass.async_block_till_done()
    assert coordinator.last_update_success is False


async def test_refresh_on_reconnect(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    """A True event schedules an async_request_refresh so HA sees a
    fresh state quickly rather than waiting for the next 30s tick."""
    coordinator = init_integration.runtime_data
    # Flip to disconnected first so the recovery transition is observable.
    on_change = mock_client_factory.connection_listeners[0]
    on_change(False)
    await hass.async_block_till_done()
    assert coordinator.last_update_success is False

    # Returning True should trigger a refresh; the mock's get_status returns
    # state_pool_running, which restores last_update_success.
    mock_client_factory.get_status.reset_mock()
    on_change(True)
    await hass.async_block_till_done()
    assert mock_client_factory.get_status.await_count >= 1
    assert coordinator.last_update_success is True


async def test_connection_change_logs_lost_and_restored(
    hass: HomeAssistant,
    mock_client_factory,
    init_integration,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Satisfies HA's `log-when-unavailable` rule: one warning on drop,
    one info on recovery — no more, no less."""
    caplog.set_level(
        logging.INFO, logger="custom_components.poolex_silverline.coordinator"
    )
    on_change = mock_client_factory.connection_listeners[0]

    caplog.clear()
    on_change(False)
    await hass.async_block_till_done()
    lost_records = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "lost" in r.getMessage()
    ]
    assert lost_records, "expected a WARNING log record mentioning 'lost'"

    caplog.clear()
    on_change(True)
    await hass.async_block_till_done()
    restored_records = [
        r
        for r in caplog.records
        if r.levelno == logging.INFO and "restored" in r.getMessage()
    ]
    assert restored_records, "expected an INFO log record mentioning 'restored'"
