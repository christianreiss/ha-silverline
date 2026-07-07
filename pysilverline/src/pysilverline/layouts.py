"""Thin compatibility shim — DP layouts now live in :mod:`pysilverline.devices`.

The layout symbols moved into the self-registering ``devices/`` registry; this
module re-exports them so existing imports (``models.py``, ``client.py``, the
integration's ``__init__.py``, and the tests) keep resolving from
``pysilverline.layouts`` unchanged.
"""

from __future__ import annotations

from .devices import DpLayout as DpLayout
from .devices import LAYOUT_BY_NAME as LAYOUT_BY_NAME
from .devices import LAYOUT_NANO_FI_3KW as LAYOUT_NANO_FI_3KW
from .devices import LAYOUT_PC_INV_120 as LAYOUT_PC_INV_120
from .devices import LAYOUT_STANDARD as LAYOUT_STANDARD
from .devices import LAYOUT_V34_WFZEIYN as LAYOUT_V34_WFZEIYN
from .devices import layout_for_model as layout_for_model

__all__ = [
    "DpLayout",
    "LAYOUT_BY_NAME",
    "LAYOUT_NANO_FI_3KW",
    "LAYOUT_PC_INV_120",
    "LAYOUT_STANDARD",
    "LAYOUT_V34_WFZEIYN",
    "layout_for_model",
]
