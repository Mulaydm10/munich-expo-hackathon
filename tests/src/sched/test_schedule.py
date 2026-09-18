"""Tests for src.sched — the optimiser.

Fixtures are built directly in this file (see conftest.py's helpers), in the shapes
`contracts/src/fleet.md` (sessions) and `contracts/src/grid.md` (envelope) document.
`src/fleet` and `src/grid` are unmerged lanes and are never imported here.

Every test that exercises a hard constraint also asserts, where noted, that the
scenario genuinely binds -- i.e. that an unconstrained/naive schedule would have
violated it -- so a pass here is not vacuous.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from src.sched import api
from .conftest import grid, mk_envelope, mk_prices, mk_sessions, site_totals


# ---------------------------------------------------------------------------
# 1. Deadline feasibility, including a genuinely tight dwell
# ---------------------------------------------------------------------------


def test_deadline_feasibility_including_tight_dwell():
    times = grid("2026-01-10T00:00", 8)  # 2 hours

    sessions = mk_sessions([
        # A: tight -- exactly max_power_kw * dwell_hours == energy_kwh.
        dict(session_id="A", site_id="S1", t_arrive=times[0], t_depart=times[4],
             energy_kwh=10.0, max_power_kw=10.0),
        # B: loose, plenty of slack.
        dict(session_id="B", site_id="S1", t_arrive=times[0], t_depart=times[8],
             energy_kwh=5.0, max_power_kw=5.0),
    ])
    envelope = mk_envelope("S1", times[:-1], 20.0)  # generous -- not the thing under test here
    prices = mk_prices(times[:-1], 50.0)

    # guard: A really is tight, not slack.
    dwell_h = (sessions.loc[0, "t_depart"] - sessions.loc[0, "t_arrive"]).total_seconds() / 3600
    available = 10.0 * dwell_h
    assert abs(available - 10.0) < 1e-9, "fixture is not actually tight -- test proves nothing"

    sched = api.schedule(sessions, envelope, prices)

    # window / max_power invariants directly on schedule()'s own output.
    merged = sched.merge(sessions, on="session_id", suffixes=("", "_s"))
    assert (merged["t"] >= merged["t_arrive"]).all()
    assert (merged["t"] < merged["t_depart"]).all()
    assert (merged["power_kw"] <= merged["max_power_kw"] + 1e-6).all()
    assert (merged["power_kw"] >= -1e-9).all()

    result = api.evaluate(sched, sessions, envelope, prices, ())
    assert result["unmet_kwh"] == pytest.approx(0.0, abs=1e-6)
    assert result["deadline_misses"] == 0


def test_deadline_infeasible_raises_naming_deadline():
    times = grid("2026-01-10T00:00", 8)
    sessions = mk_sessions([
        # dwell of 1 interval (15 min) at 4 kW = 1 kWh available; 5 kWh is due.
        dict(session_id="X", site_id="S1", t_arrive=times[0], t_depart=times[1],
             energy_kwh=5.0, max_power_kw=4.0),
    ])
    envelope = mk_envelope("S1", times[:-1], 100.0)  # generous -- deadline is the binding one
    prices = mk_prices(times[:-1], 50.0)

    with pytest.raises(api.Infeasible, match="deadline"):
        api.schedule(sessions, envelope, prices)


# ---------------------------------------------------------------------------
# 2. Envelope respected, tested at the boundary (exact capacity, not comfortable slack)
# ---------------------------------------------------------------------------


def test_envelope_respected_at_the_boundary_hot_evening():
    times = grid("2026-01-10T00:00", 4)  # 1 hour, 4 x 15-min intervals
    # Envelope tightens for the last two intervals (hot evening).
    envelope = mk_envelope("S1", times[:-1], [20.0, 20.0, 8.0, 8.0])
    prices = mk_prices(times[:-1], 50.0)

    # Two sessions that ONLY exist during the tight window, whose combined energy need
    # exactly equals the tight window's total capacity (8 kW * 0.25h * 2 intervals = 4
    # kWh). This forces every feasible schedule to sit exactly at the 8 kW boundary in
    # BOTH tight intervals (any slack in one forces a violation in the other).
    sessions = mk_sessions([
        dict(session_id="C", site_id="S1", t_arrive=times[2], t_depart=times[4],
             energy_kwh=2.0, max_power_kw=10.0),
        dict(session_id="D", site_id="S1", t_arrive=times[2], t_depart=times[4],
             energy_kwh=2.0, max_power_kw=10.0),
    ])

    # guard: an uncontrolled (ASAP) baseline really would blow the tight envelope --
    # proves the scenario is genuinely binding, not vacuously satisfied.
    base = api.baseline(sessions, policy="asap")
    base_totals = site_totals(base)
    tight_rows = base_totals[base_totals["t"].isin(times[2:4])]
    assert (tight_rows["total_kw"] > 8.0 + 1e-6).any(), (
        "fixture doesn't actually stress the tight envelope -- ASAP baseline must "
        "violate it for this to be a real test"
    )

    sched = api.schedule(sessions, envelope, prices)
    totals = site_totals(sched)
    merged = totals.merge(envelope, on=["site_id", "t"])
    assert (merged["total_kw"] <= merged["max_kw"] + 1e-6).all()

    boundary = merged[merged["t"].isin(times[2:4])]
    assert (boundary["total_kw"] > 8.0 - 1e-6).all(), "should sit exactly at the boundary"
    assert (boundary["total_kw"] <= 8.0 + 1e-6).all()

    result = api.evaluate(sched, sessions, envelope, prices, ())
    assert result["envelope_violation_kwh"] == pytest.approx(0.0, abs=1e-6)
    assert result["unmet_kwh"] == pytest.approx(0.0, abs=1e-6)
    assert result["deadline_misses"] == 0


def test_envelope_infeasible_raises_naming_envelope():
    times = grid("2026-01-10T00:00", 2)  # 30 minutes
    envelope = mk_envelope("S1", times[:-1], 5.0)  # 5 kW * 0.5h = 2.5 kWh total capacity
    prices = mk_prices(times[:-1], 50.0)
    # 3 sessions, each individually deadline-feasible alone (2.5 kWh available each,
    # 2.0 kWh due), but combined demand (6.0 kWh) exceeds the shared envelope capacity.
    sessions = mk_sessions([
        dict(session_id=f"S{i}", site_id="S1", t_arrive=times[0], t_depart=times[2],
             energy_kwh=2.0, max_power_kw=5.0)
        for i in range(3)
    ])

    with pytest.raises(api.Infeasible, match="envelope"):
        api.schedule(sessions, envelope, prices)


def test_floor_infeasible_because_sessions_lack_capacity_raises_naming_floor():
    """Envelope itself allows the floor (20 kW >= 15 kW), but the only session present
    can supply at most 5 kW -- so it's specifically the *floor* that can't be held,
    exercising the LP-diagnosis path rather than the direct envelope-vs-floor check."""
    times = grid("2026-01-10T00:00", 4)
    envelope = mk_envelope("S1", times[:-1], 20.0)
    prices = mk_prices(times[:-1], 50.0)
    sessions = mk_sessions([
        dict(session_id="A", site_id="S1", t_arrive=times[0], t_depart=times[4],
             energy_kwh=2.0, max_power_kw=5.0),
    ])
    commitments = [api.Commitment(t_start=times[0], t_end=times[4], reduction_kw=15.0)]

    with pytest.raises(api.Infeasible, match="floor"):
        api.schedule(sessions, envelope, prices, commitments=commitments)


# ---------------------------------------------------------------------------
# 3. Price response: schedule() must materially beat the ASAP baseline
# ---------------------------------------------------------------------------


def test_price_response_beats_asap_baseline():
    times = grid("2026-01-10T00:00", 16)  # 4 hours
    # cheap night valley in the second half
    price_values = [300.0] * 8 + [20.0] * 8
    prices = mk_prices(times[:-1], price_values)
    envelope = mk_envelope("S1", times[:-1], 50.0)  # generous, not the thing under test

    n_sessions = 5
    sessions = mk_sessions([
        dict(session_id=f"V{i}", site_id="S1", t_arrive=times[0], t_depart=times[16],
             energy_kwh=8.0, max_power_kw=8.0)
        for i in range(n_sessions)
    ])

    sched = api.schedule(sessions, envelope, prices)
    optimised = api.evaluate(sched, sessions, envelope, prices, ())

    base = api.baseline(sessions, policy="asap")
    base_cost = api.evaluate(base, sessions, envelope, prices, ())

    saving_pct = 100.0 * (1.0 - optimised["energy_cost_eur"] / base_cost["energy_cost_eur"])
    assert optimised["energy_cost_eur"] < 0.5 * base_cost["energy_cost_eur"], (
        f"optimised cost {optimised['energy_cost_eur']:.2f} EUR should be materially "
        f"below ASAP baseline {base_cost['energy_cost_eur']:.2f} EUR "
        f"(saving = {saving_pct:.1f}%)"
    )
    assert optimised["unmet_kwh"] == pytest.approx(0.0, abs=1e-6)
    assert optimised["deadline_misses"] == 0


def test_peak_term_flattens_load_and_beats_asap_peak():
    times = grid("2026-01-10T00:00", 8)
    prices = mk_prices(times[:-1], [20.0] * 4 + [200.0] * 4)
    envelope = mk_envelope("S1", times[:-1], 100.0)
    sessions = mk_sessions([
        dict(session_id=f"V{i}", site_id="S1", t_arrive=times[0], t_depart=times[8],
             energy_kwh=5.0, max_power_kw=10.0)
        for i in range(4)
    ])

    asap = api.baseline(sessions, policy="asap")
    asap_audit = api.evaluate(asap, sessions, envelope, prices, ())
    assert asap_audit["peak_kw"] == pytest.approx(40.0, abs=1e-6)

    energy_only = api.schedule(sessions, envelope, prices, peak_price_eur_per_kw=0.0)
    energy_only_audit = api.evaluate(energy_only, sessions, envelope, prices, ())
    assert energy_only_audit["peak_kw"] == pytest.approx(40.0, abs=1e-6)

    peak_aware = api.schedule(
        sessions,
        envelope,
        prices,
        peak_price_eur_per_kw=api.DEMAND_CHARGE_EUR_PER_KW_DAY,
    )
    peak_audit = api.evaluate(peak_aware, sessions, envelope, prices, ())
    assert peak_audit["peak_kw"] <= 10.0 + 1e-6
    assert peak_audit["peak_kw"] < asap_audit["peak_kw"]
    assert peak_audit["unmet_kwh"] == pytest.approx(0.0, abs=1e-6)
    assert peak_audit["deadline_misses"] == 0
    assert peak_audit["envelope_violation_kwh"] == pytest.approx(0.0, abs=1e-6)


def test_partial_grid_overlap_uses_bin_average_power_caps():
    times = grid("2026-01-10T08:45", 3)
    envelope = mk_envelope("S1", times[:-1], 100.0)
    prices = mk_prices(times[:-1], 50.0)
    sessions = mk_sessions([
        dict(
            session_id="partial",
            site_id="S1",
            t_arrive=pd.Timestamp("2026-01-10T08:50"),
            t_depart=pd.Timestamp("2026-01-10T09:10"),
            energy_kwh=18.75,
            max_power_kw=75.0,
        )
    ])

    scheduled = api.schedule(sessions, envelope, prices)

    assert scheduled["power_kw"].max() <= 50.0 + 1e-6
    assert scheduled["power_kw"].sum() * 0.25 == pytest.approx(18.75, abs=1e-6)


def test_partial_grid_overlap_deadline_capacity_is_enforced():
    times = grid("2026-01-10T08:45", 3)
    envelope = mk_envelope("S1", times[:-1], 100.0)
    prices = mk_prices(times[:-1], 50.0)
    sessions = mk_sessions([
        dict(
            session_id="partial",
            site_id="S1",
            t_arrive=pd.Timestamp("2026-01-10T08:50"),
            t_depart=pd.Timestamp("2026-01-10T09:10"),
            energy_kwh=26.0,
            max_power_kw=75.0,
        )
    ])

    with pytest.raises(api.Infeasible, match="deadline"):
        api.schedule(sessions, envelope, prices)


def test_peak_price_zero_is_bit_identical():
    times = grid("2026-01-10T00:00", 8)
    prices = mk_prices(times[:-1], [20.0] * 4 + [200.0] * 4)
    envelope = mk_envelope("S1", times[:-1], 100.0)
    sessions = mk_sessions([
        dict(session_id=f"V{i}", site_id="S1", t_arrive=times[0], t_depart=times[8],
             energy_kwh=5.0, max_power_kw=10.0)
        for i in range(4)
    ])

    default = api.schedule(sessions, envelope, prices)
    explicit_zero = api.schedule(sessions, envelope, prices, peak_price_eur_per_kw=0.0)

    pd.testing.assert_frame_equal(default, explicit_zero)


def test_peak_price_rejects_negative_and_nan():
    times = grid("2026-01-10T00:00", 8)
    prices = mk_prices(times[:-1], 50.0)
    envelope = mk_envelope("S1", times[:-1], 100.0)
    sessions = mk_sessions([
        dict(session_id="V", site_id="S1", t_arrive=times[0], t_depart=times[8],
             energy_kwh=1.0, max_power_kw=10.0),
    ])

    with pytest.raises(ValueError, match="src/sched.*peak_price_eur_per_kw"):
        api.schedule(sessions, envelope, prices, peak_price_eur_per_kw=-1.0)
    with pytest.raises(ValueError, match="src/sched.*peak_price_eur_per_kw"):
        api.schedule(sessions, envelope, prices, peak_price_eur_per_kw=float("nan"))


def test_dispatch_preserves_peak_price():
    times = grid("2026-01-10T00:00", 8)
    prices = mk_prices(times[:-1], [20.0] * 4 + [200.0] * 4)
    envelope = mk_envelope("S1", times[:-1], 100.0)
    sessions = mk_sessions([
        dict(session_id=f"V{i}", site_id="S1", t_arrive=times[0], t_depart=times[8],
             energy_kwh=5.0, max_power_kw=10.0)
        for i in range(4)
    ])
    peak_price = api.DEMAND_CHARGE_EUR_PER_KW_DAY
    sched = api.schedule(sessions, envelope, prices, peak_price_eur_per_kw=peak_price)

    event = api.ReductionEvent(call_t=times[2], notice_min=0.0, duration_min=30.0,
                               reduction_kw=5.0)
    amended = api.dispatch(sched, event)

    assert amended.attrs["peak_price_eur_per_kw"] == pytest.approx(peak_price)
    audit = api.evaluate(amended, sessions, envelope, prices, ())
    assert audit["deadline_misses"] == 0


# ---------------------------------------------------------------------------
# 4. The floor is real: schedule() holds it; a naive (commitment-blind) schedule
#    on the same input violates it. The contrast is the point.
# ---------------------------------------------------------------------------


def test_floor_holds_and_naive_price_only_schedule_violates_it():
    times = grid("2026-01-10T00:00", 8)  # 2 hours
    # hour 1 cheap, hour 2 (the committed window) expensive -- nothing in the vehicles'
    # own economics wants load there.
    price_values = [20.0] * 4 + [500.0] * 4
    prices = mk_prices(times[:-1], price_values)
    envelope = mk_envelope("S1", times[:-1], 50.0)

    sessions = mk_sessions([
        dict(session_id="A", site_id="S1", t_arrive=times[0], t_depart=times[8],
             energy_kwh=7.5, max_power_kw=10.0),
        dict(session_id="B", site_id="S1", t_arrive=times[0], t_depart=times[8],
             energy_kwh=7.5, max_power_kw=10.0),
    ])
    commitment = api.Commitment(t_start=times[4], t_end=times[8], reduction_kw=6.0)

    # naive: schedule ignoring the commitment entirely (pure price optimisation).
    naive = api.schedule(sessions, envelope, prices, commitments=())
    naive_hour2 = site_totals(naive)
    naive_hour2 = naive_hour2[naive_hour2["t"].isin(times[4:8])]
    assert (naive_hour2["total_kw"] < 6.0 - 1e-6).all() or naive_hour2.empty, (
        "fixture doesn't actually stress the floor -- the naive schedule must leave "
        "hour 2 below the floor for the contrast to mean anything"
    )
    naive_audit = api.evaluate(naive, sessions, envelope, prices, (commitment,))
    assert naive_audit["floor_shortfall_kw_min"] > 0.0, (
        f"naive price-only schedule should violate the floor; measured shortfall = "
        f"{naive_audit['floor_shortfall_kw_min']:.1f} kW*min"
    )
    # exact expected shortfall: 6 kW short for all 4 tight intervals (60 minutes).
    assert naive_audit["floor_shortfall_kw_min"] == pytest.approx(6.0 * 60.0, rel=1e-6)

    # honoured: schedule() with the commitment holds the floor.
    honoured = api.schedule(sessions, envelope, prices, commitments=(commitment,))
    honoured_audit = api.evaluate(honoured, sessions, envelope, prices, (commitment,))
    assert honoured_audit["floor_shortfall_kw_min"] == pytest.approx(0.0, abs=1e-6)
    assert honoured_audit["unmet_kwh"] == pytest.approx(0.0, abs=1e-6)
    assert honoured_audit["deadline_misses"] == 0


# ---------------------------------------------------------------------------
# 5. lp vs greedy: agree on feasibility (both ways), lp at least as cheap
# ---------------------------------------------------------------------------


def test_lp_and_greedy_agree_on_feasible_instance_and_lp_is_at_least_as_cheap():
    times = grid("2026-01-10T00:00", 8)
    price_values = [20.0] * 4 + [200.0] * 4
    prices = mk_prices(times[:-1], price_values)
    envelope = mk_envelope("S1", times[:-1], 50.0)
    sessions = mk_sessions([
        dict(session_id="A", site_id="S1", t_arrive=times[0], t_depart=times[8],
             energy_kwh=4.0, max_power_kw=10.0),
        dict(session_id="B", site_id="S1", t_arrive=times[0], t_depart=times[8],
             energy_kwh=4.0, max_power_kw=10.0),
    ])
    commitment = api.Commitment(t_start=times[4], t_end=times[8], reduction_kw=2.0)

    lp_sched = api.schedule(sessions, envelope, prices, commitments=(commitment,), solver="lp")
    greedy_sched = api.schedule(sessions, envelope, prices, commitments=(commitment,), solver="greedy")

    lp_eval = api.evaluate(lp_sched, sessions, envelope, prices, (commitment,))
    greedy_eval = api.evaluate(greedy_sched, sessions, envelope, prices, (commitment,))

    for name, ev in [("lp", lp_eval), ("greedy", greedy_eval)]:
        assert ev["unmet_kwh"] == pytest.approx(0.0, abs=1e-6), name
        assert ev["deadline_misses"] == 0, name
        assert ev["envelope_violation_kwh"] == pytest.approx(0.0, abs=1e-6), name
        assert ev["floor_shortfall_kw_min"] == pytest.approx(0.0, abs=1e-6), name

    assert lp_eval["energy_cost_eur"] <= greedy_eval["energy_cost_eur"] + 1e-6, (
        f"lp cost {lp_eval['energy_cost_eur']:.4f} should be <= greedy cost "
        f"{greedy_eval['energy_cost_eur']:.4f}"
    )


def test_lp_and_greedy_agree_on_infeasible_instance():
    times = grid("2026-01-10T00:00", 4)
    envelope = mk_envelope("S1", times[:-1], 20.0)
    prices = mk_prices(times[:-1], 50.0)
    sessions = mk_sessions([
        dict(session_id="A", site_id="S1", t_arrive=times[0], t_depart=times[4],
             energy_kwh=2.0, max_power_kw=5.0),
    ])
    commitments = [api.Commitment(t_start=times[0], t_end=times[4], reduction_kw=15.0)]

    with pytest.raises(api.Infeasible):
        api.schedule(sessions, envelope, prices, commitments=commitments, solver="lp")
    with pytest.raises(api.Infeasible):
        api.schedule(sessions, envelope, prices, commitments=commitments, solver="greedy")


# ---------------------------------------------------------------------------
# 6. NaN policy -- deliberate, tested, never a neutral default
# ---------------------------------------------------------------------------


def test_nan_in_envelope_max_kw_is_rejected():
    times = grid("2026-01-10T00:00", 4)
    envelope = mk_envelope("S1", times[:-1], [10.0, np.nan, 10.0, 10.0])
    prices = mk_prices(times[:-1], 50.0)
    sessions = mk_sessions([
        dict(session_id="A", site_id="S1", t_arrive=times[0], t_depart=times[4],
             energy_kwh=1.0, max_power_kw=5.0),
    ])
    with pytest.raises(ValueError, match="NaN"):
        api.schedule(sessions, envelope, prices)


def test_nan_in_sessions_energy_kwh_is_rejected():
    times = grid("2026-01-10T00:00", 4)
    envelope = mk_envelope("S1", times[:-1], 10.0)
    prices = mk_prices(times[:-1], 50.0)
    sessions = mk_sessions([
        dict(session_id="A", site_id="S1", t_arrive=times[0], t_depart=times[4],
             energy_kwh=np.nan, max_power_kw=5.0),
    ])
    with pytest.raises(ValueError, match="NaN"):
        api.schedule(sessions, envelope, prices)


def test_nan_in_prices_is_rejected():
    times = grid("2026-01-10T00:00", 4)
    envelope = mk_envelope("S1", times[:-1], 10.0)
    prices = mk_prices(times[:-1], [50.0, np.nan, 50.0, 50.0])
    sessions = mk_sessions([
        dict(session_id="A", site_id="S1", t_arrive=times[0], t_depart=times[4],
             energy_kwh=1.0, max_power_kw=5.0),
    ])
    with pytest.raises(ValueError, match="NaN"):
        api.schedule(sessions, envelope, prices)


def test_nan_commitment_reduction_kw_is_rejected():
    times = grid("2026-01-10T00:00", 4)
    envelope = mk_envelope("S1", times[:-1], 10.0)
    prices = mk_prices(times[:-1], 50.0)
    sessions = mk_sessions([
        dict(session_id="A", site_id="S1", t_arrive=times[0], t_depart=times[4],
             energy_kwh=1.0, max_power_kw=5.0),
    ])
    bad_commitment = api.Commitment(t_start=times[0], t_end=times[4], reduction_kw=float("nan"))
    with pytest.raises(ValueError, match="NaN"):
        api.schedule(sessions, envelope, prices, commitments=(bad_commitment,))


# ---------------------------------------------------------------------------
# 7. dispatch(): honours a call and still lands every deadline, or refuses it
# ---------------------------------------------------------------------------


def test_dispatch_reduces_load_and_still_lands_every_deadline():
    times = grid("2026-01-10T00:00", 8)  # 2 hours
    # cheapest at intervals 3,4 (t=00:45, t=01:00) -- LP will concentrate charging there.
    price_values = [50.0, 50.0, 50.0, 10.0, 10.0, 50.0, 50.0, 50.0]
    prices = mk_prices(times[:-1], price_values)
    envelope = mk_envelope("S1", times[:-1], 20.0)  # generous slack elsewhere
    sessions = mk_sessions([
        dict(session_id="A", site_id="S1", t_arrive=times[0], t_depart=times[8],
             energy_kwh=4.0, max_power_kw=8.0),
    ])

    sched = api.schedule(sessions, envelope, prices)
    pre_totals = site_totals(sched).set_index("t")["total_kw"]
    assert pre_totals.loc[times[3]] == pytest.approx(8.0, abs=1e-6)
    assert pre_totals.loc[times[4]] == pytest.approx(8.0, abs=1e-6)

    # call arrives exactly when the session was going to charge; ask to cut it fully.
    event = api.ReductionEvent(call_t=times[3], notice_min=0.0, duration_min=30.0,
                                reduction_kw=8.0)
    amended = api.dispatch(sched, event)

    post_totals = site_totals(amended).set_index("t")["total_kw"]
    assert post_totals.loc[times[3]] <= 1e-6
    assert post_totals.loc[times[4]] <= 1e-6

    audit = api.evaluate(amended, sessions, envelope, prices, ())
    assert audit["unmet_kwh"] == pytest.approx(0.0, abs=1e-6)
    assert audit["deadline_misses"] == 0
    assert audit["envelope_violation_kwh"] == pytest.approx(0.0, abs=1e-6)

    assert amended.attrs["reduction_kw_achieved"] == pytest.approx(8.0, abs=1e-3)
    assert amended.attrs["reduction_shortfall_kw"] == pytest.approx(0.0, abs=1e-3)


def test_dispatch_refuses_rather_than_miss_a_deadline_and_reports_shortfall():
    times = grid("2026-01-10T00:00", 4)  # 1 hour, zero slack anywhere
    envelope = mk_envelope("S1", times[:-1], 10.0)  # exactly == session's max_power_kw
    prices = mk_prices(times[:-1], 50.0)
    sessions = mk_sessions([
        # tight: 10 kW * 0.25h * 4 intervals == 10 kWh due, no slack at all.
        dict(session_id="A", site_id="S1", t_arrive=times[0], t_depart=times[4],
             energy_kwh=10.0, max_power_kw=10.0),
    ])

    sched = api.schedule(sessions, envelope, prices)
    pre_audit = api.evaluate(sched, sessions, envelope, prices, ())
    assert pre_audit["unmet_kwh"] == pytest.approx(0.0, abs=1e-6)

    # a call demanding the entire envelope be cut for one interval cannot be recovered
    # anywhere -- every other interval is already at max power for its own need.
    event = api.ReductionEvent(call_t=times[0], notice_min=0.0, duration_min=15.0,
                                reduction_kw=10.0)
    amended = api.dispatch(sched, event)

    post_audit = api.evaluate(amended, sessions, envelope, prices, ())
    assert post_audit["deadline_misses"] == 0, "dispatch must never accept a call that misses a deadline"
    assert post_audit["unmet_kwh"] == pytest.approx(0.0, abs=1e-6)

    assert amended.attrs["reduction_kw_achieved"] == pytest.approx(0.0, abs=1e-3)
    assert amended.attrs["reduction_shortfall_kw"] == pytest.approx(10.0, abs=1e-3), (
        f"measured shortfall = {amended.attrs['reduction_shortfall_kw']:.3f} kW out of "
        f"a {amended.attrs['reduction_kw_requested']:.3f} kW request -- fully refused, "
        f"not silently under-delivered"
    )


# ---------------------------------------------------------------------------
# 8. DST transition -- must not crash (CONVENTIONS.md: a real test case)
# ---------------------------------------------------------------------------


def test_dst_transition_day_does_not_crash():
    # 2026-10-25 is Europe/Berlin's fall-back day; UTC has no discontinuity, but this
    # proves timestamps around it flow through unit conversion without crashing.
    times = grid("2026-10-24T22:00", 8)  # spans into 2026-10-25 UTC
    envelope = mk_envelope("S1", times[:-1], 20.0)
    prices = mk_prices(times[:-1], 50.0)
    sessions = mk_sessions([
        dict(session_id="A", site_id="S1", t_arrive=times[0], t_depart=times[8],
             energy_kwh=4.0, max_power_kw=8.0),
    ])
    sched = api.schedule(sessions, envelope, prices)
    result = api.evaluate(sched, sessions, envelope, prices, ())
    assert result["unmet_kwh"] == pytest.approx(0.0, abs=1e-6)
    assert result["deadline_misses"] == 0


# ---------------------------------------------------------------------------
# 9. baseline(): records under-delivery, never hides it (the anti-pattern this
#    project keeps repeating elsewhere)
# ---------------------------------------------------------------------------


def test_baseline_asap_records_unmet_energy_not_silently():
    times = grid("2026-01-10T00:00", 8)
    sessions = mk_sessions([
        # fits fully: 8 kW * 0.25h * 4 intervals = 8 kWh available, 8 kWh due.
        dict(session_id="fits", site_id="S1", t_arrive=times[0], t_depart=times[4],
             energy_kwh=8.0, max_power_kw=8.0),
        # too short: only 2 intervals available (0.5h) at 4 kW = 2 kWh, but 5 kWh due.
        dict(session_id="short", site_id="S1", t_arrive=times[0], t_depart=times[2],
             energy_kwh=5.0, max_power_kw=4.0),
    ])
    base = api.baseline(sessions, policy="asap")

    assert "fits" not in base.attrs["unmet_kwh_by_session"]
    assert base.attrs["unmet_kwh_by_session"]["short"] == pytest.approx(3.0, abs=1e-6)
    assert base.attrs["unmet_kwh_total"] == pytest.approx(3.0, abs=1e-6)
    assert base.attrs["unmet_session_fraction"] == pytest.approx(0.5, abs=1e-9)


# ---------------------------------------------------------------------------
# 10. Issue #27 -- eight defects the design review found invisible to the tests above.
# ---------------------------------------------------------------------------


def test_evaluate_excludes_energy_delivered_outside_sessions_own_window():
    """#27 finding 1: `evaluate()` used to join `delivered` on `session_id` alone, with
    no check on `site_id`/`t_arrive`/`deadline_t` -- so a hand-built schedule crediting
    a session's energy at the wrong time (or site) read as a met deadline. Before this
    fix: {'unmet_kwh': 0.0, 'deadline_misses': 0} for 20 kW delivered 75 minutes after
    the session's own deadline. After: the energy is excluded, so it surfaces as unmet
    energy and a deadline miss instead of being silently banked.
    """
    times = grid("2026-01-10T00:00", 8)  # 2 hours
    sessions = mk_sessions([
        dict(session_id="A", site_id="S1", t_arrive=times[0], t_depart=times[2],
             energy_kwh=5.0, max_power_kw=10.0),  # deadline_t defaults to t_depart = times[2]
    ])
    envelope = mk_envelope("S1", times[:-1], 50.0)
    prices = mk_prices(times[:-1], 50.0)

    # hand-built, deliberately-broken schedule: all of session A's power lands at
    # times[7], well after its deadline at times[2] -- physically it was never used to
    # satisfy A's deadline, so it must not be credited as if it had been.
    broken = pd.DataFrame([
        {"t": times[7], "site_id": "S1", "session_id": "A", "power_kw": 20.0},
    ])

    result = api.evaluate(broken, sessions, envelope, prices, ())
    assert result["unmet_kwh"] == pytest.approx(5.0, abs=1e-6), (
        "energy delivered after the deadline must not count toward meeting it"
    )
    assert result["deadline_misses"] == 1
    # the power was still physically drawn, so it still counts for envelope/peak/cost:
    assert result["peak_kw"] == pytest.approx(20.0, abs=1e-6)


def test_evaluate_excludes_energy_credited_to_the_wrong_site():
    """Same finding, the site half: a schedule crediting session A's energy at site S2
    (A never charges there) must not count toward A's deadline either -- the old
    `groupby("session_id")` join had no site check at all."""
    times = grid("2026-01-10T00:00", 4)
    sessions = mk_sessions([
        dict(session_id="A", site_id="S1", t_arrive=times[0], t_depart=times[4],
             energy_kwh=5.0, max_power_kw=10.0),
    ])
    envelope = pd.concat([
        mk_envelope("S1", times[:-1], 50.0),
        mk_envelope("S2", times[:-1], 50.0),
    ], ignore_index=True)
    prices = mk_prices(times[:-1], 50.0)

    broken = pd.DataFrame([
        {"t": times[0], "site_id": "S2", "session_id": "A", "power_kw": 10.0},
    ])
    result = api.evaluate(broken, sessions, envelope, prices, ())
    assert result["unmet_kwh"] == pytest.approx(5.0, abs=1e-6)
    assert result["deadline_misses"] == 1


def test_evaluate_empty_schedule_reports_full_floor_shortfall_not_zero():
    """#27 finding 2: the empty-schedule early return hard-coded
    `floor_shortfall_kw_min: 0.0` -- perfect floor compliance for a site delivering
    nothing. An empty schedule under a live 40 kW commitment across 4 intervals is a
    full shortfall: 40 kW short * 15 min * 4 intervals = 2400 kW*min."""
    times = grid("2026-01-10T00:00", 4)
    sessions = mk_sessions([
        dict(session_id="A", site_id="S1", t_arrive=times[0], t_depart=times[4],
             energy_kwh=5.0, max_power_kw=10.0),
    ])
    envelope = mk_envelope("S1", times[:-1], 50.0)
    prices = mk_prices(times[:-1], 50.0)
    commitment = api.Commitment(t_start=times[0], t_end=times[4], reduction_kw=40.0)
    empty = pd.DataFrame(columns=["t", "site_id", "session_id", "power_kw"])

    result = api.evaluate(empty, sessions, envelope, prices, (commitment,))
    assert result["unmet_kwh"] == pytest.approx(5.0, abs=1e-6)
    assert result["floor_shortfall_kw_min"] == pytest.approx(40.0 * 15.0 * 4, rel=1e-6), (
        "an empty schedule delivering 0 kW against a live 40 kW floor is a full "
        "shortfall, not perfect compliance"
    )


def test_evaluate_missing_site_interval_reports_floor_shortfall_not_silence():
    """Same finding, the non-empty-path half: a schedule that has rows for some but not
    all committed (site, t) pairs used to read the missing ones as "no obligation"
    because the floor loop only ever visited rows present in the schedule."""
    times = grid("2026-01-10T00:00", 4)
    sessions = mk_sessions([
        dict(session_id="A", site_id="S1", t_arrive=times[0], t_depart=times[4],
             energy_kwh=5.0, max_power_kw=10.0),
    ])
    envelope = mk_envelope("S1", times[:-1], 50.0)
    prices = mk_prices(times[:-1], 50.0)
    commitment = api.Commitment(t_start=times[0], t_end=times[4], reduction_kw=40.0)
    # only ONE of the 4 committed intervals has a schedule row at all.
    partial = pd.DataFrame([
        {"t": times[0], "site_id": "S1", "session_id": "A", "power_kw": 40.0},
    ])
    result = api.evaluate(partial, sessions, envelope, prices, (commitment,))
    # times[1], times[2], times[3] are missing rows -> zero load against a live floor.
    assert result["floor_shortfall_kw_min"] == pytest.approx(40.0 * 15.0 * 3, rel=1e-6)


def test_zero_variable_lp_still_holds_the_floor():
    """#27 finding 6: a session whose window does not intersect the envelope grid at
    all produces zero LP variables, and `_solve_lp`'s `n == 0` early return used to fire
    before any floor row was ever built -- reporting success while holding none of a
    live floor. The on-grid, zero-power case (session present but drained) already
    raised correctly; this is specifically the empty-variable path."""
    times = grid("2026-01-10T00:00", 4)
    envelope = mk_envelope("S1", times[:-1], 50.0)
    prices = mk_prices(times[:-1], 50.0)
    # session's own window starts only once the envelope grid has already ended.
    one_interval = times[1] - times[0]
    sessions = mk_sessions([
        dict(session_id="A", site_id="S1", t_arrive=times[4], t_depart=times[4] + one_interval,
             energy_kwh=0.0, max_power_kw=10.0),
    ])
    commitment = api.Commitment(t_start=times[0], t_end=times[4], reduction_kw=40.0)

    with pytest.raises(api.Infeasible, match="floor"):
        api.schedule(sessions, envelope, prices, commitments=(commitment,), solver="lp")
    with pytest.raises(api.Infeasible, match="floor"):
        api.schedule(sessions, envelope, prices, commitments=(commitment,), solver="greedy")


def test_unknown_solver_string_is_rejected_not_silently_run_as_greedy():
    """#27 finding 7: `solve_fn = _solve_site_lp if solver == "lp" else _solve_site_greedy`
    made every non-"lp" string mean greedy -- a capitalisation typo like "LP" silently
    changed the optimiser instead of raising."""
    times = grid("2026-01-10T00:00", 4)
    envelope = mk_envelope("S1", times[:-1], 50.0)
    prices = mk_prices(times[:-1], 50.0)
    sessions = mk_sessions([
        dict(session_id="A", site_id="S1", t_arrive=times[0], t_depart=times[4],
             energy_kwh=2.0, max_power_kw=10.0),
    ])
    with pytest.raises(ValueError, match="solver"):
        api.schedule(sessions, envelope, prices, solver="LP")
    with pytest.raises(ValueError, match="solver"):
        api.schedule(sessions, envelope, prices, solver="bogus")


def test_dispatch_rejects_negative_duration_event():
    """#27 finding 8: `compliance_end < compliance_start` capped nothing, so a negative
    `duration_min` solved the untouched problem and reported the full request as
    delivered (`ReductionEvent(duration_min=-60.0, reduction_kw=999.0)` ->
    `achieved 999.0, shortfall 0.0`). Reject rather than silently accept a window whose
    end does not follow its start."""
    times = grid("2026-01-10T00:00", 8)
    envelope = mk_envelope("S1", times[:-1], 50.0)
    prices = mk_prices(times[:-1], 50.0)
    sessions = mk_sessions([
        dict(session_id="A", site_id="S1", t_arrive=times[0], t_depart=times[8],
             energy_kwh=4.0, max_power_kw=22.0),
    ])
    sched = api.schedule(sessions, envelope, prices)

    event = api.ReductionEvent(call_t=times[4], notice_min=0.0, duration_min=-60.0,
                                reduction_kw=999.0)
    with pytest.raises(ValueError, match="duration_min"):
        api.dispatch(sched, event)


def test_dispatch_rejects_negative_notice():
    """Same finding, the `notice_min` half."""
    times = grid("2026-01-10T00:00", 8)
    envelope = mk_envelope("S1", times[:-1], 50.0)
    prices = mk_prices(times[:-1], 50.0)
    sessions = mk_sessions([
        dict(session_id="A", site_id="S1", t_arrive=times[0], t_depart=times[8],
             energy_kwh=4.0, max_power_kw=22.0),
    ])
    sched = api.schedule(sessions, envelope, prices)

    event = api.ReductionEvent(call_t=times[4], notice_min=-15.0, duration_min=30.0,
                                reduction_kw=10.0)
    with pytest.raises(ValueError, match="notice_min"):
        api.dispatch(sched, event)


def test_dispatch_never_changes_power_already_elapsed():
    """#27 finding 5 / issue-3: `dispatch()` used to recover deferred energy by
    charging in intervals that were already in the past relative to `event.call_t`,
    because it re-solved every session's whole arrival-departure window regardless of
    when the call arrived. Expensive-early/cheap-late prices push the unconstrained
    schedule to fully use its 4 cheapest (latest) intervals with zero slack; a call
    partway through that block can only be answered (if at all) by pinning everything
    before `call_t` to what the committed schedule actually drew.
    """
    times = grid("2026-01-10T18:00", 8)  # 18:00 .. 20:00
    price_values = [200.0] * 4 + [10.0] * 4  # expensive 18:00-19:00, cheap 19:00-20:00
    prices = mk_prices(times[:-1], price_values)
    envelope = mk_envelope("S1", times[:-1], 50.0)
    sessions = mk_sessions([
        # tight: 22 kW * 0.25h * 4 intervals == 22 kWh due -- the LP has no reason (and
        # no room) to touch the expensive early hours at all.
        dict(session_id="A", site_id="S1", t_arrive=times[0], t_depart=times[8],
             energy_kwh=22.0, max_power_kw=22.0),
    ])
    sched = api.schedule(sessions, envelope, prices)
    pre_totals = site_totals(sched).set_index("t")["total_kw"]
    # guard: fixture really is tight and really does concentrate in the late block.
    assert (pre_totals.reindex(times[:4], fill_value=0.0) == 0.0).all()
    assert (pre_totals.reindex(times[4:8]) - 22.0).abs().max() < 1e-6

    call_t = times[5]  # 19:15 -- one interval into the tight block
    event = api.ReductionEvent(call_t=call_t, notice_min=0.0, duration_min=30.0,
                                reduction_kw=22.0)
    amended = api.dispatch(sched, event)
    post_totals = site_totals(amended).set_index("t")["total_kw"]

    elapsed = [t for t in times[:-1] if t < call_t]
    for t in elapsed:
        assert post_totals.get(t, 0.0) == pytest.approx(pre_totals.get(t, 0.0), abs=1e-6), (
            f"power at {t}, which had already elapsed when the call arrived, changed "
            f"between the committed and amended schedule"
        )
    # with no slack anywhere else and the past pinned, the honest answer is that
    # nothing can be shed here without a deadline miss -- dispatch must refuse, not
    # manufacture room by rewriting the past.
    audit = api.evaluate(amended, sessions, envelope, prices, ())
    assert audit["deadline_misses"] == 0
    assert audit["unmet_kwh"] == pytest.approx(0.0, abs=1e-6)
    assert amended.attrs["reduction_kw_achieved"] == pytest.approx(0.0, abs=1e-3)
    assert amended.attrs["reduction_shortfall_kw"] == pytest.approx(22.0, abs=1e-3)


def test_dispatch_reports_measured_shed_not_solver_feasibility():
    """#27 finding 4: `reduction_kw_achieved` used to be `alpha * event.reduction_kw` --
    proof only that a solve succeeded under a cap of `max(0, original - request)`. When
    nothing was sold, the cost-optimal plan already sits at 0 kW during the requested
    window, so that cap is trivially satisfiable at alpha=1 despite zero kW actually
    being shed: before this fix, requesting 60 kW here reported `achieved: 60.0,
    shortfall: 0.0` for a call that changed nothing.
    """
    times = grid("2026-01-10T00:00", 16)  # 4 hours
    # expensive everywhere except a late valley -- nothing is sold, so the LP puts
    # every session's charging in the cheap valley and leaves the requested window at
    # 0 kW on its own, with no event involved at all.
    price_values = [100.0] * 12 + [10.0] * 4
    prices = mk_prices(times[:-1], price_values)
    envelope = mk_envelope("S1", times[:-1], 100.0)
    sessions = mk_sessions([
        dict(session_id=f"V{i}", site_id="S1", t_arrive=times[0], t_depart=times[16],
             energy_kwh=8.0, max_power_kw=8.0)
        for i in range(4)
    ])
    sched = api.schedule(sessions, envelope, prices)  # no commitments sold
    pre_totals = site_totals(sched).set_index("t")["total_kw"]
    window = times[4:8]
    assert (pre_totals.reindex(window, fill_value=0.0) == 0.0).all(), (
        "fixture must leave the requested window at 0 kW with nothing sold, or this "
        "test proves nothing"
    )

    event = api.ReductionEvent(call_t=window[0], notice_min=0.0, duration_min=60.0,
                                reduction_kw=60.0)
    amended = api.dispatch(sched, event)
    post_totals = site_totals(amended).set_index("t")["total_kw"]

    actual_shed = float((pre_totals.reindex(window, fill_value=0.0)
                          - post_totals.reindex(window, fill_value=0.0)).min())
    assert actual_shed == pytest.approx(0.0, abs=1e-6)
    assert amended.attrs["reduction_kw_achieved"] == pytest.approx(actual_shed, abs=1e-3), (
        "reduction_kw_achieved must equal what was actually measured, not what the "
        "solver merely proved feasible"
    )
    assert amended.attrs["reduction_kw_achieved"] == pytest.approx(0.0, abs=1e-3)
    assert amended.attrs["reduction_shortfall_kw"] == pytest.approx(60.0, abs=1e-3)


def test_dispatch_releases_floor_in_compliance_window_and_sheds_the_full_request():
    """#27 finding 1 (+ 4 measured together): selling exactly the reduction later
    called for, in its own window, must actually shed that amount. Before this fix,
    `dispatch()` passed the sold commitment through unchanged, so the floor (load >=
    60 kW) stayed enforced *during* the compliance window while the tightened envelope
    capped the site at `original - 60` -- the two constraints fought and the floor won,
    shedding only ~28 kW of the 60 kW sold and called for.
    """
    times = grid("2026-01-10T00:00", 16)  # 4 hours
    # cheap only in the window we sell/curtail; pricier everywhere else, so the
    # unconstrained optimum voluntarily loads the site up there.
    price_values = [100.0] * 4 + [10.0] * 4 + [100.0] * 8
    prices = mk_prices(times[:-1], price_values)
    envelope = mk_envelope("S1", times[:-1], 100.0)
    sessions = mk_sessions([
        dict(session_id=f"V{i}", site_id="S1", t_arrive=times[0], t_depart=times[16],
             energy_kwh=22.0, max_power_kw=22.0)
        for i in range(4)
    ])
    window = times[4:8]
    commitment = api.Commitment(t_start=window[0], t_end=times[8], reduction_kw=60.0)

    sched = api.schedule(sessions, envelope, prices, commitments=(commitment,))
    pre_totals = site_totals(sched).set_index("t")["total_kw"]
    # guard: the fixture genuinely piles up well past the 60 kW floor on its own (four
    # 22 kW sessions with nowhere cheaper to be), so shedding down to 60 is a real cut.
    assert (pre_totals.reindex(window) > 60.0 + 1e-6).all()

    event = api.ReductionEvent(call_t=window[0], notice_min=0.0, duration_min=60.0,
                                reduction_kw=60.0)
    amended = api.dispatch(sched, event)
    post_totals = site_totals(amended).set_index("t")["total_kw"]

    actual_shed = float((pre_totals.reindex(window) - post_totals.reindex(window)).min())
    assert actual_shed == pytest.approx(60.0, abs=1e-2), (
        f"selling 60 kW and calling for 60 kW in-window should shed 60 kW, measured "
        f"{actual_shed:.3f} kW"
    )
    assert amended.attrs["reduction_kw_achieved"] == pytest.approx(60.0, abs=1e-2)
    assert amended.attrs["reduction_shortfall_kw"] == pytest.approx(0.0, abs=1e-2)

    audit = api.evaluate(amended, sessions, envelope, prices, ())
    assert audit["deadline_misses"] == 0
    assert audit["unmet_kwh"] == pytest.approx(0.0, abs=1e-6)
    assert audit["envelope_violation_kwh"] == pytest.approx(0.0, abs=1e-6)
