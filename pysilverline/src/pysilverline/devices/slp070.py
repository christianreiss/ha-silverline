"""Poolex Silverline FI 70 / PC-SLP070N, Tuya protocol v3.3 (issue #21).

Same OEM platform and the same wire DP numbering as the PC-SLP090N — a
reporter confirmed the ``pc_slp090n`` profile drives an FI 70 correctly, with
DPs 1, 2, 3, 4 and 13 all staying available (issue #21, @TRIAG73,
2026-09-13). Minimal 5-DP firmware: no 101-111 diagnostic block. DP 4 speaks
the standard mode vocabulary (Heat/Cool/Auto/BoostHeat/SilentHeat/BoostCool/
SilentCool), confirmed from the unit's own Tuya schema.

It gets its own layout for exactly one reason: the fault bitmap. The same
reporter read DP 13 == 64 (bit 6) while the wired controller displayed
**Er10**, where the classic family's table names bit 6 the inlet sensor and
prints P3. ``SLP070_FAULT_TABLE`` leaves that one bit undecoded rather than
asserting a label the hardware contradicts — see the table's comment in
``pysilverline.const``.
"""

from __future__ import annotations

from ..const import SLP070_FAULT_TABLE
from .base import DpLayout

#: Poolex Silverline FI 70 / PC-SLP070N — standard DP numbering, own fault table.
LAYOUT_SLP070 = DpLayout(fault_table=SLP070_FAULT_TABLE)
