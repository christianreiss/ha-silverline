"""Coordinator -> issue_registry: fault bits surface as auto-clearing
Repair issues. Covers the Gold rule `repair-issues`."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.poolex_silverline.const import (
    DOMAIN,
    E03_DEBOUNCE_SECONDS,
)
from pysilverline import DeviceState


def _issue(hass: HomeAssistant, key: str) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(DOMAIN, key)


async def test_no_issues_when_fault_clear(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    """state_pool_running has DP 13 = 0 → no Repair issues created."""
    assert _issue(hass, "fault_E03") is None
    assert _issue(hass, "fault_E04") is None


async def test_fault_bit_creates_repair_issue(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    """DP 13 = bit 2 (E05 high pressure) creates an ERROR-severity issue
    immediately. Non-bit-0 codes don't go through the E03 debounce."""
    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": True, "4": "Heat", "3": 26, "13": 1 << 2})
    )
    await hass.async_block_till_done()
    issue = _issue(hass, "fault_E05")
    assert issue is not None
    assert issue.severity is ir.IssueSeverity.ERROR
    assert issue.translation_key == "fault_E05"
    assert issue.is_fixable is False


async def test_fault_clearing_deletes_issue(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    """When the device clears DP 13, the issue is auto-removed."""
    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": True, "4": "Heat", "3": 26, "13": 1 << 2})
    )
    await hass.async_block_till_done()
    assert _issue(hass, "fault_E05") is not None

    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": True, "4": "Heat", "3": 26, "13": 0})
    )
    await hass.async_block_till_done()
    assert _issue(hass, "fault_E05") is None


async def test_multiple_simultaneous_faults(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    """DP 13 = 0b110 (bits 1 and 2) creates two issues independently.
    Picks the non-debounced bits so the test stays synchronous."""
    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": True, "4": "Heat", "3": 26, "13": 0b110})
    )
    await hass.async_block_till_done()
    assert _issue(hass, "fault_E04") is not None  # bit 1
    assert _issue(hass, "fault_E05") is not None  # bit 2
    assert _issue(hass, "fault_E03") is None  # bit 0 not set


async def test_partial_clear_keeps_remaining_issue(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    """If two bits are active and one clears, only that bit's issue
    disappears. The other stays."""
    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": True, "4": "Heat", "3": 26, "13": 0b110})
    )
    await hass.async_block_till_done()
    # Clear bit 1 (E04) but keep bit 2 (E05).
    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": True, "4": "Heat", "3": 26, "13": 0b100})
    )
    await hass.async_block_till_done()
    assert _issue(hass, "fault_E04") is None
    assert _issue(hass, "fault_E05") is not None


async def test_warning_severity_for_sensor_faults(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    """P-series sensor faults are WARNING severity, not ERROR — the unit
    keeps running, just with degraded readings."""
    coordinator = init_integration.runtime_data
    # bit 6 = P3 (inlet sensor fault)
    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": True, "4": "Heat", "3": 26, "13": 1 << 6})
    )
    await hass.async_block_till_done()
    issue = _issue(hass, "fault_P3")
    assert issue is not None
    assert issue.severity is ir.IssueSeverity.WARNING


async def test_repair_issue_fires_on_push(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    """Fault reconcile runs on push-frame state updates too, not just
    on coordinator polls — important because push is the fast path."""
    # The mock's push listeners list is in mock_client_factory.listeners;
    # the coordinator registered itself in async_setup. Invoke directly.
    push_listener = mock_client_factory.listeners[0]
    push_listener(DeviceState.from_dps({"1": True, "4": "Heat", "3": 26, "13": 1 << 2}))
    await hass.async_block_till_done()
    assert _issue(hass, "fault_E05") is not None


async def test_repair_issue_fires_on_poll(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    """Fault reconcile must also run on the periodic poll path.

    The DataUpdateCoordinator base class assigns _async_update_data's
    return value to self.data directly — it never routes the poll
    result through async_set_updated_data. If reconcile lived only in
    that override, a device that boots with a fault bit set would
    surface no Repair issue until the first push frame arrived.
    """
    mock_client_factory.get_status = AsyncMock(
        return_value=DeviceState.from_dps(
            {"1": True, "4": "Heat", "3": 26, "13": 1 << 2}
        )
    )
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=60))
    await hass.async_block_till_done()
    assert _issue(hass, "fault_E05") is not None


async def test_repair_issue_clears_on_poll(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    """The mirror case: a fault that clears while we're only polling
    (no pushes arriving) must drop the open Repair issue, not leave it
    stranded until the next push."""
    # Seed an active issue via the push path (mirrors a real boot with
    # a fault bit set).
    push_listener = mock_client_factory.listeners[0]
    push_listener(DeviceState.from_dps({"1": True, "4": "Heat", "3": 26, "13": 1 << 2}))
    await hass.async_block_till_done()
    assert _issue(hass, "fault_E05") is not None

    # Now switch the poll path to return a clean state and tick the
    # scheduler. The override path is not exercised — only the poll path.
    mock_client_factory.get_status = AsyncMock(
        return_value=DeviceState.from_dps({"1": True, "4": "Heat", "3": 26, "13": 0})
    )
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=60))
    await hass.async_block_till_done()
    assert _issue(hass, "fault_E05") is None


async def test_e03_debounce_no_issue_before_window(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    """Bit 0 (E03 water flow) must NOT raise a Repair issue immediately.
    The spec wants the issue only after the bit has been continuously
    set for ``E03_DEBOUNCE_SECONDS`` — startup self-trips of E03 should
    not surface a card."""
    coordinator = init_integration.runtime_data
    base = 1_000_000.0
    with patch("custom_components.poolex_silverline.coordinator.time.monotonic") as m:
        m.return_value = base
        # t=0: bit 0 appears
        coordinator.async_set_updated_data(
            DeviceState.from_dps({"1": True, "4": "Heat", "3": 26, "13": 1})
        )
        await hass.async_block_till_done()
        assert _issue(hass, "fault_E03") is None

        # t=30: still within debounce window, still no issue
        m.return_value = base + 30.0
        coordinator.async_set_updated_data(
            DeviceState.from_dps({"1": True, "4": "Heat", "3": 26, "13": 1})
        )
        await hass.async_block_till_done()
        assert _issue(hass, "fault_E03") is None

        # t=debounce+1: window elapsed → issue is raised
        m.return_value = base + E03_DEBOUNCE_SECONDS + 1.0
        coordinator.async_set_updated_data(
            DeviceState.from_dps({"1": True, "4": "Heat", "3": 26, "13": 1})
        )
        await hass.async_block_till_done()
        assert _issue(hass, "fault_E03") is not None


async def test_e03_debounce_resets_when_bit_clears(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    """If E03 toggles off before the debounce elapses, the next
    re-activation restarts the window from zero — the previous
    sighting cannot count toward the new window."""
    coordinator = init_integration.runtime_data
    base = 2_000_000.0
    with patch("custom_components.poolex_silverline.coordinator.time.monotonic") as m:
        # First sighting at t=0
        m.return_value = base
        coordinator.async_set_updated_data(
            DeviceState.from_dps({"1": True, "4": "Heat", "3": 26, "13": 1})
        )
        await hass.async_block_till_done()

        # Bit clears at t=30 — well within window
        m.return_value = base + 30.0
        coordinator.async_set_updated_data(
            DeviceState.from_dps({"1": True, "4": "Heat", "3": 26, "13": 0})
        )
        await hass.async_block_till_done()
        assert _issue(hass, "fault_E03") is None

        # Bit reappears at t=40; the debounce restarts from here.
        m.return_value = base + 40.0
        coordinator.async_set_updated_data(
            DeviceState.from_dps({"1": True, "4": "Heat", "3": 26, "13": 1})
        )
        await hass.async_block_till_done()
        assert _issue(hass, "fault_E03") is None

        # 40 + 30 = 70s elapsed in absolute time, but only 30s since the
        # restart — still no issue.
        m.return_value = base + 70.0
        coordinator.async_set_updated_data(
            DeviceState.from_dps({"1": True, "4": "Heat", "3": 26, "13": 1})
        )
        await hass.async_block_till_done()
        assert _issue(hass, "fault_E03") is None

        # Once 60s since the restart elapses, the issue surfaces.
        m.return_value = base + 40.0 + E03_DEBOUNCE_SECONDS + 1.0
        coordinator.async_set_updated_data(
            DeviceState.from_dps({"1": True, "4": "Heat", "3": 26, "13": 1})
        )
        await hass.async_block_till_done()
        assert _issue(hass, "fault_E03") is not None
