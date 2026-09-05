# src/data — contract

Owns every byte that enters the project from outside, and the canonical tables every other lane
reads. Nothing else in the repo may parse a raw download or hard-code a source URL.

Read `contracts/CONVENTIONS.md` first (units, time, `DATA_ROOT` layout, provenance rule).

## Public API — `src/data/api.py`

```python
DATA_ROOT: Path                      # env FLEXGRID_DATA, else <repo>/data
SOURCES: dict[str, Source]           # key -> Source(name, url, license, resolution_min, canonical_table)

def fetch(source: str, *, start: date, end: date, root: Path = DATA_ROOT) -> list[Path]
    """Download to data/raw/<source>/. Idempotent: an existing complete file is not re-fetched.
    Never called from a test."""

def canonicalise(source: str, *, root: Path = DATA_ROOT) -> Path
    """Parse data/raw/<source>/* -> data/canonical/<table>.parquet + <table>.meta.json. Pure
    function of the raw files; safe to re-run."""

def load(table: str, *, root: Path = DATA_ROOT, start: datetime | None = None,
         end: datetime | None = None, sites: Sequence[str] | None = None) -> pd.DataFrame
    """The only way any other lane reads canonical data. Returns tz-aware UTC, sorted by (t, site_id)
    where applicable. Raises MissingTable(table, how_to_get_it) with the exact fetch command."""

def meta(table: str, *, root: Path = DATA_ROOT) -> dict   # provenance sidecar
def require_columns(df: pd.DataFrame, cols: Sequence[str]) -> None   # boundary assert used repo-wide
def nearest_weather_station(lat: float, lon: float) -> str
```

## Canonical tables

| table | key | columns |
|---|---|---|
| `sites` | `site_id` | `operator, lat, lon, postcode, state, rated_power_kw, n_points, is_dc, commissioned` |
| `grid_load` | `t` | `load_mw, wind_mw, solar_mw, residual_mw` |
| `prices` | `t` | `price_eur_mwh` (day-ahead DE-LU) |
| `weather` | `t, station_id` | `temp_c, wind_ms, ghi_w_m2` |
| `balancing` | `t, product, direction` | `capacity_price_eur_mw_h, energy_price_eur_mwh` |
| `carbon` | `t` | `intensity_g_kwh` |

`site_id` is stable and deterministic: `<state>-<postcode>-<sha1(operator+lat+lon)[:8]>`. It must
not change when a download is repeated — every downstream artifact is keyed on it.

## Sources to wire (all public; confirm each URL and record it in the `.meta.json`)
Bundesnetzagentur Ladesäulenregister (charge points) · SMARD (load/generation) · ENTSO-E
Transparency (cross-border, balancing) · EPEX/day-ahead via SMARD · DWD Open Data (weather) ·
regelleistung.net (balancing capacity auctions).

An access barrier (API key, captcha, licence click-through) is an `agent:devin` issue, not a
workaround: no scraping around a paywall, no committed credentials.

## Guarantees other lanes depend on
- `load()` output never contains duplicate `(t, site_id)` rows and never a naive timestamp.
- A gap in a source is represented as a missing row, never as `0`.
- `MissingTable` is raised (not an empty frame) when a table has not been built.

## Explicitly not this lane's job
Simulating charging sessions (`src/fleet`), any modelling, any plotting.
