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
