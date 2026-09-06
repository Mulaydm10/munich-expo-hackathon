"""Tests for the time-series sources wired by issue #8: grid_load + prices
(SMARD), weather (DWD), carbon (derived from SMARD generation mix). See
`tests/src/data/test_api.py` for charge_points/sites (issue #7) and
`contracts/src/data.md` for the interface these are built against.

No network: every test exercises canonicalise()/load()/meta() against small,
checked-in, obviously-synthetic fixtures under `fixtures/*_synthetic_*.csv`
(each carries a leading `# SYNTHETIC FIXTURE` comment). `data/raw/` in this
repo is empty; nothing here is presented as, or derived from, a real
download. `balancing` (regelleistung.net) is deferred -- see
`test_api.py::test_canonicalise_balancing_raises_not_implemented_and_explains_why`.

Every expected numeric value below was computed by hand from the fixture
files (see the comment next to each), independently of `src.data.api`'s
implementation -- these are ground truth, not a restatement of the code.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pandas as pd
import pytest

from src.data import api

FIXTURES = Path(__file__).parent / "fixtures"


def _root(tmp_path: Path, name: str = "root") -> Path:
    return tmp_path / name


def _seed(root: Path, source: str, *filenames: str) -> None:
    dest_dir = root / "raw" / source
    dest_dir.mkdir(parents=True, exist_ok=True)
    for fn in filenames:
        shutil.copy(FIXTURES / fn, dest_dir / fn)


# =====================================================================
# grid_load (SMARD, native 15-min)
# =====================================================================

GRID_LOAD_FILES = (
    "smard_load_synthetic_2026-03-15_hole.csv",
    "smard_load_synthetic_2026-03-29_dst_spring.csv",
    "smard_load_synthetic_2026-10-25_dst_autumn.csv",
)


def _grid_load(tmp_path: Path, name: str = "root", files: tuple[str, ...] = GRID_LOAD_FILES) -> pd.DataFrame:
    root = _root(tmp_path, name)
    _seed(root, "smard_load", *files)
    api.canonicalise("smard_load", root=root)
    return api.load("grid_load", root=root)


def test_grid_load_columns_dtype_and_no_duplicates(tmp_path: Path) -> None:
    df = _grid_load(tmp_path)
    api.require_columns(df, ["t", "load_mw", "wind_mw", "solar_mw", "residual_mw"])
    assert str(df["t"].dtype) == "datetime64[ns, UTC]"
    assert df["t"].is_monotonic_increasing
    assert df["t"].is_unique
    assert len(df) == 28  # 4 (hole day) + 8 (spring) + 16 (autumn)


def test_grid_load_residual_is_load_minus_wind_minus_solar(tmp_path: Path) -> None:
    df = _grid_load(tmp_path)
    computed = df["load_mw"] - df["wind_mw"] - df["solar_mw"]
    pd.testing.assert_series_equal(df["residual_mw"], computed, check_names=False)


def test_grid_load_dst_autumn_double_hour_round_trips_no_dup_no_drop(tmp_path: Path) -> None:
    """The real content of issue #8: Europe/Berlin 02:00-02:45 on
    2026-10-25 occurs twice (once CEST, once CET). Both instances must
    survive as distinct UTC rows forming one continuous 15-min grid --
    not collide into a duplicate, not drop either occurrence."""
    df = _grid_load(tmp_path)
    window = df[
        (df["t"] >= pd.Timestamp("2026-10-24T23:00:00Z")) & (df["t"] <= pd.Timestamp("2026-10-25T02:45:00Z"))
    ].reset_index(drop=True)
    assert len(window) == 16  # 4 hours * 4 quarters, no duplicate, no gap
    assert window["t"].is_unique
    assert (window["t"].diff().dropna().dt.total_seconds() == 900).all()  # 15 min

    # The two Berlin-local "02:00" instances map to two distinct, 1-hour-
    # apart UTC instants -- and carry different load_mw, which independently
    # proves they were not silently collapsed into the same row.
    first_pass = df.loc[df["t"] == pd.Timestamp("2026-10-25T00:00:00Z"), "load_mw"].iloc[0]
    second_pass = df.loc[df["t"] == pd.Timestamp("2026-10-25T01:00:00Z"), "load_mw"].iloc[0]
    assert first_pass == pytest.approx(392.0)  # 98.00 MWh / 0.25 h, fixture row 5 (1st pass)
    assert second_pass == pytest.approx(384.0)  # 96.00 MWh / 0.25 h, fixture row 9 (2nd pass)
    assert first_pass != second_pass


def test_grid_load_dst_spring_gap_is_not_a_row(tmp_path: Path) -> None:
    """2026-03-29: Europe/Berlin 02:00-02:59 never happens (clocks jump
    straight to 03:00 CEST). The gap must not appear as a row, and the
    surrounding rows must still form one continuous 15-min UTC sequence
    (the local-time gap collapses to nothing in UTC when handled correctly)."""
    df = _grid_load(tmp_path)
    window = df[
        (df["t"] >= pd.Timestamp("2026-03-29T00:00:00Z")) & (df["t"] <= pd.Timestamp("2026-03-29T01:45:00Z"))
    ].reset_index(drop=True)
    assert len(window) == 8  # 01:00-01:45 CET + 03:00-03:45 CEST, no hole
    assert window["t"].is_unique
    assert window["t"].is_monotonic_increasing
    assert (window["t"].diff().dropna().dt.total_seconds() == 900).all()  # 15 min
    # No row corresponds to a Berlin wall-clock time that never existed.
    assert not (df["load_mw"] == 0.0).any()  # sanity: nothing here is zero-filled


def test_grid_load_hole_is_a_missing_row_not_a_zero(tmp_path: Path) -> None:
    """fixtures/smard_load_synthetic_2026-03-15_hole.csv deliberately omits
    the 10:30-10:45 local interval. It must surface as an absent row in
    load()'s output, never as a present row with value 0.0."""
    df = _grid_load(tmp_path)
    missing_utc = pd.Timestamp("2026-03-15T09:30:00+00:00")  # 10:30 CET - 1h
    present_before = pd.Timestamp("2026-03-15T09:15:00+00:00")
    present_after = pd.Timestamp("2026-03-15T09:45:00+00:00")

    assert not (df["t"] == missing_utc).any(), "the punched hole must not appear as a row at all"
    assert (df["t"] == present_before).any()
    assert (df["t"] == present_after).any()
    # And the neighbouring real rows are not themselves zero (would make a
    # zero-fill of the hole indistinguishable from real low demand).
    assert df.loc[df["t"] == present_before, "load_mw"].iloc[0] == pytest.approx(992.0)
    assert df.loc[df["t"] == present_after, "load_mw"].iloc[0] == pytest.approx(980.0)


def test_grid_load_every_raw_file_is_ingested_not_just_the_first(tmp_path: Path) -> None:
    """The known landmine at api.py:267 (pre-#8): canonicalise() once read
    only raw_files[0]. Prove all three files are read by showing the
    combined row count strictly exceeds any subset and equals the sum."""
    only_spring = len(_grid_load(tmp_path, "a", files=(GRID_LOAD_FILES[1],)))
    only_autumn = len(_grid_load(tmp_path, "b", files=(GRID_LOAD_FILES[2],)))
    both = len(_grid_load(tmp_path, "c", files=(GRID_LOAD_FILES[1], GRID_LOAD_FILES[2])))
    assert only_spring == 8
    assert only_autumn == 16
    assert both == 24
    assert both > only_spring and both > only_autumn


def test_grid_load_meta_reports_native_15min(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _seed(root, "smard_load", *GRID_LOAD_FILES)
    api.canonicalise("smard_load", root=root)
    m = api.meta("grid_load", root=root)
    assert m["resample_method"] == "native_15min"
    assert m["resolution_min"] == 15
    assert m["source_url"] == api.SOURCES["smard_load"].url


def test_localize_berlin_drops_nonexistent_spring_gap_time_defensively() -> None:
    """Unit-level guard, independent of any fixture: even if a malformed
    upstream file *did* claim a Berlin wall-clock time inside the
    spring-forward gap, the localiser must drop it (NaT), never fabricate a
    plausible-looking UTC instant for a time that never happened."""
    naive = pd.Series([pd.Timestamp("2026-03-29 02:30:00")])
    utc, n_dropped = api._localize_berlin_naive_local(naive)
    assert n_dropped == 1
    assert utc.isna().all()


# =====================================================================
# prices (SMARD day-ahead, native hourly -> forward-fill upsample)
# =====================================================================

PRICE_FILES = (
    "epex_day_ahead_synthetic_2026-03-29_dst_spring.csv",
    "epex_day_ahead_synthetic_2026-10-25_dst_autumn.csv",
)


def _prices(tmp_path: Path, name: str = "root", files: tuple[str, ...] = PRICE_FILES) -> pd.DataFrame:
    root = _root(tmp_path, name)
    _seed(root, "epex_day_ahead", *files)
    api.canonicalise("epex_day_ahead", root=root)
    return api.load("prices", root=root)


def test_prices_columns_dtype_and_no_duplicates(tmp_path: Path) -> None:
    df = _prices(tmp_path)
    api.require_columns(df, ["t", "price_eur_mwh"])
    assert str(df["t"].dtype) == "datetime64[ns, UTC]"
    assert df["t"].is_monotonic_increasing
    assert df["t"].is_unique
    assert len(df) == 40  # (4 + 6) hours * 4 quarters


def test_prices_negative_day_ahead_prices_survive_with_sign(tmp_path: Path) -> None:
    """The most interesting hours in the project: negative day-ahead prices
    must reach load() with their sign intact, not clamped to zero or
    flipped positive. Both DST-duplicate "02:00" instances are negative
    here, with different magnitudes, so this also confirms they were not
    merged."""
    df = _prices(tmp_path)
    first_pass = df[(df["t"] >= pd.Timestamp("2026-10-25T00:00:00Z")) & (df["t"] < pd.Timestamp("2026-10-25T01:00:00Z"))]
    second_pass = df[(df["t"] >= pd.Timestamp("2026-10-25T01:00:00Z")) & (df["t"] < pd.Timestamp("2026-10-25T02:00:00Z"))]
    assert len(first_pass) == 4
    assert len(second_pass) == 4
    assert first_pass["price_eur_mwh"].tolist() == [-12.50] * 4
    assert second_pass["price_eur_mwh"].tolist() == [-8.25] * 4
    assert (df["price_eur_mwh"] < 0).any()


def test_prices_dst_spring_gap_is_not_a_row(tmp_path: Path) -> None:
    df = _prices(tmp_path)
    window = df[
        (df["t"] >= pd.Timestamp("2026-03-28T23:00:00Z")) & (df["t"] <= pd.Timestamp("2026-03-29T02:45:00Z"))
    ].reset_index(drop=True)
    assert len(window) == 16  # 4 hours (00,01,03,04 local) * 4 quarters, no hole for the nonexistent local 02:00
    assert window["t"].is_unique
    assert (window["t"].diff().dropna().dt.total_seconds() == 900).all()  # 15 min


def test_prices_forward_fill_upsample_is_recorded_in_meta(tmp_path: Path) -> None:
    """CONVENTIONS.md: upsampling hourly prices by forward-fill must be
    recorded in `meta`, not left implicit."""
    root = _root(tmp_path)
    _seed(root, "epex_day_ahead", *PRICE_FILES)
    api.canonicalise("epex_day_ahead", root=root)
    m = api.meta("prices", root=root)
    assert m["resample_method"] == "forward_fill_from_60min"
    assert m["native_resolution_min"] == 60
    assert m["resolution_min"] == 15  # the canonical table itself is 15-min


def test_prices_forward_fill_repeats_hourly_value_across_all_four_quarters(tmp_path: Path) -> None:
    df = _prices(tmp_path)
    hour = df[(df["t"] >= pd.Timestamp("2026-10-24T22:00:00Z")) & (df["t"] < pd.Timestamp("2026-10-24T23:00:00Z"))]
    assert len(hour) == 4
    assert hour["price_eur_mwh"].nunique() == 1
    assert hour["price_eur_mwh"].iloc[0] == pytest.approx(45.00)


def test_prices_every_raw_file_is_ingested_not_just_the_first(tmp_path: Path) -> None:
    only_spring = len(_prices(tmp_path, "a", files=(PRICE_FILES[0],)))
    only_autumn = len(_prices(tmp_path, "b", files=(PRICE_FILES[1],)))
    both = len(_prices(tmp_path, "c", files=PRICE_FILES))
    assert only_spring == 16
    assert only_autumn == 24
    assert both == 40
    assert both > only_spring and both > only_autumn


# =====================================================================
# weather (DWD, native hourly per station -> forward-fill upsample)
# =====================================================================

WEATHER_FILES = ("dwd_weather_synthetic_DWD-BER.csv", "dwd_weather_synthetic_DWD-MUC.csv")


def _weather(tmp_path: Path, name: str = "root", files: tuple[str, ...] = WEATHER_FILES) -> pd.DataFrame:
    root = _root(tmp_path, name)
    _seed(root, "dwd_weather", *files)
    api.canonicalise("dwd_weather", root=root)
    return api.load("weather", root=root)


def test_weather_columns_dtype_and_no_duplicate_t_station(tmp_path: Path) -> None:
    df = _weather(tmp_path)
    api.require_columns(df, ["t", "station_id", "temp_c", "wind_ms", "ghi_w_m2"])
    assert str(df["t"].dtype) == "datetime64[ns, UTC]"
    assert not df.duplicated(subset=["t", "station_id"]).any()
    assert len(df) == 48  # 2 stations * 6 hours * 4 quarters


def test_weather_two_stations_both_present_not_just_the_first_file(tmp_path: Path) -> None:
    only_ber = len(_weather(tmp_path, "a", files=(WEATHER_FILES[0],)))
    only_muc = len(_weather(tmp_path, "b", files=(WEATHER_FILES[1],)))
    both = len(_weather(tmp_path, "c", files=WEATHER_FILES))
    assert only_ber == 24
    assert only_muc == 24
    assert both == 48
    assert set(_weather(tmp_path, "d", files=WEATHER_FILES)["station_id"]) == {"DWD-BER", "DWD-MUC"}


def test_weather_forward_fill_upsample_recorded_in_meta(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _seed(root, "dwd_weather", *WEATHER_FILES)
    api.canonicalise("dwd_weather", root=root)
    m = api.meta("weather", root=root)
    assert m["resample_method"] == "forward_fill_from_60min"
    assert m["native_resolution_min"] == 60


def test_nearest_weather_station_is_deterministic(tmp_path: Path) -> None:
    first = api.nearest_weather_station(52.0, 13.0)
    second = api.nearest_weather_station(52.0, 13.0)
    third = api.nearest_weather_station(52.0, 13.0)
    assert first == second == third
    assert first == "DWD-BER"


def test_nearest_weather_station_is_sane_near_a_border() -> None:
    # A point just inside Germany near the French border, close to the
    # Saarbruecken reference station.
    result = api.nearest_weather_station(49.20, 6.80)
    assert result == "DWD-SAAR"
    assert result in api._WEATHER_STATIONS


# =====================================================================
# carbon (derived from SMARD generation mix, native hourly)
# =====================================================================

CARBON_FILES = (
    "generation_mix_synthetic_2026-06-15_baseline.csv",
    "generation_mix_synthetic_2026-03-29_dst_spring.csv",
    "generation_mix_synthetic_2026-10-25_dst_autumn.csv",
)


def _carbon(tmp_path: Path, name: str = "root", files: tuple[str, ...] = CARBON_FILES) -> pd.DataFrame:
    root = _root(tmp_path, name)
    _seed(root, "generation_mix", *files)
    api.canonicalise("generation_mix", root=root)
    return api.load("carbon", root=root)


def test_carbon_columns_dtype_and_no_duplicates(tmp_path: Path) -> None:
    df = _carbon(tmp_path)
    api.require_columns(df, ["t", "intensity_g_kwh"])
    assert str(df["t"].dtype) == "datetime64[ns, UTC]"
    assert df["t"].is_unique
    assert df["t"].is_monotonic_increasing
    assert len(df) == 32  # (4 + 2 + 2) hours * 4 quarters


def test_carbon_intensity_matches_hand_computed_weighted_average(tmp_path: Path) -> None:
    """Ground truth computed independently from
    generation_mix_synthetic_2026-06-15_baseline.csv's first row (12:00-13:00
    local): Braunkohle=20, Steinkohle=10, Erdgas=15, Kernenergie=0,
    Wind Onshore=25, Wind Offshore=5, Photovoltaik=40, Biomasse=10,
    Wasserkraft=5 MWh (all with a known factor), plus Pumpspeicher=2 MWh
    (no known factor -- excluded from both numerator and denominator, issue
    #50 finding 1). Factors g/kWh: 1080/820/490/12/11/12/45/230/24.
    weighted = 20*1080 + 10*820 + 15*490 + 0*12 + 25*11 + 5*12 + 40*45
               + 10*230 + 5*24 = 41705
    total_known_gen = 20+10+15+0+25+5+40+10+5 = 130
    intensity = 41705 / 130 = 320.807692...
    """
    df = _carbon(tmp_path)
    # 12:00-13:00 CEST local -> 10:00-11:00 UTC, forward-filled into 4 rows.
    hour = df[(df["t"] >= pd.Timestamp("2026-06-15T10:00:00Z")) & (df["t"] < pd.Timestamp("2026-06-15T11:00:00Z"))]
    assert len(hour) == 4  # forward-filled across the quarter-hours
    known = {
        "Braunkohle": (20, 1080.0),
        "Steinkohle": (10, 820.0),
        "Erdgas": (15, 490.0),
        "Kernenergie": (0, 12.0),
        "Wind Onshore": (25, 11.0),
        "Wind Offshore": (5, 12.0),
        "Photovoltaik": (40, 45.0),
        "Biomasse": (10, 230.0),
        "Wasserkraft": (5, 24.0),
    }
    weighted = sum(mwh * factor for mwh, factor in known.values())
    total_known = sum(mwh for mwh, _ in known.values())
    expected = weighted / total_known
    assert hour["intensity_g_kwh"].iloc[0] == pytest.approx(expected)
    assert hour["intensity_g_kwh"].nunique() == 1  # forward-fill: identical across all 4 quarters

    # The pre-#50 six-fuel-only computation (lignite/hard coal/gas/nuclear/
    # wind/solar, wind un-split at factor 11) excludes Biomasse/Wasserkraft
    # from BOTH numerator and denominator, which reads more fossil-heavy and
    # so must give a strictly higher (biased-high) number than the fixed
    # nine-category computation above -- pinning the direction of the bias,
    # not just that a change occurred.
    six_fuel_known = {
        "Braunkohle": (20, 1080.0),
        "Steinkohle": (10, 820.0),
        "Erdgas": (15, 490.0),
        "Kernenergie": (0, 12.0),
        "Wind": (25 + 5, 11.0),  # Onshore + Offshore combined, old un-split factor
        "Photovoltaik": (40, 45.0),
    }
    six_fuel_expected = sum(mwh * factor for mwh, factor in six_fuel_known.values()) / sum(
        mwh for mwh, _ in six_fuel_known.values()
    )
    assert expected < six_fuel_expected


def test_carbon_dst_autumn_double_hour_round_trips(tmp_path: Path) -> None:
    df = _carbon(tmp_path)
    first_pass = df[(df["t"] >= pd.Timestamp("2026-10-25T00:00:00Z")) & (df["t"] < pd.Timestamp("2026-10-25T01:00:00Z"))]
    second_pass = df[(df["t"] >= pd.Timestamp("2026-10-25T01:00:00Z")) & (df["t"] < pd.Timestamp("2026-10-25T02:00:00Z"))]
    assert len(first_pass) == 4
    assert len(second_pass) == 4
    assert first_pass["intensity_g_kwh"].iloc[0] != second_pass["intensity_g_kwh"].iloc[0]


def test_carbon_dst_spring_gap_is_not_a_row(tmp_path: Path) -> None:
    df = _carbon(tmp_path)
    window = df[
        (df["t"] >= pd.Timestamp("2026-03-29T00:00:00Z")) & (df["t"] <= pd.Timestamp("2026-03-29T01:45:00Z"))
    ]
    assert len(window) == 8  # 2 hours * 4 quarters, no hole
    assert window["t"].is_unique


def test_carbon_meta_records_emission_factors_and_forward_fill(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _seed(root, "generation_mix", *CARBON_FILES)
    api.canonicalise("generation_mix", root=root)
    m = api.meta("carbon", root=root)
    assert m["resample_method"] == "forward_fill_from_60min"
    assert m["emission_factors_g_kwh"]["Braunkohle"] == pytest.approx(1080.0)
    assert m["emission_factors_g_kwh"]["Biomasse"] == pytest.approx(230.0)
    assert m["emission_factors_g_kwh"]["Wasserkraft"] == pytest.approx(24.0)
    assert m["emission_factors_g_kwh"]["Wind Onshore"] == pytest.approx(11.0)
    assert m["emission_factors_g_kwh"]["Wind Offshore"] == pytest.approx(12.0)
    assert "Wind" not in m["emission_factors_g_kwh"]  # split, not the old single key
    assert m["source_url"] == api.SOURCES["generation_mix"].url

    # Issue #50 finding 1: Pumpspeicher (baseline fixture rows 1-3) has no
    # known emission factor and must not be silently dropped from the
    # denominator -- it is excluded from the weighted average, but that
    # exclusion is recorded here, visibly, not just skipped. Only categories
    # actually present are listed (Sonstige Erneuerbare/Konventionelle don't
    # appear in these fixtures, so they are correctly absent, not padded in).
    assert m["excluded_generation_categories"] == ["Pumpspeicher"]
    # 3 of the 8 hourly rows carry Pumpspeicher > 0; each forward-fills to
    # 4 quarter-hour rows in the canonical (15-min) table -> 12.
    assert m["n_rows_with_excluded_generation_category"] == 12
    assert m["excluded_generation_mwh_share_mean"] == pytest.approx(0.003856202383350869)
    assert m["excluded_generation_mwh_share_mean"] > 0  # must actually move, not sit pinned at zero


def test_carbon_excluded_category_generation_emits_a_warning(tmp_path: Path) -> None:
    """A raw file carrying Pumpspeicher (or another no-known-factor category)
    with nonzero generation must warn -- CONVENTIONS.md: a coercion (here, an
    exclusion) is never a silent omission."""
    root = _root(tmp_path)
    _seed(root, "generation_mix", "generation_mix_synthetic_2026-06-15_baseline.csv")
    with pytest.warns(UserWarning, match="no known emission factor"):
        api.canonicalise("generation_mix", root=root)


def test_carbon_intensity_changes_when_biomass_and_hydro_are_included(tmp_path: Path) -> None:
    """Issue #50 finding 1, pinned directly: a fixture that adds a
    generation category outside the original six (Biomasse, Wasserkraft)
    must change the computed intensity relative to what the pre-#50 code
    would have produced for this exact row. That code silently summed only
    its six hard-coded `_GENERATION_MIX_FUELS` keys (Braunkohle/Steinkohle/
    Erdgas/Kernenergie/Wind/Photovoltaik) and ignored Biomasse/Wasserkraft
    entirely -- both in the numerator and the denominator -- so its answer
    for this exact row equals `six_fuel_only` below, computed by hand
    independently of src.data.api.

    Uses an inline fixture that keeps the OLD single "Wind" column name (no
    Onshore/Offshore split): under the FIXED code this column is no longer a
    recognised key (real SMARD splits wind), so it is excluded -- visibly,
    with a warning -- exactly like Pumpspeicher is elsewhere in this file.
    That exclusion is a separate, correct behaviour (see
    test_carbon_excluded_category_generation_emits_a_warning); this test
    isolates the Biomasse/Wasserkraft omission bug by checking the fixed
    code's answer against the *known-categories-only* figure it should
    produce here (Braunkohle/Steinkohle/Erdgas/Kernenergie/Photovoltaik/
    Biomasse/Wasserkraft -- Wind is not a recognised key in the fixed code
    either, so it is excluded on both sides of this comparison).
    """
    root = _root(tmp_path, "inline")
    raw_dir = root / "raw" / "generation_mix"
    raw_dir.mkdir(parents=True)
    (raw_dir / "one_row.csv").write_text(
        "Datum von;Datum bis;Braunkohle [MWh];Steinkohle [MWh];Erdgas [MWh];"
        "Kernenergie [MWh];Wind [MWh];Photovoltaik [MWh];Biomasse [MWh];Wasserkraft [MWh]\n"
        "01.06.2026 12:00;01.06.2026 13:00;20,00;10,00;15,00;0,00;30,00;40,00;10,00;5,00\n",
        encoding="utf-8",
    )
    with pytest.warns(UserWarning, match="no known emission factor"):
        api.canonicalise("generation_mix", root=root)
    df = api.load("carbon", root=root)
    hour = df[(df["t"] >= pd.Timestamp("2026-06-01T10:00:00Z")) & (df["t"] < pd.Timestamp("2026-06-01T11:00:00Z"))]
    actual = hour["intensity_g_kwh"].iloc[0]

    # What the pre-#50 code (six hard-coded fuels, Wind included at 11 g/kWh,
    # Biomasse/Wasserkraft never even looked at) would compute for this row.
    six_fuel_only = (20 * 1080 + 10 * 820 + 15 * 490 + 0 * 12 + 30 * 11 + 40 * 45) / (20 + 10 + 15 + 0 + 30 + 40)
    # What the fixed code actually computes: known categories only
    # (Braunkohle/Steinkohle/Erdgas/Kernenergie/Photovoltaik/Biomasse/
    # Wasserkraft), Wind excluded because it is not a recognised key.
    known_only = (20 * 1080 + 10 * 820 + 15 * 490 + 0 * 12 + 40 * 45 + 10 * 230 + 5 * 24) / (
        20 + 10 + 15 + 0 + 40 + 10 + 5
    )

    # On the pre-#50 code, `actual` would equal `six_fuel_only` -- this is
    # the assertion that goes red on that code (not an import/KeyError, a
    # real value mismatch): old code silently ignores Biomasse/Wasserkraft
    # rather than including them, so its number lands on six_fuel_only, not
    # known_only.
    assert actual != pytest.approx(six_fuel_only)
    assert actual == pytest.approx(known_only)


# =====================================================================
# Issue #59: a partially-qualified real SMARD header
# =====================================================================

BASELINE_FILE = "generation_mix_synthetic_2026-06-15_baseline.csv"
MIXED_HEADER_FILE = "generation_mix_synthetic_2026-06-15_mixed_header.csv"
QUALIFIED_UNKNOWN_FILE = "generation_mix_synthetic_2026-06-15_qualified_unknown.csv"

# The measured pre-fix numbers, from running the parser at 1e23eb9 on the
# baseline fixture and on the same bytes with `Braunkohle [MWh]` renamed
# `Braunkohle [MWh] Berechnete Auflösungen`. Pinned as named constants so a
# regression reads as "it went back to the broken value", not merely "it
# changed".
INTENSITY_CORRECT_FIRST_ROW = 320.81
# Design's reported case: ONLY `Braunkohle [MWh]` qualified. Dropping a
# 1080 g/kWh fuel biases the metric DOWN.
INTENSITY_BRAUNKOHLE_ONLY_DROPPED = 182.77
# This fixture's own broken value, measured under the same bug. It qualifies
# three columns -- Braunkohle (1080) but also Wind Onshore (11) and
# Photovoltaik (45) -- so losing two near-zero-carbon fuels outweighs losing
# the lignite and the metric biases UP instead. Same defect, opposite sign,
# which is precisely why "the number moved" is not a safe test and the
# assertion below is equality against the clean run.
INTENSITY_MIXED_HEADER_UNFIXED = 400.67


def test_carbon_mixed_resolution_qualifier_header_does_not_drop_a_fuel(tmp_path: Path) -> None:
    """A real export qualifies only SOME unit columns with the resolution they
    were computed at. Before #59 such a column matched neither the known nor
    the excluded bucket, so the fuel ceased to exist for the parser: dropping
    Braunkohle (1080 g/kWh) took intensity_g_kwh down 43% with no raise and no
    warning.

    The assertion is on the physical number, not on the code path: identical
    generation must yield identical intensity whatever the header shape."""
    clean = _carbon(tmp_path, "clean", files=(BASELINE_FILE,))
    mixed = _carbon(tmp_path, "mixed", files=(MIXED_HEADER_FILE,))

    pd.testing.assert_series_equal(clean["intensity_g_kwh"], mixed["intensity_g_kwh"])
    assert clean["intensity_g_kwh"].iloc[0] == pytest.approx(INTENSITY_CORRECT_FIRST_ROW, abs=0.01)
    # the specific broken value this fixture produced before the fix, named
    assert mixed["intensity_g_kwh"].iloc[0] != pytest.approx(
        INTENSITY_MIXED_HEADER_UNFIXED, abs=0.01
    )


def test_carbon_qualified_braunkohle_alone_reproduces_designs_reported_case(
    tmp_path: Path,
) -> None:
    """The exact scenario reported on #59, pinned on its own: take the baseline
    bytes and qualify ONLY `Braunkohle [MWh]`. Before the fix this read 182.77
    against a correct 320.81 -- a 43% understatement of a figure the pitch
    leads with, with a byte-identical excluded list."""
    root = _root(tmp_path, "braunkohle_only")
    _seed(root, "generation_mix", BASELINE_FILE)
    path = root / "raw" / "generation_mix" / BASELINE_FILE
    path.write_bytes(
        path.read_bytes().replace(
            "Braunkohle [MWh]".encode(),
            "Braunkohle [MWh] Berechnete Auflösungen".encode(),
            1,
        )
    )
    api.canonicalise("generation_mix", root=root)
    df = api.load("carbon", root=root)

    assert df["intensity_g_kwh"].iloc[0] == pytest.approx(INTENSITY_CORRECT_FIRST_ROW, abs=0.01)
    assert df["intensity_g_kwh"].iloc[0] != pytest.approx(
        INTENSITY_BRAUNKOHLE_ONLY_DROPPED, abs=0.01
    )
    assert api.meta("carbon", root=root)["fuels_with_resolution_qualifier"] == ["Braunkohle"]


def test_carbon_resolution_qualifier_stripping_is_recorded_in_meta(tmp_path: Path) -> None:
    """CONVENTIONS.md: a coercion is observable. Stripping a qualifier off a
    header is a coercion, so it is a count and a name list in carbon.meta.json
    -- something that survives the process -- not a log line."""
    root = _root(tmp_path, "mixed")
    _seed(root, "generation_mix", MIXED_HEADER_FILE)
    api.canonicalise("generation_mix", root=root)
    m = api.meta("carbon", root=root)

    assert m["n_generation_columns_with_resolution_qualifier"] == 3
    assert m["fuels_with_resolution_qualifier"] == ["Braunkohle", "Photovoltaik", "Wind Onshore"]
    assert m["resolution_qualifiers_seen"] == ["Berechnete Auflösungen", "Originalauflösungen"]

    # ...and it is not pinned at a constant: a clean header records zero.
    clean_root = _root(tmp_path, "clean")
    _seed(clean_root, "generation_mix", BASELINE_FILE)
    api.canonicalise("generation_mix", root=clean_root)
    clean_meta = api.meta("carbon", root=clean_root)
    assert clean_meta["n_generation_columns_with_resolution_qualifier"] == 0
    assert clean_meta["fuels_with_resolution_qualifier"] == []


def test_carbon_excluded_share_moves_when_a_qualified_column_has_no_known_factor(
    tmp_path: Path,
) -> None:
    """The #59 bug was not only a wrong number, it was an audit metric that
    read as proof while proving nothing: `excluded_generation_mwh_share_mean`
    was byte-identical between a run that dropped Braunkohle and one that did
    not.

    So the metric must demonstrably be able to see a difference. A qualified
    column whose fuel has NO known factor must land in `excluded` and move the
    share -- if this assertion can fail, the metric is live."""
    clean_root = _root(tmp_path, "clean")
    _seed(clean_root, "generation_mix", BASELINE_FILE)
    api.canonicalise("generation_mix", root=clean_root)
    clean_meta = api.meta("carbon", root=clean_root)

    q_root = _root(tmp_path, "qualified_unknown")
    _seed(q_root, "generation_mix", QUALIFIED_UNKNOWN_FILE)
    api.canonicalise("generation_mix", root=q_root)
    q_meta = api.meta("carbon", root=q_root)

    # the qualified unknown fuel is EXCLUDED, not vanished
    assert q_meta["excluded_generation_categories"] == ["Pumpspeicher", "Sonstige Konventionelle"]
    assert q_meta["fuels_with_resolution_qualifier"] == ["Sonstige Konventionelle"]
    # and the share actually moves
    assert q_meta["excluded_generation_mwh_share_mean"] > clean_meta[
        "excluded_generation_mwh_share_mean"
    ]


def test_carbon_same_fuel_at_two_resolutions_is_refused_not_double_counted(
    tmp_path: Path,
) -> None:
    """A real download centre can offer one fuel at BOTH `Originalauflösungen`
    and `Berechnete Auflösungen`. Now that the qualifier is stripped, both
    stems read `Braunkohle` and summing them would double-count that fuel --
    the same class of silent wrongness #59 is about, in the other direction.
    Refuse by name instead."""
    root = _root(tmp_path, "collide")
    _seed(root, "generation_mix", BASELINE_FILE)
    path = root / "raw" / "generation_mix" / BASELINE_FILE
    lines = path.read_bytes().split(b"\n")
    out = []
    seen_header = False
    for line in lines:
        if line.startswith(b"#") or not line.strip():
            out.append(line)
        elif not seen_header:
            out.append(line + ";Braunkohle [MWh] Originalauflösungen".encode())
            seen_header = True
        else:
            out.append(line + b";20,00")
    path.write_bytes(b"\n".join(out))

    with pytest.raises(ValueError, match="same fuel at more than one resolution"):
        api.canonicalise("generation_mix", root=root)
