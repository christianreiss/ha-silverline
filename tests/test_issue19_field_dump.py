"""End-to-end regression against a real field dump of a live fault.

The unit tests around the Full Inverter fault table build their state from
a minimal hand-written DP dict. This one replays an actual diagnostics dump
posted to issue #19 while the fault was active, so the wiring is checked
against a device's real DP set rather than the subset a test author thought
to include — the whole class of bug here was a decode that looked right in
isolation and was wrong on the hardware.

Provenance: issue #19 comment 22 (2026-08-22). The reporter cut the
filtration pump under BoostHeat; the Poolex app displayed "Fault of Water
Flow Switch" and DP 13 read 256. Before the fix this produced a "Defrost
sensor fault" entity and a "Defrost sensor fault (P1)" Repair card.

The same reporter later read the codes off the unit's own wired controller
(2026-08-22): E25 for the water-flow fault, and P25 "ambient temperature too
high/low" for DP 13 = 524288. Both readings are replayed here.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.const import CONF_HOST, CONF_PORT, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pysilverline.layouts import LAYOUT_NANO_FI_3KW
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.poolex_silverline.const import (
    CONF_DEVICE_ID,
    CONF_LOCAL_KEY,
    CONF_MODEL,
    DOMAIN,
    E03_DEBOUNCE_SECONDS,
)
from pysilverline import DeviceState

# Verbatim from issue #19 comment 22 (2026-08-22), water-flow fault active.
RAW = {
    "1": True,
    "2": 28,
    "3": 21,
    "4": "BoostHeat",
    "13": 256,
    "102": False,
    "103": 21,
    "104": 37,
    "105": 19,
    "106": 21,
    "108": 24,
    "109": 0,
    "110": 0,
    "111": 480,
    "117": 23,
    "120": 231,
    "121": 0,
}


async def test_issue19_live_water_flow_fault_dump(hass: HomeAssistant) -> None:
    """A real FI water-flow fault must read as water flow end to end."""
    device_id = "bf9988776655realdump0"
    state = DeviceState.from_dps(RAW, layout=LAYOUT_NANO_FI_3KW)

    client = MagicMock()
    client.host, client.port, client.device_id = "10.0.0.64", 6668, device_id
    client.connected, client.state = True, state
    client.detected_version = "3.5"
    client.dp_layout = LAYOUT_NANO_FI_3KW
    for m in ("connect", "disconnect", "set_dp", "set_multiple"):
        setattr(client, m, AsyncMock(return_value=None))
    client.get_status = AsyncMock(return_value=state)
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
            CONF_MODEL: "nano_fi_3kw",
        },
        version=1,
        minor_version=1,
    )
    entry.add_to_hass(hass)
    # Hold the debounce clock from before setup: the first poll already runs a
    # reconcile and records first_seen, so a patch applied afterwards cannot
    # control the window.
    base = 1_000_000.0
    mono = patch("custom_components.poolex_silverline.coordinator.time.monotonic")
    with mono as m:
        m.return_value = base
        with patch(
            "custom_components.poolex_silverline.SilverlineClient", return_value=client
        ):
            assert await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()

    reg = er.async_get(hass)
    by_uid = {
        e.unique_id: e
        for e in reg.entities.values()
        if e.config_entry_id == entry.entry_id
    }

    wf = by_uid.get(f"{device_id}_fault_water_flow")
    assert wf is not None, "no water-flow entity created from the real dump"
    assert hass.states.get(wf.entity_id).state == STATE_ON
    assert f"{device_id}_fault_defrost_sensor" not in by_uid

    fc = by_uid.get(f"{device_id}_fault_code")
    assert hass.states.get(fc.entity_id).state == "water_flow"

    # Repair card: control the whole debounce window from a fixed base, the
    # way tests/test_repair_issues.py does — first_seen is recorded on the
    # first reconcile, so the base must cover that sighting too.
    coord = entry.runtime_data
    with mono as m:
        # Startup self-trip: within the window nothing surfaces.
        m.return_value = base + 10.0
        coord.async_set_updated_data(state)
        await hass.async_block_till_done()
        assert ir.async_get(hass).async_get_issue(DOMAIN, "fault_E25") is None
        m.return_value = base + E03_DEBOUNCE_SECONDS + 1.0
        coord.async_set_updated_data(state)
        await hass.async_block_till_done()
    issues = ir.async_get(hass)
    e25 = issues.async_get_issue(DOMAIN, "fault_E25")
    assert e25 is not None, "water flow must raise E25 once the debounce elapses"
    assert e25.severity is ir.IssueSeverity.ERROR
    assert issues.async_get_issue(DOMAIN, "fault_P1") is None, (
        "bit 8 is water flow on FI firmware, not the defrost sensor"
    )
    assert issues.async_get_issue(DOMAIN, "fault_E03") is None, (
        "E03 is the classic family's water-flow code; the FI panel prints E25"
    )


async def test_issue19_ambient_range_bit_is_named_but_raises_no_card(
    hass: HomeAssistant,
) -> None:
    """DP 13 = 524288 must read as ambient_range and open no Repair issue.

    Bit 19 is the named-but-uncoded case. The reporter's panel printed "P25
    ambient temperature too high/low" while the app said "ambient temperature
    out of range", so the name is hardware-backed — but the protection is
    weather-driven and self-clearing, so a Repair card would appear and vanish
    with the forecast. It gets a binary sensor and a fault_code string only.
    """
    device_id = "bf9988776655ambient01"
    raw = {**RAW, "13": 524288}
    state = DeviceState.from_dps(raw, layout=LAYOUT_NANO_FI_3KW)

    client = MagicMock()
    client.host, client.port, client.device_id = "10.0.0.65", 6668, device_id
    client.connected, client.state = True, state
    client.detected_version = "3.5"
    client.dp_layout = LAYOUT_NANO_FI_3KW
    for m in ("connect", "disconnect", "set_dp", "set_multiple"):
        setattr(client, m, AsyncMock(return_value=None))
    client.get_status = AsyncMock(return_value=state)
    client.add_listener = MagicMock(return_value=lambda: None)
    client.add_connection_listener = MagicMock(return_value=lambda: None)

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=device_id,
        data={
            CONF_HOST: "10.0.0.65",
            CONF_PORT: 6668,
            CONF_DEVICE_ID: device_id,
            CONF_LOCAL_KEY: "0123456789abcdef",
            CONF_MODEL: "nano_fi_3kw",
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

    reg = er.async_get(hass)
    by_uid = {
        e.unique_id: e
        for e in reg.entities.values()
        if e.config_entry_id == entry.entry_id
    }
    amb = by_uid.get(f"{device_id}_fault_ambient_range")
    assert amb is not None, "bit 19 must get its own binary sensor"
    assert hass.states.get(amb.entity_id).state == STATE_ON

    fc = by_uid.get(f"{device_id}_fault_code")
    assert hass.states.get(fc.entity_id).state == "ambient_range", (
        "an uncoded bit must still be named in fault_code, not left as bit19"
    )

    # Let any debounce window elapse — an uncoded bit must never open a card.
    coord = entry.runtime_data
    with patch("custom_components.poolex_silverline.coordinator.time.monotonic") as m:
        m.return_value = 2_000_000.0 + E03_DEBOUNCE_SECONDS * 4
        coord.async_set_updated_data(state)
        await hass.async_block_till_done()
    issues = ir.async_get(hass)
    assert issues.issues == {}, (
        "an ambient-range protection must not raise a Repair card"
    )


async def test_issue19_config_sensors_survive_a_first_poll_without_them(
    hass: HomeAssistant,
) -> None:
    """The installer-parameter sensors must not depend on poll timing.

    Full Inverter firmware does not send a stable DP set. The same unit, one
    day apart, reported 142/145 without 124-132 and then the reverse (issue
    #19, @richardc1983) — so latching supported_dps on whichever poll landed
    first meant the reporter got eight of the ten config sensors, and a
    different eight after each reload. The model profile now pins the set as
    a floor. Replay a first poll that carries none of the block and require
    all ten entities anyway.
    """
    device_id = "bf9988776655lottery01"
    raw = {k: v for k, v in RAW.items() if int(k) < 124}
    state = DeviceState.from_dps(raw, layout=LAYOUT_NANO_FI_3KW)

    client = MagicMock()
    client.host, client.port, client.device_id = "10.0.0.66", 6668, device_id
    client.connected, client.state = True, state
    client.detected_version = "3.5"
    client.dp_layout = LAYOUT_NANO_FI_3KW
    for m in ("connect", "disconnect", "set_dp", "set_multiple"):
        setattr(client, m, AsyncMock(return_value=None))
    client.get_status = AsyncMock(return_value=state)
    client.add_listener = MagicMock(return_value=lambda: None)
    client.add_connection_listener = MagicMock(return_value=lambda: None)

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=device_id,
        data={
            CONF_HOST: "10.0.0.66",
            CONF_PORT: 6668,
            CONF_DEVICE_ID: device_id,
            CONF_LOCAL_KEY: "0123456789abcdef",
            CONF_MODEL: "nano_fi_3kw",
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

    uids = {
        e.unique_id
        for e in er.async_get(hass).entities.values()
        if e.config_entry_id == entry.entry_id
    }
    for key in (
        "heating_time",
        "defrost_time_limit",
        "defrost_cutout_temperature",
        "heating_start_hysteresis",
        "heating_end_hysteresis",
        "cooling_start_hysteresis",
        "cooling_end_hysteresis",
        "defrost_temperature",
        "maximum_temperature_limit",
        "minimum_temperature_limit",
    ):
        assert f"{device_id}_{key}" in uids, f"{key} lost to first-poll timing"

    # The floor must not manufacture entities for DPs no Nano Fi has ever
    # sent: 112/114/116 are mapped but unpinned, so they stay gated on the
    # wire and register nothing here.
    for key in ("aux_valve_opening", "fan_speed", "ambient_temperature"):
        assert f"{device_id}_{key}" not in uids, f"{key} must stay wire-gated"
