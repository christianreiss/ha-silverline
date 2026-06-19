"""Per-firmware DP layout registry.

Re-exports the public layout surface so it keeps resolving from
``pysilverline.devices`` (and, via the thin shim, ``pysilverline.layouts``).
The registry is a plain dict built here from the per-model instances.
"""

from __future__ import annotations

from .base import DpLayout as DpLayout
from .standard import LAYOUT_STANDARD as LAYOUT_STANDARD
from .v34_wfzeiyn import LAYOUT_V34_WFZEIYN as LAYOUT_V34_WFZEIYN

#: Canonical model key -> layout. Keys are the persisted ``CONF_MODEL`` values.
_REGISTRY: dict[str, DpLayout] = {
    "standard": LAYOUT_STANDARD,
    "silverline_v34": LAYOUT_V34_WFZEIYN,
}


def get_layout(model_key: str) -> DpLayout:
    """Return the DP layout for ``model_key`` (default: the standard layout).

    Unknown keys (including ``""`` and ``"pc_slp090n"``) fall back to
    :data:`LAYOUT_STANDARD`, so identity is preserved.
    """
    return _REGISTRY.get(model_key, LAYOUT_STANDARD)


#: Legacy-alias mapping. Old keys preserved, pointing at the same layout objects.
LAYOUT_BY_NAME: dict[str, DpLayout] = {
    "standard": LAYOUT_STANDARD,
    "v34_wfzeiyn": LAYOUT_V34_WFZEIYN,
}


def layout_for_model(model_key: str) -> DpLayout:
    """Return the DP layout for a config-entry model key (default: standard)."""
    return get_layout(model_key)


__all__ = [
    "DpLayout",
    "LAYOUT_BY_NAME",
    "LAYOUT_STANDARD",
    "LAYOUT_V34_WFZEIYN",
    "get_layout",
    "layout_for_model",
]
