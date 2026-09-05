"""Tests for src.data.api against contracts/src/data.md and the acceptance
criteria of agent-bus issue #7.

No network: every test exercises canonicalise()/load()/meta() against the
checked-in fixture `fixtures/ladesaeulenregister_excerpt.csv` — a ~50-row
excerpt of the real Bundesnetzagentur Ladesaeulenregister (see that file's
provenance in the issue/commit — sha256, source URL, retrieved-at are
asserted below via meta(), not just claimed). fetch() is never called here.
"""

from __future__ import annotations

import csv
import io
import random
import shutil
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

from src.data import api

FIXTURE = Path(__file__).parent / "fixtures" / "ladesaeulenregister_excerpt.csv"

# Ground truth for the checked-in fixture, computed once from the fixture
# itself (see the commit that added it). If either of these ever changes,
# the fixture changed and these must be re-derived — they are not
# independent of the file, they *are* its provenance.
FIXTURE_SHA256 = "5ecd36a79b4bace685886c1ce47c2a4d6c681d84abfd14345eb9390c0bccbbfd"
FIXTURE_SITE_ROWS = 43  # after collapsing co-located installations into sites

CONTRACT_SITE_COLUMNS = {
    "site_id",
    "operator",
    "lat",
    "lon",
    "postcode",
    "state",
    "rated_power_kw",
    "n_points",
    "is_dc",
    "commissioned",
}


def _fresh_root(tmp_path: Path, name: str = "root") -> Path:
    root = tmp_path / name
    (root / "raw" / "charge_points").mkdir(parents=True)
    return root


def _seed_raw(root: Path, raw_bytes: bytes | None = None, filename: str = "ladesaeulenregister_excerpt.csv") -> Path:
    dest = root / "raw" / "charge_points" / filename
    if raw_bytes is None:
        shutil.copy(FIXTURE, dest)
    else:
        dest.write_bytes(raw_bytes)
    return dest


def _canonicalised_sites(tmp_path: Path, name: str = "root") -> pd.DataFrame:
    root = _fresh_root(tmp_path, name)
    _seed_raw(root)
    api.canonicalise("charge_points", root=root)
    return api.load("sites", root=root)


# --- fixture sanity: prove the checked-in bytes are what we think they are ----


def test_fixture_is_the_real_download_bytes_we_recorded() -> None:
    raw = FIXTURE.read_bytes()
    import hashlib

    assert hashlib.sha256(raw).hexdigest() == FIXTURE_SHA256
    # The real registry export ships UTF-8-with-BOM, `;`-separated, German
    # decimal commas — assert the raw bytes, not a re-encoded copy, look
    # exactly like that (this is what makes the encoding-fallback test below
    # meaningful rather than vacuous).
    assert raw.startswith(b"\xef\xbb\xbf")  # UTF-8 BOM
    assert b"\r\n" in raw
    assert b";" in raw
    assert "Baden-Württemberg".encode("utf-8") in raw


# --- contract columns / dtypes -------------------------------------------------


def test_sites_columns_match_contract_exactly(tmp_path: Path) -> None:
    df = _canonicalised_sites(tmp_path)
    assert set(df.columns) == CONTRACT_SITE_COLUMNS
    api.require_columns(df, sorted(CONTRACT_SITE_COLUMNS))


def test_sites_lat_lon_numeric_and_power_positive(tmp_path: Path) -> None:
    df = _canonicalised_sites(tmp_path)
    assert len(df) == FIXTURE_SITE_ROWS
    assert pd.api.types.is_numeric_dtype(df["lat"])
    assert pd.api.types.is_numeric_dtype(df["lon"])
    assert df["lat"].between(47.0, 55.0).all()  # sanity: real German latitudes
    assert df["lon"].between(5.0, 15.0).all()
    assert (df["rated_power_kw"] > 0).all()


# --- German locale parsing ------------------------------------------------------


def test_german_decimal_comma_and_umlauts_parse_correctly(tmp_path: Path) -> None:
    df = _canonicalised_sites(tmp_path)

    schneiderhan = df[df["operator"] == "Katrin Schneiderhan"]
    assert len(schneiderhan) == 1
    # source cell was "16,7" (German decimal comma) — must become 16.7, not 167 or NaN.
    assert schneiderhan["rated_power_kw"].iloc[0] == pytest.approx(16.7)

    hahn = df[df["operator"] == "Hahn Automobile GmbH & Co. KG"]
    assert len(hahn) == 1
    assert hahn["rated_power_kw"].iloc[0] == pytest.approx(62.5)
    assert bool(hahn["is_dc"].iloc[0]) is True

    # Every row's state is "Baden-Württemberg" or "Bayern" in this excerpt;
    # a mis-decoded file would mangle the "ü" into mojibake or a decode error.
    assert set(df["state"]) == {"Baden-Württemberg", "Bayern"}


def test_decode_raw_falls_back_to_cp1252_for_non_utf8_bytes() -> None:
    # The acceptance criteria are explicit that a test fed only clean UTF-8
    # proves nothing about German-locale handling. The real download today
    # is UTF-8-with-BOM (asserted above), but src.data.api must not silently
    # corrupt umlauts if a future/alternate export ships latin-1/cp1252
    # instead — it should decode it correctly rather than mojibake-ing it.
    sample = "Ladeeinrichtungs-ID;Betreiber\r\n1;Stadtwerke Köln GmbH\r\n"
    latin1_bytes = sample.encode("cp1252")
    with pytest.raises(UnicodeDecodeError):
        latin1_bytes.decode("utf-8-sig")  # prove this sample is NOT valid UTF-8
    decoded = api._decode_raw(latin1_bytes)
    assert "Stadtwerke Köln GmbH" in decoded


# --- site_id stability -----------------------------------------------------


def test_site_id_stable_across_repeat_canonicalisation(tmp_path: Path) -> None:
    root = _fresh_root(tmp_path)
    _seed_raw(root)
    api.canonicalise("charge_points", root=root)
    first = api.load("sites", root=root)

    api.canonicalise("charge_points", root=root)  # re-run against the same raw file
    second = api.load("sites", root=root)

    pd.testing.assert_frame_equal(first, second)


def test_site_id_stable_across_shuffled_raw_row_order(tmp_path: Path) -> None:
    baseline = _canonicalised_sites(tmp_path, name="baseline")

    raw_text = FIXTURE.read_bytes().decode("utf-8-sig")
    # Split on the literal row terminator, not str.splitlines(): the registry's
    # quoted "Public Key" cells contain other Unicode line-boundary code
    # points, and splitlines() would fragment those fields before the shuffle
    # even runs (mirrors the fix in src.data.api._parse_charge_points).
    lines = raw_text.split("\r\n")
    header_end = next(i for i, line in enumerate(lines) if line.split(";", 1)[0] == "Ladeeinrichtungs-ID")
    head, data = lines[: header_end + 1], lines[header_end + 1 :]
    assert data, "fixture must have data rows to shuffle"

    shuffled_data = data[:]
    random.Random(42).shuffle(shuffled_data)
    assert shuffled_data != data  # the shuffle must actually have moved something

    shuffled_bytes = ("﻿" + "\r\n".join(head + shuffled_data) + "\r\n").encode("utf-8")

    shuffled_root = _fresh_root(tmp_path, name="shuffled")
    _seed_raw(shuffled_root, raw_bytes=shuffled_bytes, filename="shuffled.csv")
    api.canonicalise("charge_points", root=shuffled_root)
    shuffled = api.load("sites", root=shuffled_root)

    pd.testing.assert_frame_equal(baseline, shuffled)


def test_colocated_installations_aggregate_into_one_site(tmp_path: Path) -> None:
    # Rows 43-46 of the real file are 4 DC installations (300+300+172+172 kW,
    # 2 points each) at identical operator/lat/lon in Langenau/Seligweiler —
    # they must collapse to exactly one `sites` row, not four. (postcode
    # 89129 also holds other, distinct EnBW sites in this excerpt, so the
    # site_id — derived from operator+lat+lon, not just operator+postcode —
    # is what actually identifies this one cluster.)
    df = _canonicalised_sites(tmp_path)
    cluster = df[df["site_id"] == "Baden-Württemberg-89129-df4afbdf"]
    assert len(cluster) == 1
    row = cluster.iloc[0]
    assert row["operator"] == "EnBW mobility+ AG und Co.KG"
    assert row["rated_power_kw"] == pytest.approx(944.0)
    assert int(row["n_points"]) == 8
    assert bool(row["is_dc"]) is True

    # Sanity: this postcode really does contain other, separate EnBW sites —
    # otherwise the test above would be trivially true for the wrong reason.
    other_enbw_sites = df[(df["operator"] == "EnBW mobility+ AG und Co.KG") & (df["postcode"] == "89129")]
    assert len(other_enbw_sites) > 1


# --- MissingTable ------------------------------------------------------------


def test_load_prices_raises_missing_table_naming_the_fetch_call(tmp_path: Path) -> None:
    root = _fresh_root(tmp_path)
    with pytest.raises(api.MissingTable) as excinfo:
        api.load("prices", root=root)
    message = str(excinfo.value)
    assert "fetch(" in message
    assert "'epex_day_ahead'" in message
    assert excinfo.value.table == "prices"


def test_meta_missing_table_also_raises_missing_table(tmp_path: Path) -> None:
    root = _fresh_root(tmp_path)
    with pytest.raises(api.MissingTable) as excinfo:
        api.meta("grid_load", root=root)
    assert "fetch(" in str(excinfo.value)


# --- meta() provenance -----------------------------------------------------


def test_meta_sites_reports_all_five_provenance_fields(tmp_path: Path) -> None:
    root = _fresh_root(tmp_path)
    _seed_raw(root)
    api.canonicalise("charge_points", root=root)
    m = api.meta("sites", root=root)

    assert m["source_url"] == api.SOURCES["charge_points"].url
    assert m["license"] == api.SOURCES["charge_points"].license
    assert m["rows"] == FIXTURE_SITE_ROWS
    assert m["sha256"] == FIXTURE_SHA256
    # retrieved_at must be a real, parseable UTC timestamp.
    parsed = datetime.fromisoformat(m["retrieved_at"])
    assert parsed.tzinfo is not None


# --- SOURCES / fetch / require_columns / nearest_weather_station --------------


def test_sources_only_wires_charge_points() -> None:
    assert "charge_points" in api.SOURCES
    assert api.SOURCES["charge_points"].canonical_table == "sites"


def test_fetch_raises_not_implemented_for_unwired_sources(tmp_path: Path) -> None:
    from datetime import date

    with pytest.raises(NotImplementedError):
        api.fetch("prices", start=date(2026, 1, 1), end=date(2026, 1, 2), root=tmp_path)


def test_canonicalise_raises_not_implemented_for_unwired_sources(tmp_path: Path) -> None:
    with pytest.raises(NotImplementedError):
        api.canonicalise("prices", root=tmp_path)


def test_require_columns_raises_on_missing_column() -> None:
    df = pd.DataFrame({"a": [1]})
    api.require_columns(df, ["a"])  # does not raise
    with pytest.raises(AssertionError):
        api.require_columns(df, ["a", "b"])


def test_nearest_weather_station_not_implemented_yet() -> None:
    with pytest.raises(NotImplementedError):
        api.nearest_weather_station(48.0, 9.0)
