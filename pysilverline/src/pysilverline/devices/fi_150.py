"""Poolex Silverline FI 150, Tuya productId ``b4zr9ugt1q8xn9af``, protocol v3.5.

Reported with a full DP map and a controlled load-transition test by
@squitel (issue #20, 2026-09-09) on a unit commissioned 2026-09-06 and in
daily service. Until 0.5.11 this model had no entry in the registry and fell
back to :data:`LAYOUT_STANDARD`, which is wrong for most of this firmware's
DP block: it published water inlet 10 °C / outlet 6 °C on a pool sitting at
28 °C, a "total operating hours" counter that decreased, "compressor actual
frequency" of 43 Hz with the compressor stopped and drawing 0 A, and a
boolean "water pump" fed from a DP that reads 208-340.

**This firmware is a hybrid.** It keeps the classic family's temperature
numbering for DP 102/103/104 but carries the Full Inverter block
(109/110/111/114/115/120/121) and the installer-config block (124-145) of
the Nano Fi family — see :mod:`.nano_fi`. That is why neither existing
layout fits it and it needs its own.

**The load transition settles the inverter block.** The reporter dropped the
setpoint 29 → 26 °C (compressor stops), then restored it (compressor
restarts after the anti-short-cycle delay), logging every poll:

    time      dp114 dp121 dp120 dp111 dp101 dp103 dp108 dp104 dp105 dp106 dp109 dp110
    09:05:18   820    10   218   208    30    28    35    75     9     6    80    80
    09:07:02   498     3   228   208    30    28    37    72    10     9    35    35
    09:10:02     0     0   232   340    28    28    40    56    17    18     0     0
    09:13:02     0     0   233   340    28    28    43    50    18    21     0     0
    09:20:21   824     0   234   340    28    28    45    44    19    24     0     0
    09:20:51   810     1   231   340    28    28    43    48    18    20    45    26
    09:21:21   826     4   228   340    29    28    42    52    11    18    45    45
    09:23:21   817     9   222   340    30    28    38    57    11    12    80    76
    09:25:21   820    10   220   325    30    28    35    68     9     6    80    80

1. **DP 109 = target frequency, DP 110 = actual frequency.** 109 steps
   first and 110 follows it: 45/26 → 45/45 → 58/58 → 80/76 → 80/80. Same
   pair, same order as the Nano Fi.

2. **DP 108 is not a frequency — it is the IPM (heatsink) temperature.** It
   *rises* 35 → 45 while the unit is stopped and the fan is off, and falls
   back to 35 once the fan restarts: heatsink behaviour with no airflow, and
   the exact opposite of what a compressor frequency does. It is ``T7 IPM
   temperature`` in the OEM status table quoted in :mod:`.nano_fi`. Under
   the standard layout this DP was ``actual_frequency``, which is where the
   "43 Hz with the compressor stopped" reading came from. Mapped to the
   dedicated ``ipm_temp`` field added for it — the one DP this firmware
   exposes that :class:`DpLayout` could not previously express.

3. **DP 114 = fan speed.** ~820 whenever the fan runs, independent of
   compressor frequency (26 → 80 Hz), 0 when off — and it starts *30 s
   before* the compressor (09:20:21: fan 824, frequency 0, current 0). The
   panel's ``Pr Fan speed``.

4. **DP 111 = main EEV opening, in steps.** Parks at 340 when stopped, then
   closes linearly (−5 per 30 s) toward its control point of 208 under
   steady load. The panel's ``1F Main EEV opening``, matching the Nano Fi's
   DP 111 exactly. The standard layout read it as ``water_pump`` and
   published a boolean fed from a 208-340 integer; ``water_pump`` is
   unmapped here, so the "Water pump" binary sensor is gone on this model.
   (An earlier revision of :mod:`..models` added the int-tolerant ``_pump``
   reader specifically to keep that sensor alive on an FI 150 dump — DP 111
   = 320 was read as "pump running". The quantity was wrong, not the
   coercion.)

5. **DP 120 = mains voltage, DP 121 = mains current in whole amps.** 218 V
   at full load → 235 V unloaded, the line drop relaxing as the compressor
   unloads, while DP 121 walks 0 → 10 in lockstep with frequency. The
   standard layout's ``total_hours=120`` produced a monotonic-counter
   entity that decreased — the same misreading corrected for the Nano Fi in
   issue #19. ``ac_current_divisor`` stays 1: 220 V × 10 A ≈ 2.2 kW is the
   right order for this unit, 220 W is not a running compressor.

6. **Water temperatures.** DP 101 converges to pool temperature (28) at rest
   and rises to 30 under load → water **outlet**. DP 103 tracks DP 3 →
   inlet/pool. DP 105 (19 at rest → 9 loaded) → suction gas. DP 106
   converges to ambient (24) at rest and drops to 6 under load → outdoor
   coil. The standard layout reads inlet/outlet off 105/106, which is where
   the impossible "inlet 10 °C / outlet 6 °C on a 28 °C pool" came from.
   No distinct inlet probe is exposed (DP 3 == DP 103), so ``inlet_temp``
   aliases DP 103 rather than leaving the entity permanently unavailable —
   the same aliasing the Nano Fi profile does for ``pool_temp``.

**DP 124-145 is the installer-config block, not refrigeration telemetry.**
Every DP in it held its value across full load, shutdown and idle
(124:45, 125:12, 126:12, 127:0, 128:2, 130:0, 131:2, 132:-1, 133:-8,
137:10, 140:80, 142:40, 145:8) — live circuit measurements do not do that.
Ten of them are wired onto the installer-parameter fields introduced for the
Nano Fi in issue #19, on the strength of three things: the DP numbering is
identical, every value falls inside the range tuya-local declares for that
parameter on the Nano Fi pid, and the two units disagree on the values the
way two commissioned machines with different settings would (the Nano Fi
dump reads 125:8, 126:18, 127:3, 128:1, 130:2, 131:1, 132:-5, 145:7). What
is *not* claimed is a reading off this unit's own code-locked installer
menu, which is what settled the block on the Nano Fi — treat the labels as
inferred from the family until an FI 150 owner cross-checks them, and note
the sensors are read-only and disabled by default either way.

That block is also exactly what the standard layout was publishing as
``condensing_temp`` (124), ``superheat`` (132), ``evaporating_temp`` (133),
``target_superheat`` (137), ``compressor_load`` (140) and
``target_condensing`` (142). Those defaults were themselves inferred from an
FI 150 diagnostics dump back in 0.3.2 ("inferred from refrigeration
engineering against a live FI 150 diagnostics dump") — issue #20's constancy
test is the first evidence that actually bears on them, and it says they are
setpoints. All six are unmapped here. The standard layout is deliberately
left alone: it is shared with the classic PC-SLP090N/JetLine family, whose
DP block has never been tested this way.

**Faults.** DP 13 like the classic family, decoded with
:data:`NANO_FI_FAULT_TABLE` rather than the classic table: this is Full
Inverter firmware, and on that firmware bit 8 is the water-flow switch
(panel code E25), not the classic family's defrost sensor (P1). Inferred
from the family, not confirmed here — no fault has occurred on this unit.
The failure mode of guessing wrong is bounded and asymmetric: with the FI
table a classic-layout water-flow fault (bit 0) surfaces as an unnamed
``bit0`` and raises no Repair card, whereas with the classic table an FI
water-flow fault tells the owner to check a defrost probe while their filter
pump is off — the exact complaint that opened issue #19.

Still unidentified on this firmware, and left unmapped rather than guessed:
DP 127, 130, 138, 141 (all 0), DP 133 (-8), DP 137 (10), DP 140 (80) and
DP 145 (8) beyond the config-block reading above. DP 115 reads 0 throughout;
``defrosting=115`` is carried over from the Nano Fi family (where the 1 ⇒
defrosting decode is hardware-confirmed) and has not been observed non-zero
here — September, 21 °C ambient, no defrost cycle to catch.
"""

from __future__ import annotations

from ..const import NANO_FI_FAULT_TABLE
from .base import DpLayout

#: Poolex Silverline FI 150, Tuya pid b4zr9ugt1q8xn9af (issue #20).
LAYOUT_SILVERLINE_FI_150 = DpLayout(
    outlet_temp=101,
    ambient_temp=102,
    pool_temp=103,
    inlet_temp=103,  # no distinct inlet probe on this firmware — DP 3 == DP 103
    discharge_temp=104,
    suction_temp=105,
    outdoor_coil_temp=106,
    indoor_coil_temp=None,  # DP 108 is the IPM heatsink, not a coil probe
    ipm_temp=108,  # "T7 IPM temperature" — rises with the fan off, see above
    target_frequency=109,
    actual_frequency=110,
    eev_steps=111,  # "1F Main EEV opening", in steps — NOT the pump
    fan_speed=114,
    water_pump=None,  # this firmware exposes no pump DP at all
    condensing_temp=None,
    evaporating_temp=None,
    superheat=None,
    compressor_load=None,
    total_hours=None,  # DP 120 is mains voltage here, not a runtime counter
    target_superheat=None,
    target_condensing=None,
    ac_voltage=120,
    ac_current=121,
    ac_current_divisor=1,  # whole amps: 220 V x 10 A ~ 2.2 kW at 80 Hz
    fault_table=NANO_FI_FAULT_TABLE,  # Full Inverter bit layout, see above
    defrosting=115,
    heating_time=124,
    defrost_time_limit=125,
    defrost_cutout_temp=126,
    heating_start_hysteresis=127,
    heating_end_hysteresis=128,
    cooling_start_hysteresis=130,
    cooling_end_hysteresis=131,
    defrost_temp=132,
    max_temp_limit=142,
    min_temp_limit=145,
)
