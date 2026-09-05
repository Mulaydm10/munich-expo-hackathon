"""Fixture builders for tests/src/sched.

`src/fleet` and `src/grid` are unmerged lanes (PRs #19, #21) -- this test suite never
imports them. It builds its own sessions/envelope/prices frames directly, in the shapes
`contracts/src/fleet.md` and `contracts/src/grid.md` document, per the task's lane
discipline instructions.
"""

from __future__ import annotations

import pandas as pd


def grid(start: str, n: int, freq_min: int = 15) -> pd.DatetimeIndex:
    """`n+1` tz-aware UTC timestamps, `freq_min` apart: `times[0:n]` are the `n`
    interval-start points a schedule can actually use (pass `times[:-1]` to
    `mk_envelope`/`mk_prices`), and `times[n]` is the grid's exclusive end boundary --
    handy as a session's `t_depart` when it should run to the end of the grid.
    """
    return pd.date_range(start, periods=n + 1, freq=f"{freq_min}min", tz="UTC")


def mk_sessions(rows: list[dict]) -> pd.DataFrame:
    """Each row needs: session_id, site_id, t_arrive, t_depart, energy_kwh, max_power_kw.
    `deadline_t` defaults to `t_depart` per contracts/src/fleet.md unless overridden."""
    df = pd.DataFrame(rows)
    if "deadline_t" not in df.columns:
        df["deadline_t"] = df["t_depart"]
    df["t_arrive"] = pd.to_datetime(df["t_arrive"], utc=True)
    df["t_depart"] = pd.to_datetime(df["t_depart"], utc=True)
    df["deadline_t"] = pd.to_datetime(df["deadline_t"], utc=True)
    return df


def mk_envelope(site_id: str, times: pd.DatetimeIndex, max_kw) -> pd.DataFrame:
    """`max_kw`: a scalar (constant envelope) or a sequence the same length as `times`."""
    if not hasattr(max_kw, "__len__"):
        max_kw = [max_kw] * len(times)
    return pd.DataFrame({"site_id": site_id, "t": times, "max_kw": list(max_kw)})


def mk_prices(times: pd.DatetimeIndex, price_eur_mwh) -> pd.DataFrame:
    if not hasattr(price_eur_mwh, "__len__"):
        price_eur_mwh = [price_eur_mwh] * len(times)
    return pd.DataFrame({"t": times, "price_eur_mwh": list(price_eur_mwh)})


def site_totals(schedule: pd.DataFrame) -> pd.DataFrame:
    """t, site_id, total_kw -- a small helper tests use to inspect envelope compliance
    directly on a `schedule()`/`dispatch()` output, independent of `evaluate()`."""
    out = schedule.groupby(["site_id", "t"], as_index=False)["power_kw"].sum()
    return out.rename(columns={"power_kw": "total_kw"})
