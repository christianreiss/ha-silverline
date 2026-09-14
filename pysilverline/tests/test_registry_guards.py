"""Structural guards that keep the layout registry and its shim in step.

These pin invariants no behavioural test touches, because every one of them
fails silently: a layout that ships without its re-export, a model key that
exists on one side of the library/integration boundary only, or a
``__version__`` that drifts from the version actually published to PyPI. Each
is a class of bug the audit of issues #1-#21 found had already slipped through
at least once.
"""

from __future__ import annotations

import pathlib
import tomllib

import pysilverline
import pysilverline.devices as devices
import pysilverline.layouts as layouts

_REPO_LIB_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_version_matches_pyproject() -> None:
    """``__version__`` must equal the version hatchling publishes.

    They are two independent literals: ``release.sh`` and the PyPI workflow
    read pyproject, while the integration's diagnostics dump reports
    ``pysilverline.__version__``. Nothing else ties them together, so a bump
    that touches one and not the other ships a wheel that misreports itself.
    (``test_main`` cannot catch this — it imports ``__version__`` and asserts
    the CLI printed that same value.)
    """
    pyproject = tomllib.loads((_REPO_LIB_ROOT / "pyproject.toml").read_text())
    assert pysilverline.__version__ == pyproject["project"]["version"]


def test_every_registered_layout_is_exported_from_the_shim() -> None:
    """Every ``LAYOUT_*`` in the registry is re-exported by ``layouts``.

    ``pysilverline.layouts`` is the compatibility path the integration, the
    models module and the older tests still import from. A layout added to
    ``devices/`` but not re-exported here is invisible on that path, and no
    behavioural test notices because nothing imports the new name from the
    shim yet — exactly how ``LAYOUT_SLP070`` (issue #21) shipped asymmetric.

    Derived from the module namespace and ``_REGISTRY``, never from the
    hand-maintained ``__all__``: a layout left out of *both* lists would
    otherwise be invisible to its own guard.
    """
    registered = {
        name
        for name, value in vars(devices).items()
        if name.startswith("LAYOUT_") and isinstance(value, devices.DpLayout)
    }
    assert registered, "no LAYOUT_* objects found in pysilverline.devices"
    missing = {name for name in registered if not hasattr(layouts, name)}
    assert not missing, f"layouts.py does not re-export: {sorted(missing)}"
    for name in registered:
        assert getattr(layouts, name) is getattr(devices, name), (
            f"layouts.{name} is not the same object as devices.{name}"
        )
    # Every layout the registry actually resolves to must be reachable by
    # name through the shim — catches a layout registered under a key but
    # never bound to a module-level LAYOUT_* name at all.
    # DpLayout carries a dict field, so it is unhashable — compare by identity.
    exported = [id(getattr(layouts, name)) for name in registered]
    unreachable = [
        key for key, layout in devices._REGISTRY.items() if id(layout) not in exported
    ]
    assert not unreachable, (
        f"registry keys whose layout is not exported from layouts.py: {unreachable}"
    )


def test_every_model_key_resolves_to_its_own_layout() -> None:
    """Each ``MODEL_*`` constant round-trips through ``get_layout``.

    ``get_layout`` falls back to ``LAYOUT_STANDARD`` for unknown keys, so a
    model constant that was never registered resolves to a plausible-looking
    layout instead of raising — the failure mode issue #20 hit in the field,
    where an unregistered model silently read its DPs off the wrong map.
    """
    model_names = [
        name
        for name, value in vars(devices).items()
        if name.startswith("MODEL_") and isinstance(value, str)
    ]
    assert model_names, "no MODEL_* constants defined in pysilverline.devices"
    for name in model_names:
        key = getattr(devices, name)
        if name == "MODEL_STANDARD":
            # The standard layout IS the fallback; it has no distinct entry.
            assert devices.get_layout(key) is devices.LAYOUT_STANDARD
            continue
        assert devices.get_layout(key) is not devices.LAYOUT_STANDARD, (
            f"{name} ({key!r}) is not in the registry — it silently falls "
            "back to LAYOUT_STANDARD"
        )


def test_legacy_alias_map_agrees_with_the_registry() -> None:
    """``LAYOUT_BY_NAME`` (legacy aliases) must not drift from ``get_layout``."""
    for alias, layout in devices.LAYOUT_BY_NAME.items():
        assert devices.get_layout(alias) is layout, (
            f"LAYOUT_BY_NAME[{alias!r}] and get_layout({alias!r}) disagree"
        )
