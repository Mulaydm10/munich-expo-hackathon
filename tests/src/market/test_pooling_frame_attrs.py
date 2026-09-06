"""Regression: the frames this lane returns must survive normal pandas use.

pandas compares two frames' `.attrs` dicts with `==` whenever it finalises an
operation (`concat` does, and rendering a wide frame can too). A DataFrame,
Series or ndarray sitting in `.attrs` makes that comparison raise "truth value
of a DataFrame is ambiguous" -- so merely *printing* or concatenating a pooled
frame would explode in a consumer lane, which is exactly what `src/service` and
`src/ui` do with these.

`src/fleet` hit this and solved it by keeping `.attrs['occupancy']` a plain
dict. The same rule is pinned here for all four frames the pooling path
returns: correlation matrices, joint ranks and regime tables travel as nested
dicts and lists of floats, never as pandas or numpy objects.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.market import api

from .conftest import load_frame, quantile_frame, site_table, utc_index

SITE_IDS = ["berlin", "potsdam", "frankfurt"]
SITES = site_table(SITE_IDS, [52.52, 52.40, 50.11], [13.40, 13.06, 8.68])


def _frames():
    n_days = 6
    t = utc_index(n_days, start="2026-01-05T00:00:00Z")
    rng = np.random.default_rng(71)
    profile = np.tile(300.0 + 120.0 * np.sin(np.arange(96) / 96 * 2 * np.pi), n_days)
    common = rng.normal(0.0, 1.0, len(t))

    loads, mu = {}, {}
    for site_id in SITE_IDS:
        idio = rng.normal(0.0, 1.0, len(t))
        loads[site_id] = profile + 25.0 * (np.sqrt(0.3) * common + np.sqrt(0.7) * idio)
        mu[site_id] = loads[site_id]

    weather = pd.DataFrame({"t": t, "temp_c": rng.normal(6.0, 5.0, len(t))})
    quantiles = quantile_frame(mu, sigma=50.0, t=t)
    firm = api.firm_capacity(quantiles, tau=0.05)
    realised = pd.concat(
        [pd.DataFrame({"t": t, "site_id": s, "realised_kw": np.clip(v, 0.0, None)})
         for s, v in mu.items()],
        ignore_index=True,
    )
    return {
        "firm_capacity": firm,
        "pool": api.pool(firm, method="gaussian_copula", seed=0),
        "correlation_structure": api.correlation_structure(
            load_frame(loads, t), SITES, weather=weather),
        "diversification_curve": api.diversification_curve(
            firm, sizes=[1, 2, 3], seed=0, realised=realised),
    }


FRAMES = _frames()


def _walk(value):
    yield value
    if isinstance(value, dict):
        for v in value.values():
            yield from _walk(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _walk(v)


@pytest.mark.parametrize("name", sorted(FRAMES))
def test_attrs_carry_no_pandas_or_numpy_containers(name):
    for key, value in FRAMES[name].attrs.items():
        for node in _walk(value):
            assert not isinstance(node, (pd.DataFrame, pd.Series, pd.Index, np.ndarray)), (
                f"{name}.attrs[{key!r}] contains a {type(node).__name__}; pandas compares "
                "attrs with == on finalize and this raises"
            )


@pytest.mark.parametrize("name", sorted(FRAMES))
def test_frames_survive_concat_equals_and_repr(name):
    df = FRAMES[name]
    pd.concat([df, df], ignore_index=True)
    pd.concat([df, df.copy()], ignore_index=True)
    assert df.equals(df.copy())
    repr(df)
    df.to_string()
    pd.testing.assert_frame_equal(df, df.copy())


@pytest.mark.parametrize("name", sorted(FRAMES))
def test_attrs_are_json_shaped(name):
    """Nested plain dicts/lists of scalars: what `src/service` can serialise."""
    import json

    json.dumps(FRAMES[name].attrs, allow_nan=True)


def test_a_dataframe_in_attrs_would_actually_break_concat():
    """Guard the guard: prove the failure mode is real on this pandas version,
    so the tests above are pinning something rather than passing vacuously."""
    a = pd.DataFrame({"x": [1.0, 2.0]})
    b = pd.DataFrame({"x": [3.0, 4.0]})
    a.attrs["m"] = pd.DataFrame({"r": [1.0]})
    b.attrs["m"] = pd.DataFrame({"r": [1.0]})
    with pytest.raises(ValueError, match="ambiguous"):
        pd.concat([a, b], ignore_index=True)
