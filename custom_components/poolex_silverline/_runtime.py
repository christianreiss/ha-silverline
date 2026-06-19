"""Runtime-today accumulation math for the Poolex Silverline.

A single pure function that the coordinator's ``_tick_runtime`` delegates
to. Keeping the midnight-rollover + accumulation logic here (with ``now``
injected) lets it be unit-tested without Home Assistant fixtures while the
coordinator retains ownership of the mutable accumulator state.
"""

from __future__ import annotations

from datetime import date, datetime

from homeassistant.components.climate.const import HVACAction
from homeassistant.util import dt as dt_util


def accumulate_runtime(
    *,
    now: datetime,
    last_tick: datetime | None,
    local_date: date | None,
    today_seconds: float,
    action: HVACAction | None,
) -> tuple[float, datetime, date]:
    """Return the next ``(today_seconds, last_tick, local_date)`` triple.

    - First observation (``last_tick is None``): anchor the clock; can't
      accumulate an interval without a prior timestamp.
    - Day boundary crossed (``local_date != local_today``): zero the
      counter and re-anchor. Under-counts the few seconds between the last
      pre-midnight tick and the actual midnight instant, but avoids
      attributing any of that time to "today".
    - Otherwise accumulate the gap since the previous tick while the unit
      is HEATING or COOLING; a non-positive delta is ignored.
    """
    local_today = dt_util.as_local(now).date()

    if last_tick is None:
        return (today_seconds, now, local_today)

    if local_date != local_today:
        return (0.0, now, local_today)

    seconds = today_seconds
    if action in (HVACAction.HEATING, HVACAction.COOLING):
        delta = (now - last_tick).total_seconds()
        if delta > 0:
            seconds += delta
    return (seconds, now, local_today)
