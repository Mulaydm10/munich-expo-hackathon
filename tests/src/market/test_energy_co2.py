"""energy_cost() / co2(): interval length from actual timestamp gaps, never /4; NaN prices
are rejected rather than silently priced at zero; the DST-shifted day must not crash."""

import numpy as np
import pandas as pd
import pytest

from src.market import api


def test_energy_cost_uses_actual_interval_length_not_hardcoded_quarter_hour():
    # two hourly (not 15-min) rows of load: at 100 kW for 1h each, at 100 EUR/MWh
    t = pd.to_datetime(["2026-06-01T00:00:00Z", "2026-06-01T01:00:00Z", "2026-06-01T02:00:00Z"], utc=True)
    load = pd.DataFrame({"t": t, "load_kw": [100.0, 100.0, 100.0]})
    prices = pd.DataFrame({"t": t, "price_eur_mwh": [100.0, 100.0, 100.0]})
    cost = api.energy_cost(load, prices)
    # 100 kW * 1h = 0.1 MWh per row * 100 EUR/MWh = 10 EUR/row * 3 rows = 30 EUR
    assert cost == pytest.approx(30.0)


def test_energy_cost_sums_across_sites():
    t = pd.to_datetime(["2026-06-01T00:00:00Z", "2026-06-01T00:15:00Z"], utc=True)
    load = pd.DataFrame({
        "t": list(t) * 2,
        "site_id": ["A", "A", "B", "B"],
        "load_kw": [100.0, 100.0, 200.0, 200.0],
    })
    prices = pd.DataFrame({"t": t, "price_eur_mwh": [100.0, 100.0]})
    cost = api.energy_cost(load, prices)
    # (100+200) kW * 0.25h = 0.075 MWh * 100 EUR/MWh = 7.5 EUR per interval * 2 = 15 EUR
    assert cost == pytest.approx(15.0)


def test_energy_cost_rejects_missing_price_rather_than_pricing_at_zero():
    t = pd.to_datetime(["2026-06-01T00:00:00Z", "2026-06-01T00:15:00Z"], utc=True)
    load = pd.DataFrame({"t": t, "load_kw": [100.0, 100.0]})
    prices = pd.DataFrame({"t": t[:1], "price_eur_mwh": [100.0]})  # second timestamp missing
    with pytest.raises(api.MarketError):
        api.energy_cost(load, prices)


def test_co2_basic_arithmetic():
    t = pd.to_datetime(["2026-06-01T00:00:00Z", "2026-06-01T00:15:00Z"], utc=True)
    load = pd.DataFrame({"t": t, "load_kw": [400.0, 400.0]})
    carbon = pd.DataFrame({"t": t, "intensity_g_kwh": [300.0, 300.0]})
    kg = api.co2(load, carbon)
    # 400 kW * 0.25h = 100 kWh * 300 g/kWh = 30000 g = 30 kg, per row * 2 rows = 60 kg
    assert kg == pytest.approx(60.0)


def test_dst_transition_day_does_not_crash_and_is_arithmetically_sane():
    """CONVENTIONS.md: the DST-shifted day (25h in Europe/Berlin, 2026-10-25) is a real test
    case. Timestamps are UTC throughout, so this must be a total non-event for this lane --
    prove it by spanning the transition with a plain uniform 15-min UTC series."""
    t = pd.date_range("2026-10-24T22:00:00Z", "2026-10-25T04:00:00Z", freq="15min")
    n = len(t)
    load = pd.DataFrame({"t": t, "load_kw": [50.0] * n})
    prices = pd.DataFrame({"t": t, "price_eur_mwh": [40.0] * n})
    cost = api.energy_cost(load, prices)
    hours = (t[-1] - t[0]).total_seconds() / 3600.0 + 0.25
    expected = 50.0 / 1000.0 * hours * 40.0
    assert cost == pytest.approx(expected, rel=1e-6)
