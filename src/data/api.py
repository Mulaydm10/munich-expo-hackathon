"""src.data — public API.

Ingest public German energy + charge-point data into canonical tables.

This module is the lane's ONLY cross-lane surface: other lanes import
`src.data.api` and nothing else. See `contracts/src/data.md` for the
interface this lane owes the rest of the project, and
`contracts/CONVENTIONS.md` for units, time handling and the data layout.

Fetch is wired for all five live sources and was verified against downloads on
2026-09-18: Bundesnetzagentur charge points, SMARD load/prices/generation,
and DWD weather. `balancing` (regelleistung.net) remains deferred.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
import re
import warnings
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

LANE = "src/data"

# Europe/Berlin, used to localise the German-market sources (SMARD) whose raw
# timestamps are local wall-clock, not UTC. DWD's raw timestamps are already
# UTC (see `_parse_dwd_weather_raw`), so this constant is not used there.
_BERLIN = ZoneInfo("Europe/Berlin")

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _default_data_root() -> Path:
    env = os.environ.get("FLEXGRID_DATA")
    return Path(env).expanduser() if env else _REPO_ROOT / "data"


DATA_ROOT: Path = _default_data_root()

_SMARD_ENDPOINT = "https://www.smard.de/nip-download-manager/nip/download/market-data"


@dataclass(frozen=True)
class Source:
    """One row of `SOURCES`: everything needed to fetch and attribute a source."""

    name: str
    url: str
    license: str
    resolution_min: int | None  # None for a point-in-time registry snapshot
    canonical_table: str


SOURCES: dict[str, Source] = {
    "charge_points": Source(
        name="charge_points",
        url=(
            "https://data.bundesnetzagentur.de/Bundesnetzagentur/DE/"
            "Fachthemen/ElektrizitaetundGas/E-Mobilitaet/"
            "Ladesaeulenregister_BNetzA_2026-09-01.csv"
        ),
        license="Creative Commons Namensnennung 4.0 International (CC BY 4.0), Bundesnetzagentur",
        resolution_min=None,
        canonical_table="sites",
    ),
    # Human-facing download-center page:
    # https://www.smard.de/home/downloadcenter/download-marktdaten
    "smard_load": Source(
        name="smard_load",
        url=_SMARD_ENDPOINT,
        license="Nutzung frei mit Quellenangabe, Bundesnetzagentur | SMARD.de",
        resolution_min=15,
        canonical_table="grid_load",
    ),
    "epex_day_ahead": Source(
        name="epex_day_ahead",
        url=_SMARD_ENDPOINT,
        license="Nutzung frei mit Quellenangabe, Bundesnetzagentur | SMARD.de",
        resolution_min=15,
        canonical_table="prices",
    ),
    # DWD Open Data: public, no key, well-known stable directory root.
    "dwd_weather": Source(
        name="dwd_weather",
        url="https://opendata.dwd.de/climate_environment/CDC/observations_germany/climate/hourly/",
        license="Creative Commons Namensnennung 4.0 International (CC BY 4.0), Deutscher Wetterdienst",
        resolution_min=60,
        canonical_table="weather",
    ),
    # `carbon` is deliberately NOT sourced from ENTSO-E: the Transparency
    # Platform's REST API needs a free security token, which is an
    # `agent:devin` issue, not something to commit or work around. Instead we
    # derive intensity from SMARD's public generation-by-fuel-type export
    # (same download center, no key) combined with published emission
    # factors -- see CARBON_INTENSITY_FACTORS_G_KWH below.
    "generation_mix": Source(
        name="generation_mix",
        url=_SMARD_ENDPOINT,
        license="Nutzung frei mit Quellenangabe, Bundesnetzagentur | SMARD.de",
        resolution_min=15,
        canonical_table="carbon",
    ),
}

_SMARD_REQUESTS: dict[str, list[tuple[str, str, list[int]]]] = {
    "smard_load": [
        ("consumption", "DE", [5000410]),
        ("generation", "DE", [1004067, 1001225, 1004068]),
    ],
    "epex_day_ahead": [("price", "DE-LU", [8004169])],
    "generation_mix": [
        (
            "generation",
            "DE",
            [
                1004066,
                1001226,
                1001225,
                1004067,
                1004068,
                1001228,
                1001223,
                1004069,
                1004071,
                1004070,
                1001227,
                1001224,
            ],
        )
    ],
}

_FETCH_WIRED = {
    "charge_points",
    "smard_load",
    "epex_day_ahead",
    "generation_mix",
    "dwd_weather",
}

# Human-readable "how to build this" pointer used only to compose MissingTable
# messages.
# The mapping mirrors "Sources to wire" in contracts/src/data.md.
_TABLE_SOURCE: dict[str, str] = {
    "sites": "charge_points",
    "grid_load": "smard_load",
    "prices": "epex_day_ahead",
    "weather": "dwd_weather",
    "balancing": "regelleistung",
    "carbon": "generation_mix",
}


class MissingTable(LookupError):
    """Raised by load()/meta() when a canonical table has not been built yet.

    The message names the exact fetch()/canonicalise() call that would build
    the table — every other lane's agent reads this string to unblock itself.
    """

    def __init__(self, table: str, how_to_get_it: str) -> None:
        self.table = table
        self.how_to_get_it = how_to_get_it
        super().__init__(
            f"canonical table {table!r} has not been built yet. "
            f"Build it with: {how_to_get_it}"
        )


def _how_to_build(table: str) -> str:
    source = _TABLE_SOURCE.get(table)
    if source is None:
        return f"canonicalise({table!r}) — no source is named for this table yet"
    return f"fetch({source!r}, start=..., end=...); canonicalise({source!r})"


def require_columns(df: pd.DataFrame, cols: Sequence[str]) -> None:
    """Boundary assert used repo-wide: fail loudly if `df` is missing any of `cols`."""
    missing = [c for c in cols if c not in df.columns]
    assert not missing, f"missing required columns {missing}; have {list(df.columns)}"


def _month_chunks(start: date, end: date) -> list[tuple[date, date]]:
    chunks: list[tuple[date, date]] = []
    current = start.replace(day=1)
    while current <= end:
        next_month = (current.replace(day=28) + timedelta(days=4)).replace(day=1)
        chunks.append((max(start, current), min(end, next_month - timedelta(days=1))))
        current = next_month
    return chunks


def _smard_timestamp_ms(value: date, *, last_interval: bool = False) -> int:
    timestamp = pd.Timestamp(value)
    if last_interval:
        timestamp = timestamp + pd.Timedelta(days=1) - pd.Timedelta(minutes=15)
    return int(timestamp.tz_localize(_BERLIN).timestamp() * 1000)


def _fetch_smard_chunk(
    *,
    source: str,
    stem: str,
    region: str,
    module_ids: list[int],
    chunk_start: date,
    chunk_end: date,
    root: Path,
) -> Path:
    import requests

    dest_dir = Path(root) / "raw" / source
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{stem}_{chunk_start:%Y-%m-%d}_{chunk_end:%Y-%m-%d}.csv"
    today = datetime.now(timezone.utc).date()
    if dest.exists() and dest.stat().st_size > 0 and chunk_end < today:
        return dest

    body = {
        "request_form": [
            {
                "format": "CSV",
                "moduleIds": module_ids,
                "region": region,
                "timestamp_from": _smard_timestamp_ms(chunk_start),
                "timestamp_to": _smard_timestamp_ms(chunk_end, last_interval=True),
                "type": "discrete",
                "language": "de",
                "resolution": "quarterhour",
            }
        ]
    }
    response = requests.post(_SMARD_ENDPOINT, json=body, timeout=180)
    response.raise_for_status()
    if not response.content:
        raise ValueError(f"SMARD returned an empty body for {source}/{dest.name}")
    if response.content[:2] == b"PK":
        raise ValueError(
            f"SMARD returned a ZIP for {source}/{dest.name}; "
            "request one category at a time"
        )
    dest.write_bytes(response.content)
    return dest


_DWD_ROOT = "https://opendata.dwd.de/climate_environment/CDC/observations_germany/climate/hourly"


def _fetch_dwd_file(
    *,
    station_id: str,
    kind: str,
    path: str,
    root: Path,
) -> Path | None:
    import requests

    dest_dir = Path(root) / "raw" / "dwd_weather"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{kind}_{station_id}.zip"
    if dest.exists() and dest.stat().st_size > 0:
        age = datetime.now(timezone.utc).timestamp() - dest.stat().st_mtime
        if age < 24 * 3600:
            return dest
    response = requests.get(f"{_DWD_ROOT}/{path}", timeout=180)
    if response.status_code == 404 and kind == "st":
        return None
    response.raise_for_status()
    if not response.content:
        raise ValueError(f"DWD returned an empty body for {path}")
    dest.write_bytes(response.content)
    return dest


def fetch(source: str, *, start: date, end: date, root: Path = DATA_ROOT) -> list[Path]:
    """Download source bytes to data/raw/<source>/.

    For charge_points and DWD, start/end are intentionally unused because
    their snapshot/recent product windows are fixed; DWD start is only checked
    against that window's age.
    """
    if source not in SOURCES:
        raise NotImplementedError(
            f"source {source!r} is unknown; only {sorted(SOURCES)} are implemented"
        )
    if source not in _FETCH_WIRED:
        raise NotImplementedError(f"fetch({source!r}, ...) is not wired")
    import requests  # imported lazily: fetch() is exercised by hand, never by pytest

    if end < start:
        raise ValueError("end must be on or after start")
    if source in _SMARD_REQUESTS:
        paths = []
        for chunk_start, chunk_end in _month_chunks(start, end):
            for stem, region, module_ids in _SMARD_REQUESTS[source]:
                paths.append(
                    _fetch_smard_chunk(
                        source=source,
                        stem=stem,
                        region=region,
                        module_ids=module_ids,
                        chunk_start=chunk_start,
                        chunk_end=chunk_end,
                        root=root,
                    )
                )
        return sorted(paths)
    if source == "dwd_weather":
        cutoff = datetime.now(timezone.utc).date() - timedelta(days=500)
        if start < cutoff:
            raise ValueError("dwd_weather recent/ data covers at most the last 500 days")
        paths = []
        for station_id in _WEATHER_STATIONS:
            paths.append(
                _fetch_dwd_file(
                    station_id=station_id,
                    kind="tu",
                    path=f"air_temperature/recent/stundenwerte_TU_{station_id}_akt.zip",
                    root=root,
                )
            )
            paths.append(
                _fetch_dwd_file(
                    station_id=station_id,
                    kind="ff",
                    path=f"wind/recent/stundenwerte_FF_{station_id}_akt.zip",
                    root=root,
                )
            )
            paths.append(
                _fetch_dwd_file(
                    station_id=station_id,
                    kind="st",
                    path=f"solar/stundenwerte_ST_{station_id}_row.zip",
                    root=root,
                )
            )
        return sorted(path for path in paths if path is not None)

    src = SOURCES[source]
    dest_dir = Path(root) / "raw" / source
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.url.rsplit("/", 1)[-1]
    if dest.exists() and dest.stat().st_size > 0:
        return [dest]
    response = requests.get(src.url, timeout=120)
    response.raise_for_status()
    dest.write_bytes(response.content)
    return [dest]


# --- charge_points -> sites ------------------------------------------------

_HEADER_MARKER = "Ladeeinrichtungs-ID"

# Contract column order for the `sites` table (site_id is the key, listed
# separately in contracts/src/data.md).
_SITE_COLUMNS = [
    "operator",
    "lat",
    "lon",
    "postcode",
    "state",
    "rated_power_kw",
    "n_points",
    "is_dc",
    "commissioned",
]


def _decode_raw(raw: bytes) -> str:
    """The registry export has shipped as UTF-8-with-BOM; fall back to cp1252
    (a strict superset of latin-1 for German text) for older/alternate
    exports so a change in the source's encoding doesn't silently corrupt
    umlauts instead of failing loudly."""
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252")


# A number as a German-locale export actually writes one: `.` groups thousands, `,` is
# the decimal mark. Either the digits carry thousands groups of exactly three (`11.349`,
# `1.234.567`) or they carry none at all (`22`, `48`); a decimal part is `,` plus digits.
# Anything else -- notably a bare `.` that is NOT a thousands group, e.g. `41.2` -- is
# ambiguous between the two locales and is refused rather than guessed at.
_GERMAN_NUMBER_RE = re.compile(r"^[+-]?(?:\d{1,3}(?:\.\d{3})+|\d+)(?:,\d+)?$")


def _decimal_comma_to_float(series: pd.Series) -> pd.Series:
    """German-locale numeric strings -> float. Shared by every source in this
    module that carries German-locale numbers (charge_points, and the
    SMARD-derived grid_load/prices/carbon sources); DWD's raw numbers use a
    plain decimal point (see `_parse_dwd_weather_raw`).

    Issue #50 item 2: this used to convert the decimal comma and leave the
    THOUSANDS separator in place, so a real export's `11.349,25` became the
    unparseable `11.349.25`. Every figure in the fixtures happens to be small
    enough to have no thousands group, which is why the synthetic shape passed
    and the first real SMARD download would not have.

    Both separators are handled now, and the string is validated FIRST. Silently
    stripping every `.` would turn a plain-decimal-point `41.2` into `412` -- a
    1000x error in a load or price series, arriving with no signal at all. A
    value that is not unambiguously German-locale therefore raises, which is the
    same refusal `errors="raise"` already gave for non-numeric text.
    """
    cleaned = series.astype(str).str.strip()
    missing = cleaned.isin(["", "-"])
    malformed = cleaned[~missing & ~cleaned.str.match(_GERMAN_NUMBER_RE)]
    if not malformed.empty:
        sample = ", ".join(repr(v) for v in malformed.unique()[:5])
        raise ValueError(
            f"{len(malformed)} value(s) are not German-locale numbers ('.' groups "
            f"thousands, ',' is the decimal mark): {sample}. Refusing to guess -- "
            "stripping a '.' that is a decimal point would be a 1000x error."
        )
    parsed = pd.to_numeric(
        cleaned.mask(missing).str.replace(".", "", regex=False).str.replace(",", ".", regex=False),
        errors="raise",
    )
    return parsed.astype(float)


def _raw_files(root: Path, source: str) -> list[Path]:
    """Every raw file for `source`, sorted by name. Shared by every
    canonicalise() path: reading only the first file was a real bug here
    (see the multi-file tests) -- centralising this makes "read every file,
    not just one" the only way to get raw files at all."""
    raw_dir = Path(root) / "raw" / source
    raw_files = sorted(p for p in raw_dir.glob("*") if p.is_file())
    if not raw_files:
        raise FileNotFoundError(
            f"no raw file under {raw_dir}; run fetch({source!r}, start=..., end=...) first"
        )
    return raw_files


def _parse_installations(raw: bytes) -> pd.DataFrame:
    """Pure function: raw registry bytes -> one row per charging installation.

    The export leads with a handful of free-text notice lines before the
    real header row (identified by its first cell, `_HEADER_MARKER`), uses
    `;` separators and German decimal commas, and lists one row per physical
    charging installation ("Ladeeinrichtung"). Several installations can
    share one physical site (same operator at the same coordinates); those
    are aggregated into a single `sites` row: power and point counts summed,
    DC availability OR'd, commissioning date taken as the earliest.
    """
    text = _decode_raw(raw)
    # Split on the literal row terminator only. str.splitlines() also
    # breaks on other Unicode line-boundary code points (e.g. NEL), and
    # the registry's quoted "Public Key" cells contain one of those --
    # splitlines() would fragment that field and (after any row
    # reordering) corrupt row structure.
    # CRLF is what the registry ships, but requiring it made a re-saved or
    # LF-normalised export fail with a confusing "could not find header row".
    # Pick the terminator the file actually uses; keep the literal split
    # either way, since that is what protects the embedded line-boundary
    # characters that defeated splitlines().
    terminator = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(terminator)
    try:
        header_idx = next(i for i, line in enumerate(lines) if line.split(";", 1)[0] == _HEADER_MARKER)
    except StopIteration as exc:
        raise ValueError(
            f"could not find header row (first cell {_HEADER_MARKER!r}) in raw charge_points file"
        ) from exc

    body = terminator.join(lines[header_idx:])
    raw_df = pd.read_csv(io.StringIO(body), sep=";", dtype=str, keep_default_na=False)

    operator = raw_df["Betreiber"].str.strip()
    lat = _decimal_comma_to_float(raw_df["Breitengrad"])
    lon = _decimal_comma_to_float(raw_df["Längengrad"])
    postcode = raw_df["Postleitzahl"].str.strip()
    state = raw_df["Bundesland"].str.strip()
    rated_power_kw = _decimal_comma_to_float(raw_df["Nennleistung Ladeeinrichtung [kW]"])
    n_points = pd.to_numeric(raw_df["Anzahl Ladepunkte"].str.strip(), errors="raise").astype(int)
    is_dc = raw_df["Art der Ladeeinrichtung"].str.strip() == "Schnellladeeinrichtung"
    commissioned = pd.to_datetime(raw_df["Inbetriebnahmedatum"].str.strip(), format="%d.%m.%Y")

    # site_id per contracts/src/data.md: <state>-<postcode>-<sha1(operator+lat+lon)[:8]>.
    # Hashing the already-parsed operator/lat/lon means the id depends only on
    # those three values, not on incidental row order or duplicate-field text
    # elsewhere in the row — required for both stability guarantees below.
    hash_input = operator + lat.astype(str) + lon.astype(str)
    site_hash = hash_input.map(lambda s: hashlib.sha1(s.encode("utf-8")).hexdigest()[:8])
    site_id = state + "-" + postcode + "-" + site_hash

    installations = pd.DataFrame(
        {
            "site_id": site_id,
            "operator": operator,
            "lat": lat,
            "lon": lon,
            "postcode": postcode,
            "state": state,
            "rated_power_kw": rated_power_kw,
            "n_points": n_points,
            "is_dc": is_dc,
            "commissioned": commissioned,
        }
    )

    return installations


def _aggregate_sites(installations: pd.DataFrame) -> pd.DataFrame:
    """Installations -> the `sites` canonical frame.

    Split out from parsing so that a source delivered as several raw files
    aggregates across all of them: co-located installations that happen to
    land in different files must still collapse into one site. Aggregating
    per file and concatenating the results would leave them as duplicates.
    """
    sites = (
        installations.groupby("site_id", sort=True)
        .agg(
            operator=("operator", "first"),
            lat=("lat", "first"),
            lon=("lon", "first"),
            postcode=("postcode", "first"),
            state=("state", "first"),
            rated_power_kw=("rated_power_kw", "sum"),
            n_points=("n_points", "sum"),
            is_dc=("is_dc", "any"),
            commissioned=("commissioned", "min"),
        )
        .reset_index()
    )
    sites = sites[["site_id", *_SITE_COLUMNS]].sort_values("site_id").reset_index(drop=True)
    return sites


def _canonicalise_charge_points(*, root: Path) -> Path:
    source = "charge_points"
    raw_files = _raw_files(root, source)
    # Every file, not just raw_files[0]. The docstring's `*` is the contract,
    # and reading one file silently produced a short table with no error --
    # masked here only because the registry currently ships as one file.
    # #8 (SMARD/DWD) is date-partitioned, where many files is the normal case.
    blobs = [p.read_bytes() for p in raw_files]
    installations = pd.concat(
        [_parse_installations(b) for b in blobs], ignore_index=True
    )
    sites = _aggregate_sites(installations)
    require_columns(sites, ["site_id", *_SITE_COLUMNS])

    canonical_dir = Path(root) / "canonical"
    canonical_dir.mkdir(parents=True, exist_ok=True)
    table = SOURCES[source].canonical_table
    out_path = canonical_dir / f"{table}.parquet"
    sites.to_parquet(out_path, index=False)

    # Newest input wins, and the digest covers every file in sorted-name order
    # so provenance describes what was actually read rather than one of N.
    retrieved_at = datetime.fromtimestamp(
        max(p.stat().st_mtime for p in raw_files), tz=timezone.utc
    ).isoformat()
    meta_doc = {
        "source_url": SOURCES[source].url,
        "license": SOURCES[source].license,
        "retrieved_at": retrieved_at,
        "rows": int(len(sites)),
        "resolution_min": SOURCES[source].resolution_min,
        "sha256": (
            hashlib.sha256(blobs[0]).hexdigest()
            if len(blobs) == 1
            else hashlib.sha256(b"".join(blobs)).hexdigest()
        ),
    }
    meta_path = canonical_dir / f"{table}.meta.json"
    meta_path.write_text(json.dumps(meta_doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return out_path


# --- Europe/Berlin localisation (the real content of issue #8) -------------


def _localize_berlin_naive_local(naive: pd.Series) -> tuple[pd.Series, int]:
    """Naive Europe/Berlin wall-clock Timestamps -> tz-aware UTC Timestamps.

    Handles both DST transitions correctly, using only the stdlib (PEP 495
    `fold`), and *by construction* rather than by hoping the fixture never
    exercises the edge:

    - Fall-back ambiguous local times (Europe/Berlin 02:00-02:59 occurs twice
      in late October): rows are assumed to arrive in real chronological
      order, as every source in this module actually delivers them. The
      first time a given naive wall-clock value is seen it is resolved as
      still-DST (`fold=0`, CEST/UTC+2); the second time, standard time
      (`fold=1`, CET/UTC+1). This is what makes two rows both labelled
      "02:00" round-trip to two distinct, correctly-ordered UTC instants
      instead of colliding into a duplicate.
    - Spring-forward nonexistent local times (Europe/Berlin 02:00-02:59 never
      occurs in late March): `zoneinfo` does not raise for these -- per PEP
      495 it silently extrapolates an offset either side of the gap. That
      would fabricate a plausible-looking but meaningless UTC instant, so
      each candidate is round-tripped back to Berlin wall-clock and dropped
      (returned as NaT, counted) if it doesn't reproduce the original
      reading. A well-formed source never emits these rows in the first
      place (there is no such wall-clock instant to report); this is the
      belt-and-braces guard for a malformed one.

    Returns (utc_series, n_dropped_nonexistent).
    """
    seen: dict[datetime, int] = {}
    utc_values: list[pd.Timestamp] = []
    n_dropped = 0
    for ts in naive:
        py_dt = ts.to_pydatetime()
        occurrence = seen.get(py_dt, 0)
        seen[py_dt] = occurrence + 1
        fold = 1 if occurrence > 0 else 0
        localized = py_dt.replace(tzinfo=_BERLIN, fold=fold)
        utc_dt = localized.astimezone(timezone.utc)
        round_trip = utc_dt.astimezone(_BERLIN).replace(tzinfo=None)
        if round_trip != py_dt:
            utc_values.append(pd.NaT)
            n_dropped += 1
        else:
            utc_values.append(pd.Timestamp(utc_dt))
    return pd.Series(utc_values, index=naive.index), n_dropped


def _finalize_local_series(rows: pd.DataFrame, *, value_cols: list[str]) -> tuple[pd.DataFrame, int]:
    """`rows` (a `t_local_naive` column plus `value_cols`) -> the canonical
    `t` (UTC) shape: localised, sorted, and asserted strictly increasing with
    no duplicates. Raises rather than silently keeping a duplicate -- two
    rows colliding onto the same UTC instant after conversion means the
    ambiguous-time handling above was fooled, not that the data is fine."""
    utc, n_dropped = _localize_berlin_naive_local(rows["t_local_naive"])
    out = rows.assign(t=utc).drop(columns=["t_local_naive"])
    out = out.dropna(subset=["t"])
    out = out.sort_values("t").reset_index(drop=True)
    if not out["t"].is_unique:
        dupes = sorted(out.loc[out["t"].duplicated(keep=False), "t"].unique())
        raise ValueError(f"duplicate timestamps after Berlin->UTC conversion: {dupes}")
    return out[["t", *value_cols]], n_dropped


def _resample_15min(
    df: pd.DataFrame,
    value_cols: list[str],
    *,
    native_resolution_min: int,
    group_cols: tuple[str, ...] = (),
) -> tuple[pd.DataFrame, str]:
    """`df` (already at `native_resolution_min`) -> the canonical 15-minute
    grid. Only ever resamples DOWN in interval length (i.e. UP in row count)
    from something coarser than 15 minutes, by forward-fill -- never the
    other way. Returns (df, method) where `method` is recorded verbatim in
    the table's `.meta.json` so a forward-filled number's provenance is
    visible, not just its value."""
    if native_resolution_min == 15:
        return df, "native_15min"
    if native_resolution_min < 15 or native_resolution_min % 15 != 0:
        raise NotImplementedError(
            f"resampling {native_resolution_min}-min data onto the 15-min grid "
            "is only implemented for exact coarser multiples of 15 (forward-"
            "fill upsample); no wired source needs anything finer-grained yet"
        )
    n_steps = native_resolution_min // 15

    def _expand(group: pd.DataFrame) -> pd.DataFrame:
        reps = group.loc[group.index.repeat(n_steps)].reset_index(drop=True)
        offsets_min = np.tile(np.arange(n_steps) * 15, len(group))
        reps["t"] = reps["t"] + pd.to_timedelta(offsets_min, unit="m")
        return reps

    if group_cols:
        # Plain iteration, not groupby(...).apply(): apply() over a groupby
        # can silently drop or reorder the grouping columns depending on
        # pandas version, which is exactly the kind of thing that should be
        # loud, not silently "fixed" by a version bump.
        out = pd.concat(
            [_expand(group) for _, group in df.groupby(list(group_cols), sort=False)],
            ignore_index=True,
        )
    else:
        out = _expand(df)
    out = out.sort_values([*group_cols, "t"]).reset_index(drop=True)
    method = f"forward_fill_from_{native_resolution_min}min"
    return out[[*group_cols, "t", *value_cols]], method


# --- SMARD-style raw parsing (grid_load, prices, carbon) --------------------


def _read_de_series_csv(raw: bytes) -> pd.DataFrame:
    """Read a UTF-8-BOM, semicolon-separated SMARD export."""
    text = _decode_raw(raw)
    lines = [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    body = "\n".join(lines)
    return pd.read_csv(io.StringIO(body), sep=";", dtype=str)


def _parse_smard_datetime_local(series: pd.Series) -> pd.Series:
    """SMARD's "Datum von"/"Datum bis" cells: `DD.MM.YYYY HH:MM`, naive --
    the wall-clock reading is Europe/Berlin local time, not UTC."""
    return pd.to_datetime(series.str.strip(), format="%d.%m.%Y %H:%M")


_SMARD_VALUE_COLUMN_RE = re.compile(
    r"^\s*(?P<name>.+?)\s*\[(?P<unit>MWh|€/MWh|EUR/MWh)\]\s*(?P<qualifier>.*?)\s*$"
)


def _smard_value_columns(df: pd.DataFrame) -> dict[str, tuple[str, str, str | None]]:
    columns: dict[str, tuple[str, str, str | None]] = {}
    for column in df.columns:
        match = _SMARD_VALUE_COLUMN_RE.match(column)
        if match:
            columns[column] = (
                match.group("name").strip(),
                match.group("unit"),
                match.group("qualifier").strip() or None,
            )
    return columns


def _native_resolution_minutes(t_start: pd.Series, t_end: pd.Series) -> int:
    durations = (t_end - t_start).dt.total_seconds() / 60.0
    unique = sorted(set(durations.tolist()))
    if len(unique) != 1 or unique[0] <= 0 or unique[0] != int(unique[0]):
        raise ValueError(f"raw file has inconsistent interval lengths: {unique}")
    return int(unique[0])


def _check_local_duplicates_allowing_dst(naive: pd.Series, label: str) -> None:
    duplicated = naive[naive.duplicated(keep=False)]
    for value in duplicated.unique():
        py_value = value.to_pydatetime()
        first = py_value.replace(tzinfo=_BERLIN, fold=0).utcoffset()
        second = py_value.replace(tzinfo=_BERLIN, fold=1).utcoffset()
        if first == second:
            raise ValueError(f"duplicate {label} values at the same timestamp")


def _parse_grid_load_raw(raw: bytes) -> pd.DataFrame:
    """One SMARD load file -> local interval starts and MWh columns."""
    df = _read_de_series_csv(raw)
    t_start = _parse_smard_datetime_local(df["Datum von"])
    t_end = _parse_smard_datetime_local(df["Datum bis"])
    value_columns = _smard_value_columns(df)
    by_name: dict[str, list[str]] = {}
    for column, (name, unit, _) in value_columns.items():
        if unit == "MWh":
            by_name.setdefault(name, []).append(column)
    output: dict[str, pd.Series] = {
        "t_local_naive": t_start,
        "t_bis_local_naive": t_end,
    }
    if "Netzlast" in by_name:
        output["load_mwh"] = _decimal_comma_to_float(df[by_name["Netzlast"][0]])
    wind_columns = [
        column
        for name in ("Wind Onshore", "Wind Offshore", "Wind")
        for column in by_name.get(name, [])
    ]
    if wind_columns:
        output["wind_mwh"] = sum(
            (_decimal_comma_to_float(df[column]) for column in wind_columns),
            start=pd.Series(0.0, index=df.index),
        )
    if "Photovoltaik" in by_name:
        output["solar_mwh"] = _decimal_comma_to_float(df[by_name["Photovoltaik"][0]])
    ignored = sorted(
        set(by_name) - {"Netzlast", "Wind Onshore", "Wind Offshore", "Wind", "Photovoltaik"}
    )
    result = pd.DataFrame(output)
    result.attrs["native_resolution_min"] = _native_resolution_minutes(t_start, t_end)
    result.attrs["ignored_columns"] = ignored
    return result


def _parse_prices_raw(raw: bytes) -> pd.DataFrame:
    """One epex_day_ahead raw file -> rows with a naive local `t` plus
    price_eur_mwh. EUR/MWh is already a rate, not an energy total, so unlike
    grid_load there is no MWh->MW conversion -- the sign and magnitude read
    off the file untouched, including negative day-ahead prices."""
    df = _read_de_series_csv(raw)
    t_start = _parse_smard_datetime_local(df["Datum von"])
    t_end = _parse_smard_datetime_local(df["Datum bis"])
    value_columns = {
        column: details
        for column, details in _smard_value_columns(df).items()
        if details[1] in {"€/MWh", "EUR/MWh"}
    }
    if len(value_columns) != 1:
        raise ValueError(f"expected exactly one price column, got {list(value_columns)}")
    price_column = next(iter(value_columns))
    result = pd.DataFrame(
        {
            "t_local_naive": t_start,
            "t_bis_local_naive": t_end,
            "price_eur_mwh": _decimal_comma_to_float(df[price_column]),
        }
    )
    result.attrs["native_resolution_min"] = _native_resolution_minutes(t_start, t_end)
    return result


# Lifecycle-ish, order-of-magnitude CO2 intensity by generation category,
# g/kWh -- a named assumption, not a measured quantity (CONVENTIONS.md's rule
# on named constants vs. invented statistics). Issue #50 finding 1: the
# previous six-fuel list (lignite/hard coal/gas/nuclear/wind/solar) excluded
# biomass (~9% of German generation) and hydro (~4%) from BOTH the numerator
# and the denominator, which reads more fossil-heavy than reality and
# overstates intensity_g_kwh -- which flows straight into co2_kg_saved, a
# headline pitch figure. Fixed by carrying every category SMARD's real
# generation-by-fuel-type export names for which a widely-cited factor
# exists, biomass and hydro included at their real (non-zero) figures.
#
# Citations:
# - Braunkohle (lignite): German-fleet-specific direct-combustion figure,
#   Umweltbundesamt / Fraunhofer ISE generation-mix reporting (order of
#   magnitude ~1080 g/kWh; lignite runs materially higher than IPCC's
#   generic "coal" median because of the German fleet's older plants and
#   lower calorific fuel).
# - Steinkohle, Erdgas, Kernenergie, Wind Onshore, Wind Offshore,
#   Photovoltaik, Biomasse, Wasserkraft: IPCC AR5 WG3 (2014), Annex III,
#   Table A.III.2 "Emissions of Selected Electricity Supply Technologies"
#   -- median lifecycle gCO2eq/kWh figures (coal 820 generic used for hard
#   coal, gas 490, nuclear 12, wind onshore 11, wind offshore 12, solar PV
#   ~41-48 -- 45 used here, hydropower 24, biomass 230). Biomass is
#   deliberately NOT treated as zero-carbon: combustion of the harvested
#   fuel is a real, cited emission, not merely upstream/lifecycle overhead.
CARBON_INTENSITY_FACTORS_G_KWH: dict[str, float] = {
    "Braunkohle": 1080.0,  # lignite -- UBA/Fraunhofer ISE, German-fleet-specific
    "Steinkohle": 820.0,  # hard coal -- IPCC AR5 WG3 Annex III Table A.III.2 median
    "Erdgas": 490.0,  # natural gas -- IPCC AR5 WG3 Annex III Table A.III.2 median
    "Kernenergie": 12.0,  # nuclear -- IPCC AR5 WG3 Annex III Table A.III.2 median
    "Wind Onshore": 11.0,  # IPCC AR5 WG3 Annex III Table A.III.2 median
    "Wind Offshore": 12.0,  # IPCC AR5 WG3 Annex III Table A.III.2 median (higher: foundations/marine logistics)
    "Photovoltaik": 45.0,  # solar -- IPCC AR5 WG3 Annex III Table A.III.2, range ~41-48, midpoint used
    "Biomasse": 230.0,  # biomass -- IPCC AR5 WG3 Annex III Table A.III.2 median; NOT zero-carbon
    "Wasserkraft": 24.0,  # hydropower -- IPCC AR5 WG3 Annex III Table A.III.2 median
}

# Generation categories a real SMARD export can carry that have NO single
# widely-cited emission factor -- named here only as documentation of what
# we expect to see, NOT as an allow-list the parser depends on (see below):
# Pumpspeicher (pumped-storage hydro) re-emits whatever generation charged
# the reservoir, so it has no factor of its own without a separate
# charge-mix model; the two "Sonstige" buckets are undifferentiated mixes
# (geothermal/biogas, or oil/waste-incineration/other fossil) with no single
# citable number.
_EXPECTED_UNKNOWN_FACTOR_CATEGORIES: tuple[str, ...] = (
    "Pumpspeicher",
    "Sonstige Erneuerbare",
    "Sonstige Konventionelle",
)

_MWH_SUFFIX = " [MWh]"

# Issue #59: a REAL SMARD download-centre export may qualify a unit column
# with the resolution it was computed at -- `Braunkohle [MWh] Berechnete
# Auflösungen` (`Originalauflösungen` also occurs). The previous exact
# `" [MWh]"` suffix match classified such a column as NEITHER known nor
# excluded, so the fuel silently vanished from the mix: no raise, no
# warning, and -- worst -- no change to
# `excluded_generation_mwh_share_mean`, so the audit trail affirmed that
# nothing was dropped in exactly the run where something was. Measured on
# the baseline fixture with Braunkohle (1080 g/kWh) qualified:
# intensity_g_kwh 320.81 -> 182.77, a 43% understatement, silently.
#
# Every `<fuel> [MWh]` column is therefore matched with an OPTIONAL trailing
# resolution qualifier. The qualifier is stripped, the fuel classified
# normally, and the stripping recorded in carbon.meta.json (a count and the
# distinct qualifiers seen) -- observable as a number, not a log line, per
# CONVENTIONS.md.
_MWH_COLUMN_RE = re.compile(r"^\s*(?P<fuel>.+?)\s*\[MWh\]\s*(?P<qualifier>\S.*?)?\s*$")


def _parse_generation_mix_raw(
    raw: bytes,
) -> tuple[pd.DataFrame, list[str], dict[str, str], list[str]]:
    """One generation_mix raw file (generation by fuel type, MWh) -> (rows,
    excluded_fuel_names).

    `rows` has a naive local `t`, intensity_g_kwh (the generation-weighted
    average of CARBON_INTENSITY_FACTORS_G_KWH over only the categories with
    a known factor), plus two private helper columns (`_known_gen_mwh`,
    `_excluded_gen_mwh`) that `_canonicalise_generation_mix` uses to compute
    and record how much generation had no known factor.

    Per CONVENTIONS.md ("any coercion is observable"), a generation category
    with no known emission factor is never silently invisible: EVERY
    `"<category> [MWh]"` column actually present in the raw file is
    classified, generically, as either "known" (its name is a
    CARBON_INTENSITY_FACTORS_G_KWH key) or "excluded" (anything else --
    covering `_EXPECTED_UNKNOWN_FACTOR_CATEGORIES` above, but also any
    other/renamed/unanticipated category a real export might carry, e.g. a
    legacy un-split "Wind" column now that Onshore/Offshore are the known
    keys). Excluded generation is dropped from BOTH the numerator and the
    denominator of intensity_g_kwh -- counting it in the denominator alone
    would silently treat it as zero-carbon, which is just as dishonest as
    ignoring it -- and the exclusion is made visible two ways: a runtime
    warning here whenever a file actually carries nonzero excluded
    generation, and the `excluded_generation_*` fields
    `_canonicalise_generation_mix` writes to carbon.meta.json.

    A weighted average of rates is scale-invariant in the underlying energy
    unit, so no MWh->MW conversion is needed here (unlike grid_load)."""
    df = _read_de_series_csv(raw)
    t_start = _parse_smard_datetime_local(df["Datum von"])
    t_end = _parse_smard_datetime_local(df["Datum bis"])

    # column -> (fuel, resolution qualifier or None). Every MWh column lands
    # here, qualified or not, so none can fall between the buckets (#59).
    column_fuel: dict[str, tuple[str, str | None]] = {}
    for column in df.columns:
        match = _MWH_COLUMN_RE.match(column)
        if match is not None:
            column_fuel[column] = (match.group("fuel"), match.group("qualifier"))

    # A real export can offer the SAME fuel at two resolutions (e.g. both
    # `Originalauflösungen` and `Berechnete Auflösungen`). Summing both would
    # double-count that fuel's generation, so refuse by name rather than
    # silently pick one -- the failure mode this whole issue is about.
    columns_by_fuel: dict[str, list[str]] = {}
    for column, (fuel, _) in column_fuel.items():
        columns_by_fuel.setdefault(fuel, []).append(column)
    collisions = {f: cols for f, cols in columns_by_fuel.items() if len(cols) > 1}
    if collisions:
        raise ValueError(
            "generation_mix raw file carries the same fuel at more than one "
            f"resolution: {collisions}. Summing them would double-count that "
            "fuel; re-export with a single resolution per fuel."
        )

    known_cols = [c for c, (fuel, _) in column_fuel.items() if fuel in CARBON_INTENSITY_FACTORS_G_KWH]
    excluded_cols = [c for c in column_fuel if c not in known_cols]

    if not known_cols:
        raise ValueError(
            "generation_mix raw file has none of the known-factor generation "
            f"columns {list(CARBON_INTENSITY_FACTORS_G_KWH)}; got columns {list(df.columns)}"
        )

    known_fuels = [column_fuel[c][0] for c in known_cols]
    known_values = {
        column_fuel[col][0]: _decimal_comma_to_float(df[col]) for col in known_cols
    }
    unreported = sorted(name for name, values in known_values.items() if values.isna().all())
    partial_missing = pd.DataFrame(
        {name: values for name, values in known_values.items() if name not in unreported}
    ).isna()
    rows_dropped_missing = partial_missing.any(axis=1)
    gen_known = pd.DataFrame(
        {name: values.fillna(0.0) for name, values in known_values.items()}
    )
    gen_known.loc[rows_dropped_missing, :] = np.nan
    factors = pd.Series(CARBON_INTENSITY_FACTORS_G_KWH)[known_fuels]
    total_known = gen_known.sum(axis=1)
    weighted = (gen_known * factors).sum(axis=1)
    intensity = weighted / total_known

    excluded_fuels = [column_fuel[c][0] for c in excluded_cols]
    excluded_missing_values = 0
    if excluded_cols:
        gen_excluded = pd.DataFrame(
            {column_fuel[col][0]: _decimal_comma_to_float(df[col]) for col in excluded_cols}
        )
        excluded_missing_values = int(gen_excluded.isna().sum().sum())
        total_excluded = gen_excluded.fillna(0.0).sum(axis=1)
    else:
        total_excluded = pd.Series(0.0, index=df.index)

    qualified_fuels = {fuel: q for (fuel, q) in column_fuel.values() if q}

    if (total_excluded > 0).any():
        warnings.warn(
            "generation_mix raw file carries generation with no known emission "
            f"factor in {excluded_fuels} (issue #50); excluded from both the "
            "numerator and denominator of intensity_g_kwh -- see "
            "carbon.meta.json's excluded_generation_mwh_share_mean.",
            stacklevel=2,
        )

    rows = pd.DataFrame(
        {
            "t_local_naive": t_start,
            "t_bis_local_naive": t_end,
            "intensity_g_kwh": intensity,
            "_known_gen_mwh": total_known,
            "_excluded_gen_mwh": total_excluded,
        }
    )
    rows = rows.loc[~rows_dropped_missing].reset_index(drop=True)
    rows.attrs["native_resolution_min"] = _native_resolution_minutes(t_start, t_end)
    rows.attrs["n_rows_dropped_missing_values"] = int(rows_dropped_missing.sum())
    rows.attrs["n_excluded_missing_values"] = excluded_missing_values
    rows.attrs["fuels_unreported_entire_file"] = unreported
    return rows, excluded_fuels, qualified_fuels, unreported


# --- DWD raw parsing (weather) ----------------------------------------------


def _dwd_solar_to_utc(df: pd.DataFrame) -> pd.DataFrame:
    """Convert DWD ST observations to UTC W/m².

    Per DWD's product description MESS_DATUM is MEZ, end of interval. The
    interval start is therefore the floored MESS_DATUM minus one hour for
    hour-ending data and one further hour to convert fixed MEZ to UTC.
    """
    station_id = df["STATIONS_ID"].str.strip().str.zfill(5)
    source_time = pd.to_datetime(df["MESS_DATUM"].str.strip(), format="%Y%m%d%H:%M")
    if pd.DataFrame({"station_id": station_id, "source_time": source_time}).duplicated().any():
        raise ValueError("duplicate solar values for (station_id, MESS_DATUM)")
    t = source_time
    t_utc = (t - pd.Timedelta(hours=2)).dt.floor("h").dt.tz_localize("UTC")
    value = pd.to_numeric(df["FG_LBERG"].str.strip(), errors="coerce").replace(-999, np.nan)
    result = pd.DataFrame(
        {
            "station_id": station_id,
            "t": t_utc,
            "source_time": source_time,
            "ghi_w_m2": value * 10000 / 3600,
        }
    ).sort_values("source_time")
    return result.drop_duplicates(["station_id", "t"], keep="last").drop(columns="source_time")


def _read_dwd_product(raw: bytes) -> pd.DataFrame:
    if raw[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members = [
                name
                for name in archive.namelist()
                if name.lower().startswith("produkt_")
                and name.lower().endswith((".txt", ".csv"))
            ]
            if not members:
                raise ValueError("DWD ZIP contains no product text member")
            raw = archive.read(members[0])
    text = _decode_raw(raw)
    lines = [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    return pd.read_csv(io.StringIO("\n".join(lines)), sep=";", dtype=str)


def _parse_dwd_weather_raw(raw: bytes) -> pd.DataFrame:
    """Parse a DWD TU, FF, ST product or the legacy synthetic fixture."""
    df = _read_dwd_product(raw)
    df.columns = [column.strip() for column in df.columns]
    if "datetime_utc" in df.columns:
        t = pd.to_datetime(df["datetime_utc"].str.strip(), format="%Y%m%d%H", utc=True)
        return pd.DataFrame(
            {
                "station_id": df["station_id"].str.strip().str.zfill(5),
                "t": t,
                "temp_c": pd.to_numeric(df["temp_c"], errors="coerce"),
                "wind_ms": pd.to_numeric(df["wind_ms"], errors="coerce"),
                "ghi_w_m2": pd.to_numeric(df["ghi_w_m2"], errors="coerce"),
            }
        )
    station_id = df["STATIONS_ID"].str.strip().str.zfill(5)
    if "TT_TU" in df.columns:
        t = pd.to_datetime(df["MESS_DATUM"].str.strip(), format="%Y%m%d%H", utc=True)
        value = pd.to_numeric(df["TT_TU"].str.strip(), errors="coerce").replace(-999, np.nan)
        return pd.DataFrame({"station_id": station_id, "t": t, "temp_c": value})
    if "F" in df.columns and "D" in df.columns:
        t = pd.to_datetime(df["MESS_DATUM"].str.strip(), format="%Y%m%d%H", utc=True)
        value = pd.to_numeric(df["F"].str.strip(), errors="coerce").replace(-999, np.nan)
        return pd.DataFrame({"station_id": station_id, "t": t, "wind_ms": value})
    if "FG_LBERG" in df.columns:
        return _dwd_solar_to_utc(df)
    raise ValueError(f"unrecognised DWD header: {list(df.columns)}")


def _write_canonical(
    df: pd.DataFrame,
    *,
    root: Path,
    table: str,
    source: Source,
    raw_files: list[Path],
    extra_meta: dict,
) -> Path:
    """Shared parquet + .meta.json writer for every non-charge_points source.
    `retrieved_at` is the newest raw file's mtime, matching the charge_points
    path, so re-running canonicalise against unchanged raw files is a no-op
    on provenance too."""
    canonical_dir = Path(root) / "canonical"
    canonical_dir.mkdir(parents=True, exist_ok=True)
    out_path = canonical_dir / f"{table}.parquet"
    df.to_parquet(out_path, index=False)

    retrieved_at = datetime.fromtimestamp(
        max(p.stat().st_mtime for p in raw_files), tz=timezone.utc
    ).isoformat()
    meta_doc = {
        "source_url": source.url,
        "license": source.license,
        "retrieved_at": retrieved_at,
        "rows": int(len(df)),
        "resolution_min": 15,  # every canonical table lives on the 15-min grid
        **extra_meta,
    }
    meta_path = canonical_dir / f"{table}.meta.json"
    meta_path.write_text(json.dumps(meta_doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return out_path


def _canonicalise_smard_load(*, root: Path) -> Path:
    raw_files = _raw_files(root, "smard_load")
    parsed = [_parse_grid_load_raw(p.read_bytes()) for p in raw_files]
    native_resolutions = sorted({int(frame.attrs["native_resolution_min"]) for frame in parsed})
    if len(native_resolutions) != 1:
        raise ValueError(f"SMARD load files have different native resolutions: {native_resolutions}")
    ignored_columns = sorted({column for frame in parsed for column in frame.attrs["ignored_columns"]})
    available = {column for frame in parsed for column in frame.columns}
    missing = sorted({"load_mwh", "wind_mwh", "solar_mwh"} - available)
    if missing:
        raise ValueError(f"SMARD load is missing required quantities: {missing}")
    combined: pd.DataFrame | None = None
    for quantity in ("load_mwh", "wind_mwh", "solar_mwh"):
        pieces = [
            frame[["t_local_naive", "t_bis_local_naive", quantity]].copy()
            for frame in parsed
            if quantity in frame
        ]
        quantity_rows = pd.concat(pieces, ignore_index=True)
        _check_local_duplicates_allowing_dst(quantity_rows["t_local_naive"], quantity)
        quantity_rows["_occurrence"] = quantity_rows.groupby(
            "t_local_naive", sort=False
        ).cumcount()
        if combined is None:
            combined = quantity_rows
        else:
            combined = combined.merge(
                quantity_rows.drop(columns=["t_bis_local_naive"]),
                on=["t_local_naive", "_occurrence"],
                how="outer",
            )
    assert combined is not None
    interval_hours = (
        combined["t_bis_local_naive"] - combined["t_local_naive"]
    ).dt.total_seconds() / 3600.0
    rows = pd.DataFrame(
        {
            "t_local_naive": combined["t_local_naive"],
            "load_mw": combined["load_mwh"] / interval_hours,
            "wind_mw": combined["wind_mwh"] / interval_hours,
            "solar_mw": combined["solar_mwh"] / interval_hours,
        }
    )
    n_missing = int(rows[["load_mw", "wind_mw", "solar_mw"]].isna().any(axis=1).sum())
    rows = rows.dropna(subset=["load_mw", "wind_mw", "solar_mw"]).copy()
    rows = rows.drop(columns=["_occurrence"], errors="ignore")
    rows["residual_mw"] = rows["load_mw"] - rows["wind_mw"] - rows["solar_mw"]
    local, n_dropped = _finalize_local_series(
        rows, value_cols=["load_mw", "wind_mw", "solar_mw", "residual_mw"]
    )
    df, method = _resample_15min(
        local,
        ["load_mw", "wind_mw", "solar_mw", "residual_mw"],
        native_resolution_min=native_resolutions[0],
    )
    require_columns(df, ["t", "load_mw", "wind_mw", "solar_mw", "residual_mw"])
    return _write_canonical(
        df,
        root=root,
        table="grid_load",
        source=SOURCES["smard_load"],
        raw_files=raw_files,
        extra_meta={
            "resample_method": method,
            "native_resolution_min": native_resolutions[0],
            "n_nonexistent_local_times_dropped": n_dropped,
            "n_rows_dropped_missing_values": n_missing,
            "ignored_columns": ignored_columns,
        },
    )


def _canonicalise_epex_day_ahead(*, root: Path) -> Path:
    raw_files = _raw_files(root, "epex_day_ahead")
    parsed = [_parse_prices_raw(p.read_bytes()) for p in raw_files]
    native_resolutions = sorted({int(frame.attrs["native_resolution_min"]) for frame in parsed})
    if len(native_resolutions) != 1:
        raise ValueError(f"price files have different native resolutions: {native_resolutions}")
    rows = pd.concat(parsed, ignore_index=True)
    _check_local_duplicates_allowing_dst(rows["t_local_naive"], "price")
    n_missing = int(rows["price_eur_mwh"].isna().sum())
    rows = rows.dropna(subset=["price_eur_mwh"]).copy()
    rows = rows.drop(columns=["t_bis_local_naive"])
    local, n_dropped = _finalize_local_series(rows, value_cols=["price_eur_mwh"])
    quarter, method = _resample_15min(
        local, ["price_eur_mwh"], native_resolution_min=native_resolutions[0]
    )
    require_columns(quarter, ["t", "price_eur_mwh"])
    return _write_canonical(
        quarter,
        root=root,
        table="prices",
        source=SOURCES["epex_day_ahead"],
        raw_files=raw_files,
        extra_meta={
            "resample_method": method,
            "native_resolution_min": native_resolutions[0],
            "n_nonexistent_local_times_dropped": n_dropped,
            "n_rows_dropped_missing_values": n_missing,
        },
    )


def _canonicalise_generation_mix(*, root: Path) -> Path:
    raw_files = _raw_files(root, "generation_mix")
    parsed = [_parse_generation_mix_raw(p.read_bytes()) for p in raw_files]
    rows = pd.concat([r for r, _, _, _ in parsed], ignore_index=True)
    native_resolutions = sorted({int(r.attrs["native_resolution_min"]) for r, _, _, _ in parsed})
    if len(native_resolutions) != 1:
        raise ValueError(f"generation files have different native resolutions: {native_resolutions}")
    # Union across every raw file, not just the static "expected" list: a
    # file can carry an excluded category we didn't anticipate (see
    # _parse_generation_mix_raw's docstring), and this must say so.
    excluded_categories_seen = sorted({fuel for _, fuels, _, _ in parsed for fuel in fuels})
    # Issue #59: stripping a resolution qualifier off a column header is a
    # coercion, so it is recorded as a number and a name list rather than a
    # log line -- otherwise the only evidence it happened dies with the
    # process. Union across files, same reasoning as above.
    qualified_fuels_seen: dict[str, str] = {}
    fuels_unreported_entire_file = sorted(
        {fuel for _, _, _, fuels in parsed for fuel in fuels}
    )
    for _, _, quals, _ in parsed:
        qualified_fuels_seen.update(quals)
    helper_cols = ["intensity_g_kwh", "_known_gen_mwh", "_excluded_gen_mwh"]
    n_missing = sum(int(r.attrs["n_rows_dropped_missing_values"]) for r, _, _, _ in parsed)
    n_excluded_missing = sum(int(r.attrs["n_excluded_missing_values"]) for r, _, _, _ in parsed)
    rows = rows.drop(columns=["t_bis_local_naive"])
    local, n_dropped = _finalize_local_series(rows, value_cols=helper_cols)
    quarter, method = _resample_15min(
        local, helper_cols, native_resolution_min=native_resolutions[0]
    )

    # Issue #50 finding 1: excluded generation (no known emission factor,
    # e.g. Pumpspeicher / "Sonstige *") must not be silently dropped from
    # the denominator. It already isn't part of intensity_g_kwh's own
    # numerator/denominator (see _parse_generation_mix_raw), but the fact
    # and size of the exclusion is recorded here rather than only living in
    # an in-process warning, so it survives to whoever reads carbon.meta.json.
    total_gen = quarter["_known_gen_mwh"] + quarter["_excluded_gen_mwh"]
    excluded_share = (quarter["_excluded_gen_mwh"] / total_gen).where(total_gen > 0)
    excluded_share_mean = float(excluded_share.dropna().mean()) if excluded_share.notna().any() else 0.0
    n_rows_with_excluded_generation = int((quarter["_excluded_gen_mwh"] > 0).sum())

    quarter = quarter.drop(columns=["_known_gen_mwh", "_excluded_gen_mwh"])
    require_columns(quarter, ["t", "intensity_g_kwh"])
    return _write_canonical(
        quarter,
        root=root,
        table="carbon",
        source=SOURCES["generation_mix"],
        raw_files=raw_files,
        extra_meta={
            "resample_method": method,
            "native_resolution_min": native_resolutions[0],
            "n_nonexistent_local_times_dropped": n_dropped,
            "n_rows_dropped_missing_values": n_missing,
            "n_excluded_missing_values": n_excluded_missing,
            "fuels_unreported_entire_file": fuels_unreported_entire_file,
            "emission_factors_g_kwh": CARBON_INTENSITY_FACTORS_G_KWH,
            "excluded_generation_categories": excluded_categories_seen,
            "fuels_with_resolution_qualifier": sorted(qualified_fuels_seen),
            "n_generation_columns_with_resolution_qualifier": len(qualified_fuels_seen),
            "resolution_qualifiers_seen": sorted(set(qualified_fuels_seen.values())),
            "excluded_generation_mwh_share_mean": excluded_share_mean,
            "n_rows_with_excluded_generation_category": n_rows_with_excluded_generation,
        },
    )


def _canonicalise_dwd_weather(*, root: Path) -> Path:
    raw_files = _raw_files(root, "dwd_weather")
    parsed = [_parse_dwd_weather_raw(p.read_bytes()) for p in raw_files]
    variable_frames: dict[str, list[pd.DataFrame]] = {
        "temp_c": [],
        "wind_ms": [],
        "ghi_w_m2": [],
    }
    for frame in parsed:
        for variable in variable_frames:
            if variable in frame:
                variable_frames[variable].append(frame[["station_id", "t", variable]])
    window_frames = variable_frames["temp_c"] or variable_frames["wind_ms"]
    temperature_window_start = (
        min(frame["t"].min() for frame in window_frames) if window_frames else None
    )

    def merge_variables(*, drop_before_temperature_window: bool, check_duplicates: bool) -> pd.DataFrame | None:
        merged: pd.DataFrame | None = None
        for variable, pieces in variable_frames.items():
            if not pieces:
                continue
            frame = pd.concat(pieces, ignore_index=True)
            if drop_before_temperature_window and temperature_window_start is not None:
                frame = frame.loc[frame["t"] >= temperature_window_start].copy()
            if check_duplicates:
                duplicate = frame.duplicated(subset=["station_id", "t"], keep=False)
                if duplicate.any():
                    raise ValueError(f"duplicate ({variable}) values for (station_id, t)")
            merged = frame if merged is None else merged.merge(
                frame, on=["station_id", "t"], how="outer"
            )
        return merged

    merged_before_window = merge_variables(
        drop_before_temperature_window=False, check_duplicates=False
    )
    if merged_before_window is None:
        raise ValueError("no recognised DWD variables in raw weather data")
    n_rows_before_temperature_window_dropped = 0
    if temperature_window_start is not None:
        before_window = merged_before_window["t"] < temperature_window_start
        n_rows_before_temperature_window_dropped = int(before_window.sum())
    merged = merge_variables(
        drop_before_temperature_window=True, check_duplicates=True
    )
    assert merged is not None
    present_variables = [variable for variable in variable_frames if variable in merged]
    merged = merged.dropna(subset=present_variables, how="all")
    merged = merged.sort_values(["station_id", "t"]).reset_index(drop=True)
    for variable in variable_frames:
        if variable not in merged:
            merged[variable] = np.nan
    missing_counts = {
        f"n_missing_{variable}": int(merged[variable].isna().sum())
        for variable in variable_frames
    }
    quarter, method = _resample_15min(
        merged,
        ["temp_c", "wind_ms", "ghi_w_m2"],
        native_resolution_min=60,
        group_cols=("station_id",),
    )
    quarter = quarter.sort_values(["t", "station_id"]).reset_index(drop=True)
    require_columns(quarter, ["t", "station_id", "temp_c", "wind_ms", "ghi_w_m2"])
    return _write_canonical(
        quarter,
        root=root,
        table="weather",
        source=SOURCES["dwd_weather"],
        raw_files=raw_files,
        extra_meta={
            "resample_method": method,
            "native_resolution_min": 60,
            "stations_with_solar": sorted(
                quarter.loc[quarter["ghi_w_m2"].notna(), "station_id"].unique()
            ),
            "stations_without_solar": sorted(
                set(quarter["station_id"]) - set(
                    quarter.loc[quarter["ghi_w_m2"].notna(), "station_id"].unique()
                )
            ),
            "n_rows_before_temperature_window_dropped": n_rows_before_temperature_window_dropped,
            **missing_counts,
        },
    )


_CANONICALISE_DISPATCH = {
    "charge_points": _canonicalise_charge_points,
    "smard_load": _canonicalise_smard_load,
    "epex_day_ahead": _canonicalise_epex_day_ahead,
    "generation_mix": _canonicalise_generation_mix,
    "dwd_weather": _canonicalise_dwd_weather,
}


def canonicalise(source: str, *, root: Path = DATA_ROOT) -> Path:
    """Parse data/raw/<source>/* -> data/canonical/<table>.parquet + <table>.meta.json.

    Pure function of the raw files; safe to re-run. `retrieved_at` in the
    meta sidecar is the raw file's mtime (not wall-clock at call time) so
    re-running canonicalise against the same, unchanged raw file yields byte-
    identical output.
    """
    if source in ("balancing", "regelleistung"):
        raise NotImplementedError(
            "canonicalise('regelleistung') is deferred, per issue #8: "
            "regelleistung.net's balancing capacity auction results may "
            "require a registered account to access programmatically, which "
            "this lane will not work around (no scraping around a "
            "login/paywall, no committed credentials -- CONVENTIONS.md). "
            "Confirming whether an account is actually required, and if so "
            "getting one, needs an `agent:devin` issue before `balancing` "
            "can be wired; it is not faked here."
        )
    if source not in SOURCES or source not in _CANONICALISE_DISPATCH:
        raise NotImplementedError(
            f"canonicalise({source!r}) is not wired yet; only "
            f"{sorted(_CANONICALISE_DISPATCH)} are implemented (see contracts/src/data.md)"
        )
    return _CANONICALISE_DISPATCH[source](root=root)


def load(
    table: str,
    *,
    root: Path = DATA_ROOT,
    start: datetime | None = None,
    end: datetime | None = None,
    sites: Sequence[str] | None = None,
) -> pd.DataFrame:
    """The only way any other lane reads canonical data. Returns tz-aware UTC,
    sorted by (t, site_id) where applicable. Raises MissingTable(table,
    how_to_get_it) with the exact fetch command."""
    path = Path(root) / "canonical" / f"{table}.parquet"
    if not path.exists():
        raise MissingTable(table, _how_to_build(table))

    df = pd.read_parquet(path)
    if start is not None and "t" in df.columns:
        df = df[df["t"] >= start]
    if end is not None and "t" in df.columns:
        df = df[df["t"] <= end]
    if sites is not None and "site_id" in df.columns:
        df = df[df["site_id"].isin(sites)]

    sort_cols = [c for c in ("t", "site_id", "station_id") if c in df.columns]
    if sort_cols:
        df = df.sort_values(sort_cols).reset_index(drop=True)
    return df


def meta(table: str, *, root: Path = DATA_ROOT) -> dict:
    """Provenance sidecar for `table`: source url, licence, retrieved-at, row
    count, resolution and (for charge_points-derived tables) the sha256 of
    the raw file that was canonicalised."""
    path = Path(root) / "canonical" / f"{table}.meta.json"
    if not path.exists():
        raise MissingTable(table, _how_to_build(table))
    return json.loads(path.read_text(encoding="utf-8"))



# Small, hand-picked reference set of station locations spread across
# different regions/borders of Germany, so nearest_weather_station() has
# something real to pick among. Coordinates are approximate city locations
# (public knowledge), not literal DWD station numbers -- this lane has no
# real DWD download to source exact station coordinates from (see module
# docstring). Station ids match the `station_id` values a canonicalised
# `weather` table can carry, but this registry does not require that table
# to exist: nearest_weather_station() is a pure function of these constants.
_WEATHER_STATIONS: dict[str, tuple[float, float]] = {
    "00433": (52.4676, 13.4020),
    "03379": (48.1632, 11.5429),
    "01975": (53.6332, 9.9881),
    "02667": (50.8645, 7.1575),
    "04336": (49.2128, 7.1077),
    "01048": (51.1278, 13.7543),
}


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r_km = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r_km * math.asin(math.sqrt(a))


def nearest_weather_station(lat: float, lon: float) -> str:
    """The `_WEATHER_STATIONS` id closest to (lat, lon) by great-circle
    distance. A pure function of fixed constants and its arguments: same
    coordinates always return the same station id (`min` over a Python dict
    iterates in a fixed, insertion-preserved order, so ties -- there are none
    among these six -- would also resolve deterministically)."""
    return min(
        _WEATHER_STATIONS,
        key=lambda station_id: _haversine_km(lat, lon, *_WEATHER_STATIONS[station_id]),
    )
