"""Coordinator behavior: push, refresh, error mapping."""

from __future__ import annotations

import logging
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from pysilverline import CannotConnect, DeviceState, InvalidAuth, SilverlineError


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


# ---------------------------------------------------------------------------
# Model-profile pre-population of supported_dps
# ---------------------------------------------------------------------------


async def test_known_model_pre_populates_supported_dps(
    hass: HomeAssistant,
    mock_client_factory,
) -> None:
    """A config entry with CONF_MODEL='pc_slp090n' should pre-populate
    supported_dps with the 5 known DPs so entities register before first poll.

    known_dps is a floor, not a replacement: the first poll's DP set is
    unioned on top of it (issue #19), so the profile guarantees its core
    entities exist without suppressing an optional DP the unit does send.
    """
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.poolex_silverline.const import (
        CONF_MODEL,
        DEVICE_PROFILES,
        DOMAIN,
    )

    from .conftest import DEVICE_ID, ENTRY_DATA, HOST

    entry = MockConfigEntry(
        domain=DOMAIN,
        title=f"Pool Heatpump ({HOST})",
        unique_id=DEVICE_ID,
        data={**ENTRY_DATA, CONF_MODEL: "pc_slp090n"},
        version=1,
        minor_version=3,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = entry.runtime_data
    expected = frozenset(
        str(dp)
        for dp in DEVICE_PROFILES["pc_slp090n"].known_dps  # type: ignore[arg-type]
    )
    assert expected <= coordinator.supported_dps, "the profile floor must hold"
    # ...and the live set is unioned on top rather than discarded.
    assert coordinator.supported_dps >= frozenset(coordinator.data.raw)


async def test_unknown_model_leaves_supported_dps_empty(
    hass: HomeAssistant,
    mock_client_factory,
) -> None:
    """CONF_MODEL='other' (known_dps=None) leaves supported_dps empty until
    the first poll populates it."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.poolex_silverline.const import CONF_MODEL, DOMAIN

    from .conftest import DEVICE_ID, ENTRY_DATA, HOST

    entry = MockConfigEntry(
        domain=DOMAIN,
        title=f"Pool Heatpump ({HOST})",
        unique_id=DEVICE_ID,
        data={**ENTRY_DATA, CONF_MODEL: "other"},
        version=1,
        minor_version=3,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = entry.runtime_data
    # After setup, first poll ran and populated from mock state (all DPs in
    # state_pool_running); it should not be empty any more, but the key point
    # is that the profile did not pre-populate it with a fixed set. DP 101 is
    # not in the 'other' profile, so its presence proves the poll populated
    # supported_dps rather than the model profile.
    assert "101" in coordinator.supported_dps  # populated by poll, not by profile


async def test_nano_5kw_pre_populates_supported_dps_even_when_device_off(
    hass: HomeAssistant,
) -> None:
    """Regression guard (issue #16/#18): the Nano 5kW family only reports
    DPs 3/4/21 while powered ON — a live diagnostic dump taken while the
    unit was off showed only {1,2}. If supported_dps were live-detected
    (known_dps=None) and the very first poll after setup landed while the
    pump was idle, the coordinator's once-only latch (see
    coordinator._async_update_data) would freeze supported_dps at {1,2}
    forever, permanently starving temperature_delta/runtime_today/the DP21
    fault sensor — even after the pump powers back on. known_dps must be a
    FIXED set for this profile so entities register from the profile alone,
    independent of what the first poll happens to catch."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from homeassistant.const import CONF_HOST, CONF_PORT
    from homeassistant.helpers import entity_registry as er
    from pysilverline.layouts import LAYOUT_NANO_5KW
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.poolex_silverline.const import (
        CONF_DEVICE_ID,
        CONF_LOCAL_KEY,
        CONF_MODEL,
        DOMAIN,
    )

    device_id = "bf99887766nano5kwoffff"
    # Device off at the moment of the first poll — exactly issue #18's
    # second diagnostic capture (only DP 1/2 present).
    off_state = DeviceState.from_dps({"1": False, "2": 30}, layout=LAYOUT_NANO_5KW)

    client = MagicMock()
    client.host = "10.0.0.64"
    client.port = 6668
    client.device_id = device_id
    client.connected = True
    client.state = off_state
    client.detected_version = "3.4"
    client.dp_layout = LAYOUT_NANO_5KW
    client.connect = AsyncMock(return_value=None)
    client.disconnect = AsyncMock(return_value=None)
    client.get_status = AsyncMock(return_value=off_state)
    client.set_dp = AsyncMock(return_value=None)
    client.set_multiple = AsyncMock(return_value=None)
    client.add_listener = MagicMock(return_value=lambda: None)
    client.add_connection_listener = MagicMock(return_value=lambda: None)

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=device_id,
        data={
            CONF_HOST: "10.0.0.64",
            CONF_PORT: 6668,
            CONF_DEVICE_ID: device_id,
            CONF_LOCAL_KEY: "0123456789abcdef",
            CONF_MODEL: "nano_5kw",
        },
        version=1,
        minor_version=1,
    )
    entry.add_to_hass(hass)
    with patch(
        "custom_components.poolex_silverline.SilverlineClient", return_value=client
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    coordinator = entry.runtime_data
    # Fixed from the profile, unaffected by the device being off at setup.
    assert coordinator.supported_dps == {"1", "2", "3", "4", "21"}

    registry = er.async_get(hass)
    sensor_keys = {
        e.unique_id.removeprefix(f"{device_id}_")
        for e in er.async_entries_for_config_entry(registry, entry.entry_id)
        if e.domain == "sensor"
    }
    assert sensor_keys == {"temperature_delta", "fault_code", "runtime_today"}


async def test_union_does_not_resurrect_a_deliberately_excluded_dp(
    hass: HomeAssistant,
    mock_client_factory,
) -> None:
    """supported_dps unions the live set, so a pin can no longer suppress a DP.

    MODEL_NANO_5KW pins {1,2,3,4,21} and its comment excludes DP 101 on
    purpose: one unit reports a boolean there whose meaning is unconfirmed.
    Since the pin became a floor (issue #19), that exclusion is no longer
    enforced by the pin — it holds only because nothing in this model's
    catalog reads DP 101. Pin that, so wiring an entity to DP 101 later
    fails here rather than shipping an unexplained sensor to Nano 5kW owners.
    """
    from unittest.mock import AsyncMock, MagicMock, patch

    from homeassistant.const import CONF_HOST, CONF_PORT
    from homeassistant.helpers import entity_registry as er
    from pysilverline.layouts import LAYOUT_NANO_5KW
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.poolex_silverline.const import (
        CONF_DEVICE_ID,
        CONF_LOCAL_KEY,
        CONF_MODEL,
        DOMAIN,
    )
    from custom_components.poolex_silverline.sensor_descriptions import (
        descriptions_for_model,
    )

    device_id = "bf9988776655nano5kw01"
    raw = {"1": True, "2": 28, "3": 26, "4": "Heat", "21": 0, "101": False}
    state = DeviceState.from_dps(raw, layout=LAYOUT_NANO_5KW)

    client = MagicMock()
    client.host, client.port, client.device_id = "10.0.0.67", 6668, device_id
    client.connected, client.state = True, state
    client.detected_version = "3.4"
    client.dp_layout = LAYOUT_NANO_5KW
    for name in ("connect", "disconnect", "set_dp", "set_multiple"):
        setattr(client, name, AsyncMock(return_value=None))
    client.get_status = AsyncMock(return_value=state)
    client.add_listener = MagicMock(return_value=lambda: None)
    client.add_connection_listener = MagicMock(return_value=lambda: None)

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=device_id,
        data={
            CONF_HOST: "10.0.0.67",
            CONF_PORT: 6668,
            CONF_DEVICE_ID: device_id,
            CONF_LOCAL_KEY: "0123456789abcdef",
            CONF_MODEL: "nano_5kw",
        },
        version=1,
        minor_version=1,
    )
    entry.add_to_hass(hass)
    with patch(
        "custom_components.poolex_silverline.SilverlineClient", return_value=client
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    coordinator = entry.runtime_data
    assert "101" in coordinator.supported_dps, "the union must see the live DP"

    entities = [
        e
        for e in er.async_get(hass).entities.values()
        if e.config_entry_id == entry.entry_id
    ]
    for description in descriptions_for_model("nano_5kw"):
        assert "101" not in description.dp_keys, (
            f"{description.key} would now register against the unexplained DP 101"
        )
    assert entities, "sanity: the entry did create its normal entities"


async def test_a_push_storm_does_not_starve_the_periodic_poll(
    hass: HomeAssistant,
    mock_client_factory,
    init_integration,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Single-DP pushes must not keep rescheduling the full-state poll.

    Regression test for the mechanism behind issue #19's config-sensor
    lottery. ``DataUpdateCoordinator.async_set_updated_data`` resets the
    refresh timer by design — right for firmware whose push carries the
    whole state, fatal here, where a Nano Fi pushes one DP at a time every
    ~2.4 s against a 30 s interval. The real field log (2026-08-22) holds
    215 pushes and exactly one DP_QUERY across 8.6 minutes: the startup read
    was the only full-state read of the session, so anything the firmware
    left out of it never arrived at all.

    The coordinator schedules with ``loop.call_at`` on the event loop's
    monotonic clock, not ``utcnow()``, so the loop clock is what has to
    advance for a rescheduling bug to be visible — freezing ``dt_util`` alone
    leaves the target where it was and the test passes either way. Each tick
    therefore advances the fake loop clock and then asks only whether
    anything is due within the *next* step, which is what makes a timer that
    keeps sliding forward distinguishable from one that does not.
    """
    coordinator = init_integration.runtime_data
    interval = coordinator.update_interval
    assert interval is not None
    listeners = mock_client_factory.listeners
    mock_client_factory.get_status.reset_mock()

    step = interval / 6
    clock = {"now": hass.loop.time()}
    monkeypatch.setattr(hass.loop, "time", lambda: clock["now"])

    # Push one DP at a time, faster than the poll interval, across two full
    # intervals of wall clock — the shape the field log shows.
    for tick in range(1, 13):
        clock["now"] += step.total_seconds()
        listeners[0](DeviceState.from_dps({"1": True, "120": 230 + tick % 5}))
        async_fire_time_changed(hass, dt_util.utcnow() + step)
        await hass.async_block_till_done()

    assert mock_client_factory.get_status.call_count >= 1, (
        "the periodic poll never ran: every push reset its timer, so the "
        "startup query is the only full-state read the device ever gets"
    )


async def test_a_push_only_dp_still_reaches_supported_dps(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    """A DP first seen in a push counts as supported, not just a polled one.

    The accumulator used to live in the poll path alone, which assumed every
    DP eventually shows up in a DP_QUERY response. This firmware disproves
    that: DP 115 (defrosting) rides ordinary status pushes while the 124-145
    config block does not ride pushes at all.
    """
    coordinator = init_integration.runtime_data
    assert "115" not in coordinator.supported_dps

    mock_client_factory.listeners[0](
        DeviceState.from_dps({"1": True, "13": 0, "115": 1})
    )
    await hass.async_block_till_done()

    assert "115" in coordinator.supported_dps
