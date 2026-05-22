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
    assert diag["state"]["raw"] == "**REDACTED**"
