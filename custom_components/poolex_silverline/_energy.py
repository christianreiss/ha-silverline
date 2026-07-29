"""Energy-accumulation math for the Poolex Silverline.

A single pure function that the coordinator's ``_tick_energy`` delegates to,
mirroring ``_runtime.accumulate_runtime``: ``now`` is injected so the
integration can be unit-tested without Home Assistant fixtures while the
coordinator retains ownership of the mutable accumulator state.

The firmware exposes instantaneous line voltage and current but no energy
register, so kWh is integrated here from the sampled power. Sampling is
push-driven and therefore irregular; the trapezoidal rule (averaging the
two endpoint powers) tracks ramping loads far better than a rectangular
sum, which would over-count on rising loads and under-count on falling
ones by the full sample interval.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

#: Gaps longer than this are treated as a data hole, not a load period.
#: Without it, a device offline for six hours would come back and bill the
#: entire outage at whatever power it happened to report on reconnect.
#: Scaled off the poll interval so a slow-polling entry isn't punished.
MAX_GAP_MULTIPLE = 5
MIN_MAX_GAP = timedelta(seconds=300)


def max_energy_gap(scan_interval: float) -> timedelta:
    """Return the longest sample gap still treated as continuous operation."""
    return max(timedelta(seconds=scan_interval * MAX_GAP_MULTIPLE), MIN_MAX_GAP)


def accumulate_energy(
    *,
    now: datetime,
    last_tick: datetime | None,
    last_power_w: float | None,
    total_kwh: float,
    power_w: float | None,
    max_gap: timedelta,
) -> tuple[float, datetime | None, float | None]:
    """Return the next ``(total_kwh, last_tick, last_power_w)`` triple.

    - No power reading (``power_w is None``): drop the anchor. The next
      reading starts a fresh interval rather than bridging the blind spot.
    - First observation, or a gap longer than ``max_gap``: anchor only.
    - Otherwise add the trapezoidal area under the power curve. A
      non-positive delta is ignored so a clock adjustment cannot rewind or
      double-count the counter, which is TOTAL_INCREASING.
    """
    if power_w is None:
        return (total_kwh, None, None)

    if last_tick is None or last_power_w is None:
        return (total_kwh, now, power_w)

    delta = now - last_tick
    if timedelta() < delta <= max_gap:
        average_w = (last_power_w + power_w) / 2
        # W * s -> kWh
        total_kwh += average_w * delta.total_seconds() / 3_600_000

    return (total_kwh, now, power_w)


def is_usable_power(power_w: float | None) -> bool:
    """Reject readings that would corrupt a monotonic counter.

    NaN propagates irreversibly through the running total, and a negative
    product means a DP is not carrying what the layout claims — neither can
    be undone once added.
    """
    return power_w is not None and math.isfinite(power_w) and power_w >= 0
