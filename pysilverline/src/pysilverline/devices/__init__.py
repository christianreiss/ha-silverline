"""Per-firmware DP layout registry.

Re-exports the public layout surface so it keeps resolving from
``pysilverline.devices`` (and, via the thin shim, ``pysilverline.layouts``).
The registry is a plain dict built here from the per-model instances.
"""

from __future__ import annotations

from typing import Final

from .base import DpLayout as DpLayout
from .nano_fi import LAYOUT_NANO_FI_3KW as LAYOUT_NANO_FI_3KW
from .pc_inv_120 import LAYOUT_PC_INV_120 as LAYOUT_PC_INV_120
from .standard import LAYOUT_STANDARD as LAYOUT_STANDARD
from .v34_wfzeiyn import LAYOUT_V34_WFZEIYN as LAYOUT_V34_WFZEIYN

#: Canonical model keys — the persisted ``CONF_MODEL`` values that map to a
#: distinct layout. Defined here (the registry owns them) so the integration
#: imports them instead of duplicating the literal strings across the boundary.
MODEL_STANDARD: Final = "standard"
MODEL_SILVERLINE_V34: Final = "silverline_v34"
MODEL_PC_INV_120: Final = "pc_inv_120v2"
MODEL_NANO_FI_3KW: Final = "nano_fi_3kw"

#: Canonical model key -> layout. Keys are the persisted ``CONF_MODEL`` values.
_REGISTRY: Final[dict[str, DpLayout]] = {
    MODEL_STANDARD: LAYOUT_STANDARD,
    MODEL_SILVERLINE_V34: LAYOUT_V34_WFZEIYN,
    MODEL_PC_INV_120: LAYOUT_PC_INV_120,
    MODEL_NANO_FI_3KW: LAYOUT_NANO_FI_3KW,
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
    "pc_inv_120v2": LAYOUT_PC_INV_120,
    "nano_fi_3kw": LAYOUT_NANO_FI_3KW,
}


def layout_for_model(model_key: str) -> DpLayout:
    """Return the DP layout for a config-entry model key (default: standard)."""
    return get_layout(model_key)


__all__ = [
    "LAYOUT_BY_NAME",
    "LAYOUT_NANO_FI_3KW",
    "LAYOUT_PC_INV_120",
    "LAYOUT_STANDARD",
    "LAYOUT_V34_WFZEIYN",
    "MODEL_NANO_FI_3KW",
    "MODEL_PC_INV_120",
    "MODEL_SILVERLINE_V34",
    "MODEL_STANDARD",
    "DpLayout",
    "get_layout",
    "layout_for_model",
]
