"""Guards for regressions that a behavioural test would not have caught.

Each test here pins an invariant that an audit of issues #1-#21 found either
already broken or held only by luck. They are grouped by the class of silent
failure rather than by issue, because every one of them recurs the same way:
a new model profile ships and one of the several places it has to be wired is
missed, with the whole suite still green.
"""

from __future__ import annotations

import pathlib
import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.select import (
    ATTR_OPTION,
    SERVICE_SELECT_OPTION,
)
from homeassistant.components.select import (
    DOMAIN as SELECT_DOMAIN,
)
from homeassistant.const import ATTR_ENTITY_ID, CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pysilverline.devices import _REGISTRY as LAYOUT_REGISTRY
from pysilverline.layouts import LAYOUT_STANDARD
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.poolex_silverline._config_validation import (
    _KNOWN_POOLEX_PRODUCT_KEYS,
)
from custom_components.poolex_silverline.const import (
    CONF_DEVICE_ID,
    CONF_LOCAL_KEY,
    CONF_MODEL,
    DEVICE_PROFILES,
    DOMAIN,
)
from pysilverline import DeviceState

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Discovery allow-list: a documented model whose productKey is not allow-listed
# ---------------------------------------------------------------------------


def test_every_documented_product_key_is_allowlisted() -> None:
    """Every productKey README names must pass the discovery filter.

    ``config_flow.async_step_integration_discovery`` aborts with
    ``unsupported_product`` for any productKey outside
    ``_KNOWN_POOLEX_PRODUCT_KEYS`` — *before* the already-configured branch, so
    an omission also costs those owners the Gold ``discovery-update-info`` host
    rewrite when their DHCP lease moves. The FI 150's pid (issue #20) was
    documented, profiled, sensor-mapped and tested for three releases while
    still missing from this list, because nothing tied the two together.
    """
    readme = (_REPO_ROOT / "README.md").read_text()
    documented = set(re.findall(r"productKey\s+`?([A-Za-z0-9]{16,22})`?", readme))
    assert documented, "README no longer names any productKey — update this guard"
    missing = documented - _KNOWN_POOLEX_PRODUCT_KEYS
    assert not missing, (
        f"productKeys documented in README but not allow-listed: {sorted(missing)}"
    )


def test_fi_150_product_key_is_allowlisted() -> None:
    """Issue #20's pid specifically — it is also the JetLine FI new control
    board from issue #7, so the omission covered two model families."""
    assert "b4zr9ugt1q8xn9af" in _KNOWN_POOLEX_PRODUCT_KEYS


# ---------------------------------------------------------------------------
# Library/integration boundary: a layout with no profile, or the reverse
# ---------------------------------------------------------------------------


def test_every_library_model_key_has_an_integration_profile() -> None:
    """Each key in pysilverline's layout registry has a ``DeviceProfile``.

    The two live in different packages and are joined only by a string. A
    layout that ships without its profile gets the default clamps and preset
    vocabulary; a profile without its layout reads DPs off the standard map.
    ``MODEL_STANDARD`` is the documented exception — it is the fallback, not a
    selectable model.
    """
    from pysilverline.devices import MODEL_STANDARD

    unprofiled = {key for key in LAYOUT_REGISTRY if key != MODEL_STANDARD} - set(
        DEVICE_PROFILES
    )
    assert not unprofiled, (
        f"layout registered with no DeviceProfile: {sorted(unprofiled)}"
    )


def test_every_profile_is_selectable_and_labelled() -> None:
    """Each ``DeviceProfile`` has a non-empty display name for the selector."""
    for key, profile in DEVICE_PROFILES.items():
        assert profile.display_name, f"{key} has no display_name"


# ---------------------------------------------------------------------------
# Issue #10 / nano_5kw: a profile that overrides only the "none" preset
# ---------------------------------------------------------------------------


async def _setup_profile(
    hass: HomeAssistant, model_key: str, dps: dict[str, object]
) -> tuple[MockConfigEntry, MagicMock]:
    """Bring up a config entry pinned to ``model_key`` with a given DP map."""
    device_id = "bf99999999abcdefghijkl"
    state = DeviceState.from_dps(dps)
    client = MagicMock()
    client.host, client.port, client.device_id = "10.0.0.80", 6668, device_id
    client.connected, client.state = True, state
    client.detected_version = "3.3"
    client.dp_layout = LAYOUT_STANDARD
    for method in ("connect", "disconnect", "set_dp", "set_multiple"):
        setattr(client, method, AsyncMock(return_value=None))
    client.get_status = AsyncMock(return_value=state)
    client.add_listener = MagicMock(return_value=lambda: None)
    client.add_connection_listener = MagicMock(return_value=lambda: None)

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=device_id,
        data={
            CONF_HOST: "10.0.0.80",
            CONF_PORT: 6668,
            CONF_DEVICE_ID: device_id,
            CONF_LOCAL_KEY: "0123456789abcdef",
            CONF_MODEL: model_key,
        },
        version=1,
        minor_version=3,
    )
    entry.add_to_hass(hass)
    with patch(
        "custom_components.poolex_silverline.SilverlineClient", return_value=client
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry, client


@pytest.mark.parametrize(
    ("model_key", "mode_string", "expected"),
    [
        ("steinbach_silent_mini", "Heating", "Heating"),
        ("nano_5kw", "Heat", "Heat"),
    ],
)
async def test_preset_select_falls_back_for_partial_preset_maps(
    hass: HomeAssistant, model_key: str, mode_string: str, expected: str
) -> None:
    """Picking boost on a profile with only a "none" preset writes the plain
    mode string instead of raising.

    ``steinbach_silent_mini`` (issue #10) was the first profile to override
    only ``PRESET_NONE`` — its firmware's boost/eco DP-4 vocabulary is
    unconfirmed, and const.py says so: "falls back to the plain
    Heating/Cooling string for those presets too". ``climate._mode_string_for``
    honoured that with ``table.get(preset, table[PRESET_NONE])``; the select
    entity indexed the map directly and raised ``KeyError``. ``nano_5kw`` has
    the identical shape, so this pins both.
    """
    entry, client = await _setup_profile(
        hass, model_key, {"1": True, "2": 28, "3": 26, "4": mode_string, "13": 0}
    )
    registry = er.async_get(hass)
    preset = next(
        e.entity_id
        for e in er.async_entries_for_config_entry(registry, entry.entry_id)
        if e.unique_id.endswith("_preset_mode")
    )
    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: preset, ATTR_OPTION: "boost"},
        blocking=True,
    )
    client.set_multiple.assert_awaited()
    assert client.set_multiple.await_args.args[0] == {4: expected}


# ---------------------------------------------------------------------------
# Issue #6 / #21: the first-poll latch race on minimal 5-DP firmware
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model_key", ["jetline_fi", "pc_slp090n", "pc_slp070n", "nano_5kw"]
)
async def test_five_dp_profiles_register_controls_on_a_partial_first_poll(
    hass: HomeAssistant, model_key: str
) -> None:
    """A power cycle that hides DP 2 must not cost the target-temperature entity.

    Platforms register once, from ``supported_dps`` as it stands at setup. On
    "Other / Unknown" the FI 70 reporter's first poll after a power cycle came
    back with only DPs 1, 3 and 4, the set latched without DP 2, and the
    setpoint entity never appeared (issue #21). Every profile for a minimal
    5-DP firmware pins a floor to defeat that — ``jetline_fi`` did not, despite
    issue #6 reporting exactly that firmware.
    """
    entry, _ = await _setup_profile(hass, model_key, {"1": True, "3": 28, "4": "Heat"})
    registry = er.async_get(hass)
    unique_ids = {
        e.unique_id for e in er.async_entries_for_config_entry(registry, entry.entry_id)
    }
    assert any(uid.endswith("_target_temperature") for uid in unique_ids), (
        f"{model_key}: target-temperature entity missing after a partial first poll"
    )
