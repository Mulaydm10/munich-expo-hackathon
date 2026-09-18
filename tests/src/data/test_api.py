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


def test_sources_wires_charge_points_and_the_issue_8_time_series_sources() -> None:
    assert "charge_points" in api.SOURCES
    assert api.SOURCES["charge_points"].canonical_table == "sites"
    assert api.SOURCES["smard_load"].canonical_table == "grid_load"
    assert api.SOURCES["epex_day_ahead"].canonical_table == "prices"
    assert api.SOURCES["dwd_weather"].canonical_table == "weather"
    assert api.SOURCES["generation_mix"].canonical_table == "carbon"
    # `balancing` is deliberately NOT in SOURCES: deferred, see the
    # canonicalise() test below.
    assert "regelleistung" not in api.SOURCES
    assert "balancing" not in api.SOURCES


def test_fetch_raises_not_implemented_for_unwired_source_name(tmp_path: Path) -> None:
    from datetime import date

    with pytest.raises(NotImplementedError):
        api.fetch("balancing", start=date(2026, 1, 1), end=date(2026, 1, 2), root=tmp_path)


def test_real_sources_are_fetch_wired() -> None:
    assert api._FETCH_WIRED == {
        "charge_points",
        "smard_load",
        "epex_day_ahead",
        "generation_mix",
        "dwd_weather",
    }


def test_canonicalise_raises_not_implemented_for_unwired_source_name(tmp_path: Path) -> None:
    with pytest.raises(NotImplementedError):
        api.canonicalise("nonexistent_source", root=tmp_path)


def test_canonicalise_balancing_raises_not_implemented_and_explains_why(tmp_path: Path) -> None:
    # regelleistung.net's balancing capacity auctions may need an account;
    # #8 defers this rather than faking a `balancing` table -- and says so
    # in the exception, not just in a PR comment.
    with pytest.raises(NotImplementedError, match="regelleistung"):
        api.canonicalise("balancing", root=tmp_path)


def test_require_columns_raises_on_missing_column() -> None:
    df = pd.DataFrame({"a": [1]})
    api.require_columns(df, ["a"])  # does not raise
    with pytest.raises(AssertionError):
        api.require_columns(df, ["a", "b"])


# --- multi-file ingestion and line-terminator tolerance -----------------------
# Both of these were real bugs found in review of PR #20, not hypotheticals.
# The first was silent: canonicalise() read raw_files[0] and dropped the rest
# with no error, so a partitioned source produced a short table that every
# other test still passed on. #8 (SMARD/DWD) is date-partitioned, so the very
# next issue in this lane would have walked into it.


def _split_fixture_rows() -> tuple[bytes, bytes, int]:
    """The fixture's data rows split in half, each half keeping preamble+header."""
    raw = FIXTURE.read_bytes()
    parts = raw.split(b"\r\n")
    hdr = next(i for i, l in enumerate(parts) if l.split(b";", 1)[0] == b"Ladeeinrichtungs-ID")
    head, rows = parts[: hdr + 1], [r for r in parts[hdr + 1 :] if r.strip()]
    mid = len(rows) // 2
    a = b"\r\n".join(head + rows[:mid]) + b"\r\n"
    b = b"\r\n".join(head + rows[mid:]) + b"\r\n"
    return a, b, len(rows)


def test_every_raw_file_is_ingested_not_just_the_first(tmp_path: Path) -> None:
    a, b, _ = _split_fixture_rows()

    def sites_for(files: list[tuple[str, bytes]], name: str) -> int:
        root = _fresh_root(tmp_path, name)
        for fn, data in files:
            _seed_raw(root, data, filename=fn)
        api.canonicalise("charge_points", root=root)
        return len(api.load("sites", root=root))

    only_a = sites_for([("a.csv", a)], "a")
    only_b = sites_for([("b.csv", b)], "b")
    both = sites_for([("a.csv", a), ("b.csv", b)], "both")

    # Halves may share a co-located site, so `both` need not equal a + b --
    # but it must exceed either half, and it must equal the whole fixture.
    assert both > only_a and both > only_b, (
        f"both={both} did not exceed halves ({only_a}, {only_b}): a raw file was dropped"
    )
    assert both == FIXTURE_SITE_ROWS


def test_co_located_installations_aggregate_across_raw_files(tmp_path: Path) -> None:
    """Splitting the same rows across two files must not create duplicate sites."""
    a, b, _ = _split_fixture_rows()
    root = _fresh_root(tmp_path, "cross")
    _seed_raw(root, a, filename="a.csv")
    _seed_raw(root, b, filename="b.csv")
    api.canonicalise("charge_points", root=root)
    df = api.load("sites", root=root)
    assert df["site_id"].is_unique
    assert len(df) == FIXTURE_SITE_ROWS


def test_lf_only_export_parses_like_crlf(tmp_path: Path) -> None:
    """A re-saved or LF-normalised export must not fail to find the header."""
    raw = FIXTURE.read_bytes()
    assert b"\r\n" in raw, "fixture is expected to ship CRLF"
    lf = raw.replace(b"\r\n", b"\n")

    root = _fresh_root(tmp_path, "lf")
    _seed_raw(root, lf, filename="lf.csv")
    api.canonicalise("charge_points", root=root)
    assert len(api.load("sites", root=root)) == FIXTURE_SITE_ROWS


def test_single_file_sha256_is_unchanged_by_the_multifile_fix(tmp_path: Path) -> None:
    """Provenance for the one-file case must stay byte-identical: the fixture's
    recorded sha256 is a published claim, not an implementation detail."""
    root = _fresh_root(tmp_path, "sha")
    _seed_raw(root)
    api.canonicalise("charge_points", root=root)
    assert api.meta("sites", root=root)["sha256"] == FIXTURE_SHA256
