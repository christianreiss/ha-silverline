"""OEM Poolstar PC-INV-120V2 (Poolex Silverline FI 120 V2 sibling), protocol v3.3.

Reported in issue #5 (froggy974). This minimal-DP firmware exposes only the
core DPs (1, 2, 3, 4, 9) but reports DP 3 (current water temperature) in
tenths of a degree — raw 277 = 27.7 °C, cross-checked by the reporter against
two DS18B20 probes (27.0 / 27.1 °C). DP 2 (setpoint) stays whole °C, so the
÷10 scaling is applied to the current-temp field only via the layout divisor.

The diagnostic DP numbering is inherited from the standard layout because this
firmware does not expose DPs 101+ at all (they resolve to None regardless).
"""

from __future__ import annotations

from .base import DpLayout

#: PC-INV-120V2 — standard numbering, current-temp DP in tenths of a degree.
LAYOUT_PC_INV_120 = DpLayout(temp_current_divisor=10)
