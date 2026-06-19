"""Diagnostics redaction test."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from .conftest import DEVICE_ID, HOST, LOCAL_KEY


async def test_diagnostics_redacts_secrets(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    init_integration,
) -> None:
    diag = await get_diagnostics_for_config_entry(hass, hass_client, init_integration)
    flat = repr(diag)
    assert LOCAL_KEY not in flat
    assert DEVICE_ID not in flat
    assert HOST not in flat
    assert "**REDACTED**" in flat
    assert "state" in diag
    assert diag["state"]["mode"] == "Heat"
    # raw is intentionally kept un-redacted so an operator helping with a
    # new firmware variant can see unmapped DPs in the dump. It holds DP
    # numbers and values (temps, modes, fault bits) — no secrets.
    assert isinstance(diag["state"]["raw"], dict)
    assert diag["state"]["raw"]  # populated with the fixture's DPs


async def test_diagnostics_includes_debug_context(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    init_integration,
) -> None:
    """The dump carries the context a maintainer needs around the raw DPs."""
    diag = await get_diagnostics_for_config_entry(hass, hass_client, init_integration)

    # Versions — the first thing asked of a reporter.
    assert isinstance(diag["versions"]["pysilverline"], str)
    assert diag["versions"]["pysilverline"]
    assert "integration" in diag["versions"]

    # Connection health / negotiated protocol.
    assert "detected_version" in diag["connection"]
    assert isinstance(diag["connection"]["connected"], bool)
    # host stays redacted even on the new path.
    assert diag["connection"]["host"] == "**REDACTED**"

    # Coordinator context.
    assert isinstance(diag["coordinator"]["supported_dps"], list)
    assert isinstance(diag["coordinator"]["runtime_today_seconds"], (int, float))
    assert isinstance(diag["coordinator"]["last_update_success"], bool)
    assert isinstance(diag["coordinator"]["active_fault_codes"], list)


async def test_diagnostics_last_exception_does_not_leak_host(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    init_integration,
) -> None:
    """A poll failure stamps last_exception with the host in its message;
    the dump must surface only the failure type, never the IP."""
    coordinator = init_integration.runtime_data
    # Mirror the real wrap path: pysilverline embeds the host in the message
    # (CannotConnect(f"connect {host}:{port}: ...")) and the coordinator
    # re-wraps it. async_redact_data scrubs by key, not substring, so a raw
    # string would leak the IP.
    coordinator.last_exception = RuntimeError(
        f"poll failed: connect {HOST}:6668: timeout"
    )

    diag = await get_diagnostics_for_config_entry(hass, hass_client, init_integration)

    assert HOST not in repr(diag)
    assert diag["coordinator"]["last_exception"] == "RuntimeError"
