"""Config flow tests — Bronze rule config-flow-test-coverage requires 100%."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from pysilverline import CannotConnect, InvalidAuth
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.poolex_silverline._config_validation import (
    scan_interval_from_options,
)
from custom_components.poolex_silverline.const import (
    CONF_DEVICE_ID,
    CONF_LOCAL_KEY,
    CONF_MODEL,
    CONF_PROTOCOL_VERSION,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)

from .conftest import DEVICE_ID, ENTRY_DATA, HOST, LOCAL_KEY


async def _start_user_flow(hass: HomeAssistant) -> str:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return result["flow_id"]


async def _submit_model_step(
    hass: HomeAssistant, flow_id: str, model: str = "other"
) -> dict:
    """Submit the model selection step and return the result."""
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_MODEL: model}
    )
    return result


async def test_user_flow_happy_path(hass: HomeAssistant, mock_client_factory) -> None:
    flow_id = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(flow_id, ENTRY_DATA)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "model"

    result = await _submit_model_step(hass, result["flow_id"], "pc_slp090n")
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_PROTOCOL_VERSION] == "3.3"
    assert result["data"][CONF_MODEL] == "pc_slp090n"
    assert result["title"].startswith("Pool Heatpump")
    assert result["result"].unique_id == DEVICE_ID


@pytest.mark.parametrize(
    "exc,expected_error",
    [
        (CannotConnect("nope"), "cannot_connect"),
        (InvalidAuth("bad key"), "invalid_auth"),
        (ValueError("local_key must be 16 ASCII characters"), "invalid_auth"),
        (RuntimeError("boom"), "unknown"),
    ],
)
async def test_user_flow_validation_errors(
    hass: HomeAssistant, mock_client_factory, exc: Exception, expected_error: str
) -> None:
    mock_client_factory.get_status.side_effect = exc
    flow_id = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(flow_id, ENTRY_DATA)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected_error}
    # The validator must close the socket even when validation fails
    # (_validate disconnects in a finally). Assert here, before the
    # recovery retry — the success path below also disconnects, so a
    # check at the end of the test would be vacuous.
    assert mock_client_factory.disconnect.called

    # Recover: clear side_effect, retry → model step → create entry
    mock_client_factory.get_status.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], ENTRY_DATA
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "model"
    result = await _submit_model_step(hass, result["flow_id"])
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_aborts_when_already_configured(
    hass: HomeAssistant, mock_client_factory, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    flow_id = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(flow_id, ENTRY_DATA)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_flow_happy_path(
    hass: HomeAssistant, mock_client_factory, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOCAL_KEY: "fedcba9876543210"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data[CONF_LOCAL_KEY] == "fedcba9876543210"
    # The reauth probe detected v3.3, so the conditional that persists the
    # detected protocol version onto the entry must have fired.
    assert config_entry.data.get(CONF_PROTOCOL_VERSION) == "3.3"
    # async_update_reload_and_abort schedules the reload as a background
    # task; without draining here the new coordinator's refresh timer
    # outlives the test and trips the lingering-timer cleanup check.
    await hass.async_block_till_done()


async def test_reauth_flow_rejects_bad_key(
    hass: HomeAssistant, mock_client_factory, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reauth_flow(hass)

    mock_client_factory.get_status.side_effect = InvalidAuth("still bad")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOCAL_KEY: "still_wrong_keyy"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_reconfigure_flow_happy_path(
    hass: HomeAssistant, mock_client_factory, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    new_data = {**ENTRY_DATA, CONF_HOST: "10.0.0.99", CONF_PORT: 6669}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], new_data)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "model"

    result = await _submit_model_step(hass, result["flow_id"], "jetline_fi")
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert config_entry.data[CONF_HOST] == "10.0.0.99"
    assert config_entry.data[CONF_PORT] == 6669
    assert config_entry.data[CONF_MODEL] == "jetline_fi"
    # Drain the reload triggered by async_update_reload_and_abort so the
    # new coordinator's refresh timer is cancelled before teardown.
    await hass.async_block_till_done()


async def test_reconfigure_flow_rejects_device_id_change(
    hass: HomeAssistant, mock_client_factory, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reconfigure_flow(hass)
    new_data = {**ENTRY_DATA, CONF_DEVICE_ID: "different_device_id_22"}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], new_data)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "device_id_mismatch"


async def test_reconfigure_flow_validation_failure(
    hass: HomeAssistant, mock_client_factory, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reconfigure_flow(hass)

    mock_client_factory.get_status.side_effect = CannotConnect("offline")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], ENTRY_DATA
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_user_flow_disconnect_called_after_validation(
    hass: HomeAssistant, mock_client_factory
) -> None:
    """The validation helper must close the socket even on success."""
    flow_id = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(flow_id, ENTRY_DATA)
    assert mock_client_factory.disconnect.called
    # Complete the model step so the flow is not left open.
    if result["type"] is FlowResultType.FORM:
        await _submit_model_step(hass, result["flow_id"])


# ---------------------------------------------------------------------------
# Integration-discovery flow (Gold rules `discovery` + `discovery-update-info`)
# ---------------------------------------------------------------------------


async def test_discovery_flow_happy_path(
    hass: HomeAssistant, mock_client_factory
) -> None:
    """A UDP broadcast triggers the flow; user supplies local_key only;
    entry is created with host + device_id from the broadcast."""
    from homeassistant.config_entries import SOURCE_INTEGRATION_DISCOVERY

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_INTEGRATION_DISCOVERY},
        data={"device_id": DEVICE_ID, "ip": HOST, "version": "3.3"},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "discovery_confirm"
    # description_placeholders carries the discovered host so the UI
    # can show "Discovered at <ip>".
    assert result["description_placeholders"] == {"host": HOST}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOCAL_KEY: LOCAL_KEY}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "model"

    result = await _submit_model_step(hass, result["flow_id"])
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_HOST] == HOST
    assert result["data"][CONF_DEVICE_ID] == DEVICE_ID
    assert result["data"][CONF_LOCAL_KEY] == LOCAL_KEY
    assert result["result"].unique_id == DEVICE_ID


async def test_discovery_updates_host_on_existing_entry(
    hass: HomeAssistant, mock_client_factory, config_entry: MockConfigEntry
) -> None:
    """If the device is already configured but appears at a NEW IP, the
    discovery flow aborts as already_configured but rewrites
    entry.data[CONF_HOST] in place — that's Gold `discovery-update-info`."""
    from homeassistant.config_entries import SOURCE_INTEGRATION_DISCOVERY

    config_entry.add_to_hass(hass)
    assert config_entry.data[CONF_HOST] == HOST

    new_ip = "10.0.0.99"
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_INTEGRATION_DISCOVERY},
        data={"device_id": DEVICE_ID, "ip": new_ip, "version": "3.3"},
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert config_entry.data[CONF_HOST] == new_ip


async def test_discovery_rewrites_host_only_on_verified_response(
    hass: HomeAssistant, mock_client_factory, config_entry: MockConfigEntry
) -> None:
    """Existing entry, broadcast announces a new IP, the new IP responds
    correctly to an encrypted handshake with our stored local_key → the
    entry's CONF_HOST is rewritten."""
    from homeassistant.config_entries import SOURCE_INTEGRATION_DISCOVERY

    config_entry.add_to_hass(hass)
    assert config_entry.data[CONF_HOST] == HOST

    new_ip = "10.0.0.99"
    # mock_client_factory's get_status succeeds by default → verification
    # passes → CONF_HOST is rewritten.
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_INTEGRATION_DISCOVERY},
        data={"device_id": DEVICE_ID, "ip": new_ip, "version": "3.3"},
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert config_entry.data[CONF_HOST] == new_ip
    # The verifier must close the socket it opened.
    assert mock_client_factory.disconnect.called


@pytest.mark.parametrize(
    ("inject_attr", "exc"),
    [
        # Responder is reachable but never proves it holds our local_key.
        ("get_status", CannotConnect("spoof")),
        # connect() failure — for v3.4/v3.5 the session-key handshake
        # inside connect() IS the auth check, so a rotated/invalid
        # local_key surfaces here as InvalidAuth.
        ("connect", InvalidAuth("rotated key")),
        # A malformed handshake payload bubbles up as ValueError (the
        # third member of the verifier's caught tuple).
        ("connect", ValueError("garbage frame")),
    ],
)
async def test_discovery_ignores_unverified_host(
    hass: HomeAssistant,
    mock_client_factory,
    config_entry: MockConfigEntry,
    inject_attr: str,
    exc: Exception,
) -> None:
    """Existing entry, broadcast announces a hostile IP that doesn't
    answer with our local_key → CONF_HOST stays put and the flow aborts
    with `unverified_host`.

    The verifier wraps both connect() (the v3.4/v3.5 session-key
    handshake) and get_status() in one try; a failure at either stage —
    via any member of the caught (CannotConnect, InvalidAuth, ValueError)
    tuple — must abort without rewriting the stored host.
    """
    from homeassistant.config_entries import SOURCE_INTEGRATION_DISCOVERY

    config_entry.add_to_hass(hass)
    assert config_entry.data[CONF_HOST] == HOST

    hostile_ip = "10.0.0.66"
    getattr(mock_client_factory, inject_attr).side_effect = exc
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_INTEGRATION_DISCOVERY},
        data={"device_id": DEVICE_ID, "ip": hostile_ip, "version": "3.3"},
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "unverified_host"
    # Crucially, the stored host is unchanged.
    assert config_entry.data[CONF_HOST] == HOST
    # Even on failed verification, the verifier must call disconnect().
    assert mock_client_factory.disconnect.called


async def test_discovery_same_ip_aborts_without_verify(
    hass: HomeAssistant, mock_client_factory, config_entry: MockConfigEntry
) -> None:
    """A discovery broadcast for an already-configured device at its
    existing IP short-circuits to already_configured without opening a
    verification socket."""
    from homeassistant.config_entries import SOURCE_INTEGRATION_DISCOVERY

    config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_INTEGRATION_DISCOVERY},
        data={"device_id": DEVICE_ID, "ip": HOST, "version": "3.3"},
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    # Short-circuit before _verify_host — connect must not be called.
    assert not mock_client_factory.connect.called


async def test_discovery_step_delegates_to_integration_discovery(
    hass: HomeAssistant, mock_client_factory
) -> None:
    """async_step_discovery is the hassfest-recognised alias that
    forwards to async_step_integration_discovery."""
    from homeassistant.config_entries import SOURCE_DISCOVERY

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_DISCOVERY},
        data={"device_id": DEVICE_ID, "ip": HOST, "version": "3.3"},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "discovery_confirm"


async def test_verify_host_swallows_unexpected_exception(
    hass: HomeAssistant, mock_client_factory, config_entry: MockConfigEntry
) -> None:
    """An unexpected exception inside _verify_host is treated as
    verification failure, not propagated."""
    from homeassistant.config_entries import SOURCE_INTEGRATION_DISCOVERY

    config_entry.add_to_hass(hass)
    mock_client_factory.get_status.side_effect = RuntimeError("kaboom")
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_INTEGRATION_DISCOVERY},
        data={"device_id": DEVICE_ID, "ip": "10.0.0.77", "version": "3.3"},
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "unverified_host"
    assert config_entry.data[CONF_HOST] == HOST
    assert mock_client_factory.disconnect.called


async def test_discovery_logs_known_product_key(
    hass: HomeAssistant,
    mock_client_factory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A broadcast carrying a confirmed Poolex productKey should produce
    a discovery flow AND an INFO log line marking it as known. The
    permissive filter does not abort — that's the user-chosen behavior
    until more productKeys are captured."""
    import logging
    from homeassistant.config_entries import SOURCE_INTEGRATION_DISCOVERY

    caplog.set_level(
        logging.INFO, logger="custom_components.poolex_silverline.config_flow"
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_INTEGRATION_DISCOVERY},
        data={
            "device_id": DEVICE_ID,
            "ip": HOST,
            "version": "3.3",
            "product_key": "3bhylhz5zhogklel",
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "discovery_confirm"
    assert any(
        "productKey=3bhylhz5zhogklel" in r.getMessage()
        and "known=True" in r.getMessage()
        for r in caplog.records
    ), (
        f"expected known-productKey INFO log; got {[r.getMessage() for r in caplog.records]}"
    )


async def test_discovery_aborts_on_unknown_product_key(
    hass: HomeAssistant,
    mock_client_factory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An unknown productKey (e.g. a Tuya smart bulb / plug / camera
    that broadcasts on the same discovery port) must abort the flow with
    `unsupported_product` so no spurious "Pool Heatpump" discovery card
    appears for it. The check fires before async_set_unique_id, so the
    bogus device_id never lands in the flow context either."""
    import logging
    from homeassistant.config_entries import SOURCE_INTEGRATION_DISCOVERY

    caplog.set_level(
        logging.INFO, logger="custom_components.poolex_silverline.config_flow"
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_INTEGRATION_DISCOVERY},
        data={
            "device_id": DEVICE_ID,
            "ip": HOST,
            "version": "3.3",
            "product_key": "tuyabulbkeyXXXXX",
        },
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "unsupported_product"
    assert any(
        "productKey=tuyabulbkeyXXXXX" in r.getMessage()
        and "ignoring non-Poolex" in r.getMessage()
        for r in caplog.records
    ), f"expected non-Poolex INFO log; got {[r.getMessage() for r in caplog.records]}"


async def test_discovery_logs_missing_product_key_as_known_false(
    hass: HomeAssistant,
    mock_client_factory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Discovery payload without a productKey (e.g. older Tuya firmware
    that doesn't include it in the broadcast JSON) logs known=False and
    continues with the flow."""
    import logging
    from homeassistant.config_entries import SOURCE_INTEGRATION_DISCOVERY

    caplog.set_level(
        logging.INFO, logger="custom_components.poolex_silverline.config_flow"
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_INTEGRATION_DISCOVERY},
        data={"device_id": DEVICE_ID, "ip": HOST, "version": "3.3"},
    )
    assert result["type"] is FlowResultType.FORM
    assert any(
        "productKey=None" in r.getMessage() and "known=False" in r.getMessage()
        for r in caplog.records
    ), (
        f"expected None-productKey INFO log; got {[r.getMessage() for r in caplog.records]}"
    )


async def test_discovery_invalid_key_re_prompts(
    hass: HomeAssistant, mock_client_factory
) -> None:
    """If the user enters a wrong local_key in the discovery confirm step,
    the form is shown again with the invalid_auth error key, not aborted."""
    from homeassistant.config_entries import SOURCE_INTEGRATION_DISCOVERY

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_INTEGRATION_DISCOVERY},
        data={"device_id": DEVICE_ID, "ip": HOST, "version": "3.3"},
    )
    mock_client_factory.get_status = AsyncMock(side_effect=InvalidAuth("nope"))
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOCAL_KEY: "wrong-key-123456"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "discovery_confirm"
    assert result["errors"] == {"base": "invalid_auth"}


# ---------------------------------------------------------------------------
# Model selector (Phase 2)
# ---------------------------------------------------------------------------


async def test_model_step_stores_model_key(
    hass: HomeAssistant, mock_client_factory
) -> None:
    """Selecting a named model stores CONF_MODEL in entry data."""
    flow_id = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(flow_id, ENTRY_DATA)
    assert result["step_id"] == "model"

    result = await _submit_model_step(hass, result["flow_id"], "pc_slp090n")
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_MODEL] == "pc_slp090n"


async def test_model_step_defaults_to_other(
    hass: HomeAssistant, mock_client_factory
) -> None:
    """Default model selection 'other' is stored and leaves supported_dps to
    live-detection."""
    flow_id = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(flow_id, ENTRY_DATA)
    result = await _submit_model_step(hass, result["flow_id"], "other")
    assert result["data"][CONF_MODEL] == "other"


async def test_model_step_reconfigure_updates_model(
    hass: HomeAssistant, mock_client_factory, config_entry: MockConfigEntry
) -> None:
    """Reconfigure flow reaches model step and updates the entry model."""
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], ENTRY_DATA
    )
    assert result["step_id"] == "model"

    result = await _submit_model_step(hass, result["flow_id"], "jetline_fi")
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert config_entry.data[CONF_MODEL] == "jetline_fi"
    await hass.async_block_till_done()


async def test_reauth_skips_model_step(
    hass: HomeAssistant, mock_client_factory, config_entry: MockConfigEntry
) -> None:
    """Reauth flow only changes local_key — no model step."""
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOCAL_KEY: "fedcba9876543210"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    await hass.async_block_till_done()


async def test_options_flow_sets_scan_interval(
    hass: HomeAssistant, mock_client_factory, config_entry: MockConfigEntry
) -> None:
    """The options form writes the chosen interval and the reload listener
    hands it to the coordinator."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL: 60}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    assert config_entry.options[CONF_SCAN_INTERVAL] == 60
    coordinator = config_entry.runtime_data
    assert coordinator.update_interval == timedelta(seconds=60)


async def test_options_flow_defaults_to_thirty_seconds(
    hass: HomeAssistant, mock_client_factory, config_entry: MockConfigEntry
) -> None:
    """An entry with no options polls at DEFAULT_SCAN_INTERVAL."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    coordinator = config_entry.runtime_data
    assert coordinator.update_interval == timedelta(seconds=DEFAULT_SCAN_INTERVAL)


async def test_options_flow_rejects_sub_wbr3_interval(
    hass: HomeAssistant, mock_client_factory, config_entry: MockConfigEntry
) -> None:
    """MIN_SCAN_INTERVAL is a hardware floor, not a UI hint: WBR3 WiFi
    modules reboot when polled faster than ~8s. The NumberSelector bounds
    only constrain the frontend, so the schema must reject the value
    itself — a hand-crafted options payload must not be able to put the
    coordinator into a reboot loop."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    with pytest.raises(InvalidData):
        await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_SCAN_INTERVAL: 1}
        )


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        ({}, DEFAULT_SCAN_INTERVAL),
        ({CONF_SCAN_INTERVAL: 45}, 45),
        # Written before the bounds existed, or edited by hand.
        ({CONF_SCAN_INTERVAL: 1}, MIN_SCAN_INTERVAL),
        ({CONF_SCAN_INTERVAL: 9999}, MAX_SCAN_INTERVAL),
        ({CONF_SCAN_INTERVAL: "not a number"}, DEFAULT_SCAN_INTERVAL),
        ({CONF_SCAN_INTERVAL: None}, DEFAULT_SCAN_INTERVAL),
    ],
)
def test_scan_interval_from_options_clamps(stored: dict, expected: int) -> None:
    """Stored options are clamped, never trusted — an entry that predates
    the bounds must not reach the coordinator unclamped."""
    assert scan_interval_from_options(stored) == expected


def test_options_schema_serializes_for_the_frontend() -> None:
    """The bounds are enforced with vol.All(NumberSelector, ..., vol.Range),
    and a vol.All-wrapped selector is not handled the same way as a bare one
    by voluptuous_serialize. If the wrapper broke serialization the form
    would render as a plain text box (or not at all) while every server-side
    test still passed, so pin the rendered payload here."""
    import voluptuous_serialize
    from homeassistant.helpers import config_validation as cv

    from custom_components.poolex_silverline._config_validation import options_schema

    fields = voluptuous_serialize.convert(
        options_schema(DEFAULT_SCAN_INTERVAL), custom_serializer=cv.custom_serializer
    )
    assert len(fields) == 1
    field = fields[0]
    assert field["name"] == CONF_SCAN_INTERVAL
    assert field["default"] == DEFAULT_SCAN_INTERVAL
    # The number selector survives the wrapper, bounds intact.
    number = field["selector"]["number"]
    assert number["min"] == MIN_SCAN_INTERVAL
    assert number["max"] == MAX_SCAN_INTERVAL
    assert number["unit_of_measurement"] == "s"
