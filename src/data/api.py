"""src.data — public API.

Ingest public German energy + charge-point data into canonical tables.

This module is the lane's ONLY cross-lane surface: other lanes import
`src.data.api` and nothing else. See `contracts/src/data.md` for the
interface this lane owes the rest of the project, and
`contracts/CONVENTIONS.md` for units, time handling and the data layout.

Only the `charge_points` source (Bundesnetzagentur Ladesaeulenregister) is
wired end to end, producing the `sites` canonical table. Every other source
named in the contract raises NotImplementedError from `fetch`/`canonicalise`
until a later issue wires it.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Sequence

import pandas as pd

LANE = "src/data"

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _default_data_root() -> Path:
    env = os.environ.get("FLEXGRID_DATA")
    return Path(env).expanduser() if env else _REPO_ROOT / "data"


DATA_ROOT: Path = _default_data_root()


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
}

# Human-readable "how to build this" pointer used only to compose MissingTable
# messages. Naming a source here does not mean it is wired: fetch()/
# canonicalise() still raise NotImplementedError for anything not in SOURCES.
# The mapping mirrors "Sources to wire" in contracts/src/data.md.
_TABLE_SOURCE: dict[str, str] = {
    "sites": "charge_points",
    "grid_load": "smard_load",
    "prices": "epex_day_ahead",
    "weather": "dwd_weather",
    "balancing": "regelleistung",
    "carbon": "entsoe_carbon",
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


def fetch(source: str, *, start: date, end: date, root: Path = DATA_ROOT) -> list[Path]:
    """Download to data/raw/<source>/. Idempotent: an existing complete file is not
    re-fetched. Never called from a test.

    `start`/`end` are part of the contract signature but unused for
    `charge_points`: the Ladesaeulenregister is a full point-in-time snapshot,
    not a date-partitioned series.
    """
    if source not in SOURCES:
        raise NotImplementedError(
            f"source {source!r} is not wired yet; only {sorted(SOURCES)} "
            "are implemented (see contracts/src/data.md)"
        )
    import requests  # imported lazily: fetch() is exercised by hand, never by pytest

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


def _parse_charge_points(raw: bytes) -> pd.DataFrame:
    """Pure function: raw registry bytes -> the `sites` canonical DataFrame.

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
    lines = text.split("\r\n")
    try:
        header_idx = next(i for i, line in enumerate(lines) if line.split(";", 1)[0] == _HEADER_MARKER)
    except StopIteration as exc:
        raise ValueError(
            f"could not find header row (first cell {_HEADER_MARKER!r}) in raw charge_points file"
        ) from exc

    body = "\r\n".join(lines[header_idx:])
    raw_df = pd.read_csv(io.StringIO(body), sep=";", dtype=str, keep_default_na=False)

    def _decimal_comma_to_float(series: pd.Series) -> pd.Series:
        return pd.to_numeric(series.str.strip().str.replace(",", ".", regex=False), errors="raise")

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


def canonicalise(source: str, *, root: Path = DATA_ROOT) -> Path:
    """Parse data/raw/<source>/* -> data/canonical/<table>.parquet + <table>.meta.json.

    Pure function of the raw files; safe to re-run. `retrieved_at` in the
    meta sidecar is the raw file's mtime (not wall-clock at call time) so
    re-running canonicalise against the same, unchanged raw file yields byte-
    identical output.
    """
    if source not in SOURCES or source != "charge_points":
        raise NotImplementedError(
            f"canonicalise({source!r}) is not wired yet; only 'charge_points' is implemented"
        )

    raw_dir = Path(root) / "raw" / source
    raw_files = sorted(p for p in raw_dir.glob("*") if p.is_file())
    if not raw_files:
        raise FileNotFoundError(
            f"no raw file under {raw_dir}; run fetch({source!r}, start=..., end=...) first"
        )
    raw_path = raw_files[0]
    raw_bytes = raw_path.read_bytes()

    sites = _parse_charge_points(raw_bytes)
    require_columns(sites, ["site_id", *_SITE_COLUMNS])

    canonical_dir = Path(root) / "canonical"
    canonical_dir.mkdir(parents=True, exist_ok=True)
    table = SOURCES[source].canonical_table
    out_path = canonical_dir / f"{table}.parquet"
    sites.to_parquet(out_path, index=False)

    retrieved_at = datetime.fromtimestamp(raw_path.stat().st_mtime, tz=timezone.utc).isoformat()
    meta_doc = {
        "source_url": SOURCES[source].url,
        "license": SOURCES[source].license,
        "retrieved_at": retrieved_at,
        "rows": int(len(sites)),
        "resolution_min": SOURCES[source].resolution_min,
        "sha256": hashlib.sha256(raw_bytes).hexdigest(),
    }
    meta_path = canonical_dir / f"{table}.meta.json"
    meta_path.write_text(json.dumps(meta_doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return out_path


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

    sort_cols = [c for c in ("t", "site_id") if c in df.columns]
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


def nearest_weather_station(lat: float, lon: float) -> str:
    """Part of the lane's contracted public surface, but depends on the DWD
    weather source, which is not wired in this issue."""
    raise NotImplementedError(
        "nearest_weather_station() depends on the DWD weather source (table "
        "'weather'), which is not wired yet — see contracts/src/data.md"
    )
