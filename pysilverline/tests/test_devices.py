"""Pin the layout-registry collapse: canonical key, defaults, object identity."""

from __future__ import annotations

from dataclasses import fields

from pysilverline.const import FAULT_BIT_NAMES, STANDARD_FAULT_TABLE
from pysilverline.devices import LAYOUT_SLP070, get_layout
from pysilverline.layouts import (
    LAYOUT_BY_NAME,
    LAYOUT_NANO_5KW,
    LAYOUT_NANO_FI_3KW,
    LAYOUT_PC_INV_120,
    LAYOUT_SILVERLINE_FI_150,
    LAYOUT_STANDARD,
    LAYOUT_V34_WFZEIYN,
    layout_for_model,
)


def test_layout_for_model_canonical_key() -> None:
    assert layout_for_model("silverline_v34") is LAYOUT_V34_WFZEIYN


def test_layout_for_model_pc_inv_120v2_key() -> None:
    layout = layout_for_model("pc_inv_120v2")
    assert layout is LAYOUT_PC_INV_120
    assert layout.temp_current_divisor == 10


def test_standard_layout_has_no_temp_scaling() -> None:
    assert LAYOUT_STANDARD.temp_current_divisor == 1


def test_layout_for_model_standard_key() -> None:
    assert layout_for_model("standard") is LAYOUT_STANDARD


def test_layout_for_model_empty_defaults_to_standard() -> None:
    assert layout_for_model("") is LAYOUT_STANDARD


def test_layout_for_model_unknown_defaults_to_standard() -> None:
    assert layout_for_model("pc_slp090n") is LAYOUT_STANDARD


def test_layout_by_name_legacy_aliases() -> None:
    assert LAYOUT_BY_NAME["standard"] is LAYOUT_STANDARD
    assert LAYOUT_BY_NAME["v34_wfzeiyn"] is LAYOUT_V34_WFZEIYN


def test_get_layout_canonical_and_default() -> None:
    assert get_layout("silverline_v34") is LAYOUT_V34_WFZEIYN
    assert get_layout("anything-else") is LAYOUT_STANDARD


def test_layout_for_model_nano_fi_3kw_key() -> None:
    assert layout_for_model("nano_fi_3kw") is LAYOUT_NANO_FI_3KW


def test_layout_for_model_pc_slp070n_key() -> None:
    assert layout_for_model("pc_slp070n") is LAYOUT_SLP070
    assert LAYOUT_BY_NAME["pc_slp070n"] is LAYOUT_SLP070


def test_slp070_keeps_standard_dp_numbering() -> None:
    """The FI 70 differs from the standard layout ONLY in its fault table.

    Every wire DP is the same as the PC-SLP090N's — the reporter ran their
    FI 70 on the pc_slp090n profile successfully (issue #21). If a future
    edit renumbers a DP here it is a mistake, not a model difference.
    """
    for f in fields(LAYOUT_STANDARD):
        if f.name == "fault_table":
            continue
        assert getattr(LAYOUT_SLP070, f.name) == getattr(LAYOUT_STANDARD, f.name), (
            f.name
        )


def test_slp070_fault_table_leaves_bit6_undecoded() -> None:
    """Bit 6 must stay unnamed and uncoded on the FI 70.

    The reporter's unit read DP 13 == 64 while its own panel showed Er10,
    contradicting the classic table's "inlet sensor / P3" (issue #21).
    Naming it would raise a Repair card for a fault the hardware disputes.
    Every other bit is carried over from the classic table unchanged.
    """
    table = LAYOUT_SLP070.fault_table
    assert 6 not in table.names
    assert 6 not in table.codes
    assert dict(table.names) == {
        bit: name for bit, name in FAULT_BIT_NAMES.items() if bit != 6
    }
    assert dict(table.codes) == {
        bit: code for bit, code in STANDARD_FAULT_TABLE.codes.items() if bit != 6
    }


def test_v34_wfzeiyn_dp_mapping() -> None:
    """Pin the v3.4 wfzeiyn DP mapping from live hardware: DP 120/121 are
    electrical diagnostics, not a runtime-hours counter."""
    layout = LAYOUT_V34_WFZEIYN
    assert layout.total_hours is None
    assert layout.ac_voltage == 120
    assert layout.ac_current == 121
    # This layout's condensing_temp/superheat/target_condensing (124/132/142)
    # collide numerically with the Nano Fi 3kW's config-setpoint DPs
    # (issue #19). heating_time etc. must stay unmapped here — a
    # dp_keys-only gate on the number platform would otherwise attach a
    # "Heating time" entity to this firmware's condensing-temp DP.
    assert layout.heating_time is None
    assert layout.defrost_temp is None
    assert layout.max_temp_limit is None


def test_ac_current_divisor_defaults_to_whole_amps() -> None:
    """Every AC-capable layout reads DP 121 as whole amps.

    On the Nano Fi this is hardware-confirmed rather than a default: a field
    log of the unit under load (issue #19, 2026-08-22) walks DP 121 through
    0/1/2/3/4 in lockstep with compressor frequency, which is a whole-amp
    field — tenths would read in the tens across that same envelope. The
    other two layouts remain unconfirmed and are pinned only so that
    flipping one to tenths is a deliberate, visible change rather than a
    silent 10x shift in everyone's derived power and energy.

    tuya-local declares scale: 10 for a sibling product, so this constant
    invites a well-meaning "fix". It is not one.
    """
    assert LAYOUT_STANDARD.ac_current_divisor == 1
    for layout in (LAYOUT_V34_WFZEIYN, LAYOUT_NANO_FI_3KW):
        assert layout.ac_current == 121
        assert layout.ac_current_divisor == 1


def test_nano_fi_leaves_dp_102_unmapped() -> None:
    """DP 102 is a manual-override switch, not the pump's running state.

    The tempting mapping — panel line "Pu Water pump state", schema name
    ``pump_manual`` — is refuted by the wire: DP 102 reads false in the same
    query frame that shows the compressor turning at 65 Hz (issue #19 field
    log, 2026-08-22), and a pool heat pump cannot run without circulation.
    Anything that maps DP 102 to a "Water pump" sensor is repeating this
    profile's founding mistake with a different DP, so pin the absence.
    """
    assert LAYOUT_NANO_FI_3KW.water_pump is None
    assert 102 not in [
        getattr(LAYOUT_NANO_FI_3KW, f.name)
        for f in fields(LAYOUT_NANO_FI_3KW)
        if isinstance(getattr(LAYOUT_NANO_FI_3KW, f.name), int)
    ]


def test_ac_current_divisor_scales_tenths_of_an_amp() -> None:
    """A layout that does report tenths yields real amps as a float."""
    from dataclasses import replace

    from pysilverline import DeviceState

    tenths = replace(LAYOUT_V34_WFZEIYN, ac_current_divisor=10)
    state = DeviceState.from_dps({"120": 232, "121": 43}, layout=tenths)
    assert state.ac_current == 4.3


def test_layout_by_name_nano_fi_3kw_alias() -> None:
    assert LAYOUT_BY_NAME["nano_fi_3kw"] is LAYOUT_NANO_FI_3KW


def test_nano_fi_3kw_dp_mapping() -> None:
    """Pin the DP mapping cross-checked against the official Tuya schema
    for pid am4nomaadnhwvekq — regression guard against re-introducing the
    "other"-fallback bug (DP 120 read as total_hours instead of ac_voltage,
    inlet/outlet/ambient temps swapped with the outdoor-coil DPs)."""
    layout = LAYOUT_NANO_FI_3KW
    assert layout.inlet_temp == 103
    assert layout.outlet_temp == 104
    assert layout.outdoor_coil_temp == 105
    assert layout.ambient_temp == 106
    assert layout.indoor_coil_temp == 108
    assert layout.actual_frequency == 110
    # DP 111 is the main EEV opening in steps, hardware-labeled by a Fi 5kW
    # owner against the unit's own "1F Main EEV opening" parameter (issue
    # #19). It was routed through water_pump/water_pump_rpm until 0.5.7 and
    # published as "Circulation pump speed" in RPM — wrong quantity, wrong
    # unit. This firmware exposes no circulation-pump DP at all.
    assert layout.eev_steps == 111
    assert layout.water_pump is None
    assert layout.suction_temp == 117
    # Confirmed on a Nano Fi 5kW (same pid, larger sibling) via issue #19.
    assert layout.target_frequency == 109
    assert layout.defrosting == 115
    # DP 124/132/142 are configuration setpoints on this pid (heating time,
    # defrost temp, max temp — tuya-local schema, issue #19), not
    # condensing_temp/superheat/target_condensing telemetry. Must stay
    # unmapped rather than reintroduce the earlier mislabeling.
    assert layout.condensing_temp is None
    assert layout.superheat is None
    assert layout.target_condensing is None
    # DP 120 on this firmware is AC line voltage, not a runtime-hours
    # counter — must stay unmapped rather than reused for total_hours.
    assert layout.total_hours is None
    # No distinct pool-water probe — aliased to the core DP 3 reading
    # (same value already used for temp_current) instead of left unmapped.
    assert layout.pool_temp == 3
    assert layout.temp_current_divisor == 1
    assert layout.ac_voltage == 120
    assert layout.ac_current == 121
    # Installer-menu setpoints (issue #19, richardc1983) — wired onto
    # dedicated fields distinct from the reverted condensing_temp/superheat/
    # target_condensing above. Hardware-confirmed on read (they match the
    # code-locked installer menu) and hardware-confirmed to reject writes,
    # so the integration publishes them read-only.
    assert layout.heating_time == 124
    assert layout.defrost_time_limit == 125
    assert layout.defrost_cutout_temp == 126
    assert layout.heating_start_hysteresis == 127
    assert layout.heating_end_hysteresis == 128
    assert layout.cooling_start_hysteresis == 130
    assert layout.cooling_end_hysteresis == 131
    assert layout.defrost_temp == 132
    assert layout.max_temp_limit == 142
    assert layout.min_temp_limit == 145


def test_layout_for_model_nano_5kw_key() -> None:
    assert layout_for_model("nano_5kw") is LAYOUT_NANO_5KW


def test_layout_by_name_nano_5kw_alias() -> None:
    assert LAYOUT_BY_NAME["nano_5kw"] is LAYOUT_NANO_5KW


def test_nano_5kw_dp_mapping() -> None:
    """Pin the Nano 5kW family's minimal DP mapping (issue #16 / #18):
    fault lives on DP 21 instead of the standard DP 13, and every
    diagnostic field this hardware doesn't expose stays unmapped rather
    than inheriting a standard-layout default that would coincide with an
    unrelated DP (e.g. DP 101 being a boolean, not suction_temp, on this
    firmware — issue #18)."""
    layout = LAYOUT_NANO_5KW
    assert layout.fault == 21
    assert layout.suction_temp is None
    assert layout.outlet_temp is None
    assert layout.ambient_temp is None
    assert layout.pool_temp is None
    assert layout.discharge_temp is None
    assert layout.inlet_temp is None
    assert layout.actual_frequency is None
    assert layout.target_frequency is None
    assert layout.water_pump is None
    assert layout.total_hours is None
    assert layout.ac_voltage is None
    assert layout.ac_current is None


def test_layout_for_model_fi_150_key() -> None:
    assert layout_for_model("fi_150") is LAYOUT_SILVERLINE_FI_150


def test_layout_by_name_fi_150_alias() -> None:
    assert LAYOUT_BY_NAME["fi_150"] is LAYOUT_SILVERLINE_FI_150


def test_fi_150_dp_mapping() -> None:
    """Pin the Silverline FI 150 mapping from the issue #20 load-transition
    test (pid b4zr9ugt1q8xn9af). Until 0.5.11 this model had no layout and
    fell back to LAYOUT_STANDARD, which produced physically impossible
    readings; every assertion below is one of those misreadings."""
    layout = LAYOUT_SILVERLINE_FI_150

    # Water temps: the standard layout read inlet/outlet off DP 105/106 and
    # reported "inlet 10 °C / outlet 6 °C" on a pool sitting at 28 °C.
    assert layout.outlet_temp == 101
    assert layout.ambient_temp == 102
    assert layout.pool_temp == 103
    # No distinct inlet probe on this firmware (DP 3 == DP 103), so the field
    # aliases the pool DP rather than being left unmapped.
    assert layout.inlet_temp == 103
    assert layout.discharge_temp == 104
    assert layout.suction_temp == 105
    assert layout.outdoor_coil_temp == 106

    # DP 108 rose 35 -> 45 while the unit was stopped and the fan was off,
    # then fell back once the fan restarted: the IPM heatsink, not a
    # frequency. Under the standard layout it was actual_frequency and read
    # 43 Hz with the compressor stopped and drawing 0 A.
    assert layout.ipm_temp == 108
    assert layout.indoor_coil_temp is None
    assert layout.target_frequency == 109
    assert layout.actual_frequency == 110

    # DP 111 parks at 340 stopped and closes toward 208 under load — the main
    # EEV opening, as on the Nano Fi. The standard layout published it as a
    # boolean "water pump" fed from a 208-340 integer.
    assert layout.eev_steps == 111
    assert layout.water_pump is None
    assert layout.fan_speed == 114
    assert layout.defrosting == 115

    # DP 120/121 are mains voltage and whole amps (218 V at full load ->
    # 235 V unloaded, current 0 -> 10 in lockstep with frequency), not the
    # standard layout's lifetime hour counter — which decreased.
    assert layout.ac_voltage == 120
    assert layout.ac_current == 121
    assert layout.ac_current_divisor == 1
    assert layout.total_hours is None

    # DP 124-145 held every value across full load, shutdown and idle: the
    # installer-config block, not refrigeration telemetry. The standard
    # layout published six of them as circuit measurements.
    assert layout.condensing_temp is None
    assert layout.evaporating_temp is None
    assert layout.superheat is None
    assert layout.compressor_load is None
    assert layout.target_superheat is None
    assert layout.target_condensing is None
    assert layout.heating_time == 124
    assert layout.defrost_time_limit == 125
    assert layout.defrost_cutout_temp == 126
    assert layout.heating_start_hysteresis == 127
    assert layout.heating_end_hysteresis == 128
    assert layout.cooling_start_hysteresis == 130
    assert layout.cooling_end_hysteresis == 131
    assert layout.defrost_temp == 132
    assert layout.max_temp_limit == 142
    assert layout.min_temp_limit == 145

    assert layout.temp_current_divisor == 1


def test_fi_150_decodes_the_issue20_field_dump() -> None:
    """Replay the DP_QUERY dump from issue #20 verbatim.

    The per-field assertions above pin the map; this pins what an owner
    actually sees, against the exact payload their unit sent while heating.
    """
    from pysilverline import DeviceState

    raw = {
        "1": True,
        "2": 29,
        "3": 28,
        "4": "Heat",
        "13": 0,
        "101": 30,
        "102": 21,
        "103": 28,
        "104": 69,
        "105": 10,
        "106": 6,
        "108": 35,
        "109": 80,
        "110": 80,
        "111": 305,
        "114": 817,
        "115": 0,
        "120": 220,
        "121": 10,
        "124": 45,
        "125": 12,
        "126": 12,
        "127": 0,
        "128": 2,
        "130": 0,
        "131": 2,
        "132": -1,
        "133": -8,
        "137": 10,
        "138": 0,
        "140": 80,
        "141": 0,
        "142": 40,
        "145": 8,
    }
    state = DeviceState.from_dps(raw, layout=LAYOUT_SILVERLINE_FI_150)

    # The four readings the bug report led with.
    assert state.inlet_temp == 28  # was 10 under LAYOUT_STANDARD
    assert state.outlet_temp == 30  # was 6
    assert state.total_hours is None  # was 220 and decreasing
    assert state.water_pump is None  # was True, from a DP reading 208-340

    assert state.pool_temp == 28
    assert state.ambient_temp == 21
    assert state.discharge_temp == 69
    assert state.suction_temp == 10
    assert state.outdoor_coil_temp == 6
    assert state.ipm_temp == 35
    assert state.actual_frequency == 80
    assert state.target_frequency == 80
    assert state.eev_steps == 305
    assert state.fan_speed == 817
    assert state.defrosting is False
    # 220 V x 10 A ~ 2.2 kW at 80 Hz, the right order for this unit.
    assert state.ac_voltage == 220
    assert state.ac_current == 10
    # Installer setpoints, not circuit telemetry.
    assert state.heating_time == 45
    assert state.min_temp_limit == 8
    assert state.condensing_temp is None
    assert state.evaporating_temp is None
    assert state.superheat is None
    assert state.compressor_load is None


def test_fi_150_uses_the_full_inverter_fault_table() -> None:
    """DP 13 like the classic family, decoded with the FI bit layout.

    Inferred from the family rather than confirmed on this unit (no fault
    has occurred on it), and the asymmetry is why: with the FI table a
    classic-layout water-flow fault surfaces as an unnamed bit and raises no
    Repair card, while with the classic table an FI water-flow fault tells
    the owner to check a defrost probe — issue #19's original complaint.
    """
    from pysilverline.const import NANO_FI_FAULT_TABLE

    assert LAYOUT_SILVERLINE_FI_150.fault == 13
    assert LAYOUT_SILVERLINE_FI_150.fault_table is NANO_FI_FAULT_TABLE


def test_default_layouts_still_map_fault_to_dp13() -> None:
    """Every pre-existing layout must default to `fault=13` — the Nano 5kW
    override must not silently change what any other model reads."""
    for layout in (
        LAYOUT_STANDARD,
        LAYOUT_V34_WFZEIYN,
        LAYOUT_PC_INV_120,
        LAYOUT_NANO_FI_3KW,
        LAYOUT_SILVERLINE_FI_150,
    ):
        assert layout.fault == 13


def test_fault_table_travels_with_the_layout_not_the_dp_number() -> None:
    """The fault DP number does not identify the bit layout.

    Full Inverter firmware (Nano Fi 3kW/5kW) reports on DP 13 like the
    classic PC-SLP090N family but carries water flow on bit 8, where the
    classic family carries the defrost sensor — hardware-confirmed in issue
    #19 by inducing the fault (filter pump cut, app said "Fault of Water
    Flow Switch", DP 13 read 256). Selecting a table by DP number therefore
    cannot work, and decoding an FI unit with the classic table raised
    "Defrost sensor fault (P1)" for a stopped filter pump.
    """
    from pysilverline.const import (
        NANO_5KW_FAULT_TABLE,
        NANO_FI_FAULT_TABLE,
        STANDARD_FAULT_TABLE,
    )

    # Two layouts, same fault DP, different meaning for bit 8.
    assert LAYOUT_NANO_FI_3KW.fault == LAYOUT_STANDARD.fault == 13
    assert LAYOUT_STANDARD.fault_table.names[8] == "defrost_sensor"
    assert LAYOUT_NANO_FI_3KW.fault_table.names[8] == "water_flow"
    assert LAYOUT_STANDARD.fault_table.codes[8] == "P1"
    # ...and print a different service code for it, too: the FI wired
    # controller displays E25 where the classic panel displays P1 (issue #19,
    # panel photograph 2026-08-22). An earlier revision assumed E03 here, the
    # classic manual's water-flow code — wrong for the same reason the bit was.
    assert LAYOUT_NANO_FI_3KW.fault_table.codes[8] == "E25"

    assert LAYOUT_NANO_FI_3KW.fault_table is NANO_FI_FAULT_TABLE
    assert LAYOUT_NANO_5KW.fault_table is NANO_5KW_FAULT_TABLE
    for layout in (LAYOUT_STANDARD, LAYOUT_V34_WFZEIYN, LAYOUT_PC_INV_120):
        assert layout.fault_table is STANDARD_FAULT_TABLE

    # Every coded bit must be named. The reverse is allowed and used: a bit
    # we can name but do not want to raise a Repair card for is named-only.
    # A code with no name would be the dangerous direction — that is what
    # lets a decoder read a position out of one table and a service code out
    # of the other.
    for table in (STANDARD_FAULT_TABLE, NANO_5KW_FAULT_TABLE, NANO_FI_FAULT_TABLE):
        assert set(table.codes) <= set(table.names)

    # The FI table is deliberately sparse — the classic family's assignments
    # were never verified here and must not be carried over just because the
    # DP number matches. Bit 19 is the named-but-uncoded case: an
    # ambient-range protection, confirmed by panel and app, that is
    # weather-driven and self-clearing, so it gets a name and no Repair card.
    assert set(NANO_FI_FAULT_TABLE.names) == {8, 19}
    assert set(NANO_FI_FAULT_TABLE.codes) == {8}
    assert NANO_FI_FAULT_TABLE.names[19] == "ambient_range"


def test_fi_120_v35_reuses_fi_150_without_changing_v2() -> None:
    """Issue #22 is a firmware sibling, not a replacement for the V2 map."""
    from pysilverline.devices import MODEL_SILVERLINE_FI_120_V35, get_layout

    assert get_layout(MODEL_SILVERLINE_FI_120_V35) is LAYOUT_SILVERLINE_FI_150
    assert LAYOUT_BY_NAME["fi_120_v35"] is LAYOUT_SILVERLINE_FI_150
    assert layout_for_model("pc_inv_120v2") is LAYOUT_PC_INV_120
