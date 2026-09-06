"""src.ui — public API.

Judge-facing map and dashboard, served by src.service, no build step.

This module is the lane's ONLY cross-lane surface: other lanes import
`src.ui.api` and nothing else. See `contracts/src/ui.md` for the
interface this lane owes the rest of the project, and
`contracts/CONVENTIONS.md` for units, time handling and the data layout.

What this lane actually is: five Jinja2 templates (`src/ui/templates/`) and their
hand-written ES modules (`src/ui/static/`), plus the small amount of Python needed to
render those templates from data shaped like the `src/service` HTTP responses in
`contracts/src/service.md`. `src/service` owns the FastAPI app and routing; it is
expected to do roughly:

    from src.ui import api as ui
    templates_dir = ui.TEMPLATES_DIR          # Jinja2Templates(directory=...)
    static_dir = ui.STATIC_DIR                # StaticFiles(directory=...), mounted at /static
    html = ui.render("ledger", context)       # context shaped like the JSON below

`render()` never fetches anything and never computes a displayed figure — it only
formats numbers that are already in `context` and renders the provenance that came
with them. If a number is not present, the template says so; it does not invent one.
Nothing here imports `src.service` (unmerged, written concurrently — see issue #28);
this lane is tested entirely against recorded fixture payloads shaped like the
contract, never against a running service.

Context shape per screen
------------------------
Every screen accepts the same two optional top-level keys:

  `error`    : {error, detail, how_to_fix} — the failure shape mandated by
               `contracts/src/service.md`. When present the screen renders the error
               and NOTHING else (see `screen_body()` in `_macros.html`).
  `warnings` : the `warnings[]` array off `ScenarioResult`. Rendered on every screen,
               never swallowed.

and then, per screen:

  map      `sites`      : [{site_id, lat, lon, firm_kw, revenue_eur, co2_kg, ...}]
                          (`/api/sites` + `/api/scenario/{id}/map`)
  day      `intervals`  : [{t, baseline_load_kw, optimised_load_kw, envelope_kw,
                            price_eur_mwh, firm_kw}]  (`/api/scenario/{id}/timeseries`)
  call     `dispatch`   : the `/api/scenario/{id}/dispatch` response
           `dispatch_url`   : the POST target for the dispatch button
           `reduction_event`: the `ReductionEvent` the button must POST --
                          {call_t, notice_min, duration_min, reduction_kw}, the field
                          names `src/sched/api.py::ReductionEvent` declares. This lane
                          does NOT invent it: `contracts/src/ui.md` forbids computing
                          anything, and choosing a reduction size or a notice period in
                          JavaScript would be exactly that. When the service does not
                          send one the button is disabled and says why, because an
                          empty POST body is a request the service must reject.
  pooling  `curve`      : [{n_sites, firm_kw_per_site, shortfall_rate}]
                          (`/api/scenario/{id}/pooling`, i.e. `diversification_curve` rows)
  ledger   `totals`     : `ScenarioResult.totals`
           `assumptions`: `/api/assumptions` (`src.market.ASSUMPTIONS`, verbatim)
           `products`   : `src.market.PRODUCTS` entries, when the service exposes them

NOTE ON KEY NAMES: `contracts/src/service.md` freezes the *routes* and the top level of
`ScenarioResult`, but names the per-interval and per-site payload fields only in prose
("per-interval `load_kw` baseline vs optimised"). The names above are this lane's
reading of that prose. A field this lane looks for and does not find renders visibly as
"not provided by API" — never as 0 — so a naming mismatch with `src/service` shows up
on screen as a missing figure rather than as a confident wrong one. That is deliberate:
fixing it is a one-line rename, and a silent zero would not be.
"""

from __future__ import annotations

import datetime as _dt
import math as _math
import re
from pathlib import Path
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape

LANE = "src/ui"

_LANE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = _LANE_DIR / "templates"
STATIC_DIR = _LANE_DIR / "static"

# Where `src/service` is expected to mount STATIC_DIR. Templates reference assets by
# this prefix and `static_assets_referenced()` finds them by it; if the service mounts
# somewhere else, this constant and the mount move together.
STATIC_URL_PREFIX = "/static"

# Display timezone per contracts/CONVENTIONS.md: the wire is always tz-aware UTC; a
# human never sees anything else.
BERLIN_TZ = ZoneInfo("Europe/Berlin")

# The five screens, in the order DEMO.md uses them (contracts/src/ui.md "Screens").
SCREENS: tuple[str, ...] = ("map", "day", "call", "pooling", "ledger")

SCREEN_TITLES: dict[str, str] = {
    "map": "Map — every charge point",
    "day": "One day — baseline vs optimised",
    "call": "The call — promised vs delivered",
    "pooling": "Pooling — safe promise per site",
    "ledger": "Ledger — every number and where it came from",
}

_TEMPLATE_BY_SCREEN: dict[str, str] = {
    "map": "map.html",
    "day": "day.html",
    "call": "call.html",
    "pooling": "pooling.html",
    "ledger": "ledger.html",
}

# contracts/CONVENTIONS.md "Units" table: column-suffix -> the unit a judge should
# read next to it. Used to label any field the frozen contract didn't already name a
# unit for -- an unrecognised suffix renders with NO unit rather than a guessed one.
UNIT_SUFFIXES: dict[str, str] = {
    "_eur_mwh": "EUR/MWh",
    "_eur_mw_h": "EUR/MW/h",
    "_g_kwh": "gCO₂/kWh",
    "_kwh": "kWh",
    "_kw": "kW",
    "_mw": "MW",
    "_eur": "EUR",
    "_kg": "kg",
    "_c": "°C",
    "_min": "min",
}

# The sentinel `Assumption.source` / `Product.source` value that marks a figure as
# invented rather than looked up (src/market/api.py). Matched as a whole word so a
# real citation that merely contains the letters is not relabelled as a guess.
_ASSUMED_RE = re.compile(r"\bassumed\b", re.IGNORECASE)

# A figure whose value must never be aggregated into a portfolio headline
# (contracts/CONVENTIONS.md, "Measure the physical quantity, not the derived one"):
# src/sched's reduction_kw_achieved is a per-(site, interval) worst-interval floor.
PER_INTERVAL_FLOOR_KEYS: frozenset[str] = frozenset({"reduction_kw_achieved"})
PER_INTERVAL_FLOOR_CAVEAT = (
    "per-(site, interval) worst-interval floor from src/sched — NOT a portfolio total"
)

MISSING_LABEL = "not provided by API"

# Display buckets for the map legend, in kW of `firm_kw`. These are a *rendering*
# device -- how the dots are drawn -- not a figure: no number a judge reads off this
# page is derived from them, and every site's own firm_kw is in the payload verbatim.
# Each bucket carries a shape as well as a colour, because contracts/src/ui.md forbids
# relying on colour alone.
MAP_LEGEND_BINS: tuple[tuple[float, float | None, str], ...] = (
    (0.0, 50.0, "small circle"),
    (50.0, 200.0, "medium circle"),
    (200.0, 500.0, "large circle"),
    (500.0, None, "large ring"),
)

# The fifth mark on the map, and the one the buckets above cannot express: a site whose
# `firm_kw` the API did not send. It is NOT a bucket -- it has no range -- and it must
# never be drawn as the 0-50 kW mark, because "this site can promise almost nothing" and
# "we do not know what this site can promise" are opposite facts to an operator.
# `screen-map.js` draws exactly this mark (its UNKNOWN_TEXT is MAP_UNKNOWN_LABEL verbatim,
# asserted in tests/src/ui/test_static_modules.py); this is the legend row that names it.
MAP_UNKNOWN_MARK = "hollow ring with a cross, no fill"
MAP_UNKNOWN_LABEL = "capacity not provided by API"


# Fields named in contracts/src/service.md whose unit is NOT recoverable from a
# suffix, because the unit sits in the middle of the name. Listed one by one on
# purpose: an unrecognised field still renders with no unit rather than a guessed one,
# and adding to this table is a deliberate act with the contract open.
UNIT_OVERRIDES: dict[str, str] = {
    "co2_kg_saved": "kg",                 # ScenarioResult.totals
    "floor_shortfall_kw_min": "kW·min",   # src/sched.evaluate scorecard
    "reduction_kw_achieved": "kW",        # src/sched, per-(site, interval) floor
    "peak_kw_baseline": "kW",             # ScenarioResult.totals
    "peak_kw_optimised": "kW",            # ScenarioResult.totals
    "firm_kw_per_site": "kW",             # diversification_curve rows
    "load_kw_before": "kW",               # dispatch: amended timeseries
    "load_kw_after": "kW",                # dispatch: amended timeseries
}


def unit_for_key(key: str) -> str:
    """Best-effort unit label for a wire field name.

    An exact match in `UNIT_OVERRIDES` wins; otherwise the longest matching suffix from
    the CONVENTIONS.md units table (`_eur_mwh` before `_eur`). Returns "" when the key
    carries no recognised unit -- callers must display the bare field name in that
    case, never a guessed unit the contract never promised.
    """
    if key in UNIT_OVERRIDES:
        return UNIT_OVERRIDES[key]
    for suffix in sorted(UNIT_SUFFIXES, key=len, reverse=True):
        if key.endswith(suffix):
            return UNIT_SUFFIXES[suffix]
    return ""


def is_assumed(source: Any) -> bool:
    """True if a `source` string marks the figure it travels with as unsourced.

    Covers both the bare sentinel (`source="ASSUMED"`) and the longer prose sources in
    `src/market/api.py::ASSUMPTIONS` / `PRODUCTS`, which all begin "ASSUMED -- ...".
    A missing/blank source is NOT "assumed" -- it is *unknown*, which the templates
    render as its own third state, because `Assumption` forbids an empty source and a
    blank one therefore means the service dropped it.
    """
    return isinstance(source, str) and bool(_ASSUMED_RE.search(source))


def fmt_number(value: Any, digits: int = 1) -> str:
    """Thousands-separated fixed-point, for a figure read off a projector.

    Non-numeric input returns `MISSING_LABEL` rather than `str(value)`: a template that
    reaches here with something unexpected must not print it as if it were a figure.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return MISSING_LABEL
    return f"{value:,.{digits}f}"


def fmt_multiplier(value: Any) -> str:
    """A multiplier as it would be read aloud: 3.0 -> "3", 2.5 -> "2.5".

    Deliberately not `fmt_number(v, 0)`: rounding a multiplier to whole numbers turns
    a 2.5x scenario into the string "2x", which reads as a different scenario rather
    than as a rounded one. Trailing zeros are dropped, significant decimals are not.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return MISSING_LABEL
    return f"{float(value):.2f}".rstrip("0").rstrip(".") or "0"


def fmt_rate(value: Any, digits: int = 2) -> str:
    """A 0..1 rate as a percentage with its sign carried by the unit, e.g. "1.25 %"."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return MISSING_LABEL
    return f"{value * 100:,.{digits}f} %"


def has_value(obj: Any, key: str) -> bool:
    """True when `obj` is a mapping that carries `key` with a non-None value.

    The one place "zero and unknown must never look the same" is decided: 0.0 has a
    value, an absent key does not, and the templates branch on this rather than on
    truthiness.
    """
    return isinstance(obj, Mapping) and key in obj and obj[key] is not None


def value_of(obj: Any, key: str) -> Any:
    """The raw value at `key`, or None when absent. Never a default."""
    if isinstance(obj, Mapping):
        return obj.get(key)
    return None


def to_berlin(value: Any) -> _dt.datetime | None:
    """Parse a tz-aware UTC wire timestamp (ISO string or datetime) and convert it to
    Europe/Berlin for display. Returns None (never a fabricated time) if `value` is
    falsy, unparseable, or **naive** -- a template must render "unknown", not a wrong
    clock.

    The naive case is the one that used to lie: `contracts/CONVENTIONS.md` says the wire
    is `datetime64[ns, UTC]`, so a timestamp that arrives without an offset is a service
    bug, not a UTC timestamp with the offset omitted. Stamping UTC on it and converting
    produced a confident Berlin wall-clock time that is one or two hours wrong whenever
    the sender meant local time -- and on `2026-10-25` the hour it invents is the
    ambiguous one. An unknown time shown as "unknown time" costs a demo a label; an
    invented one costs the audience their trust in every other number on the page.
    """
    if value is None or value == "":
        return None
    if isinstance(value, _dt.datetime):
        dt = value
    else:
        try:
            dt = _dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        return None
    return dt.astimezone(BERLIN_TZ)


def series_point_count(rows: Any, key: str) -> int:
    """How many rows carry a finite number at `key` -- i.e. how many points the canvas
    modules will actually plot for that series.

    The templates ask this instead of inspecting `rows[0]`. A series absent from the
    first interval but present later IS drawn by the module (every module tests each
    point with `typeof v === "number" && Number.isFinite(v)`), so a legend that read
    only the first row could print "series not drawn" beside a plotted series. Bools
    and non-finite floats are not points, matching that per-point test exactly.
    """
    if not isinstance(rows, (list, tuple)):
        return 0
    n = 0
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        v = row.get(key)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        if _math.isfinite(v):
            n += 1
    return n


def berlin_label(value: Any, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """Format `value` (see `to_berlin`) for display in Europe/Berlin, or "unknown time"
    if it cannot be parsed -- never silently falling back to a bare/UTC-labelled string
    that a judge could mistake for local time. The %Z in the default caller's format is
    deliberate on the clock-only variant below: on 2026-10-25 the same wall-clock hour
    occurs twice and only the offset distinguishes them.
    """
    dt = to_berlin(value)
    if dt is None:
        return "unknown time"
    return dt.strftime(fmt)


def berlin_clock(value: Any) -> str:
    """Clock time for an axis tick, with the UTC offset appended so the two 02:xx hours
    of the DST fall-back day (2026-10-25, the date CONVENTIONS.md names) are told apart
    rather than drawn on top of each other.
    """
    dt = to_berlin(value)
    if dt is None:
        return "unknown time"
    return dt.strftime("%H:%M %z")


_ENV: Environment | None = None


def jinja_env() -> Environment:
    """The lane's Jinja2 environment: loads `TEMPLATES_DIR`, autoescapes (this is
    HTML, and every rendered figure ultimately comes from an HTTP response), and
    exposes the formatting helpers above as filters/globals so templates can carry
    provenance without duplicating this logic per-screen. Built once and cached; it
    holds no per-request state.
    """
    global _ENV
    if _ENV is not None:
        return _ENV
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["unit_for_key"] = unit_for_key
    env.filters["is_assumed"] = is_assumed
    env.filters["berlin"] = berlin_label
    env.filters["berlin_clock"] = berlin_clock
    env.filters["num"] = fmt_number
    env.filters["rate"] = fmt_rate
    env.filters["mult"] = fmt_multiplier
    env.globals["SCREENS"] = SCREENS
    env.globals["SCREEN_TITLES"] = SCREEN_TITLES
    env.globals["STATIC"] = STATIC_URL_PREFIX
    env.globals["MAP_LEGEND_BINS"] = MAP_LEGEND_BINS
    env.globals["MAP_UNKNOWN_MARK"] = MAP_UNKNOWN_MARK
    env.globals["MAP_UNKNOWN_LABEL"] = MAP_UNKNOWN_LABEL
    env.globals["series_point_count"] = series_point_count
    env.globals["has_value"] = has_value
    env.globals["value_of"] = value_of
    env.globals["MISSING_LABEL"] = MISSING_LABEL
    env.globals["PER_INTERVAL_FLOOR_KEYS"] = PER_INTERVAL_FLOOR_KEYS
    env.globals["PER_INTERVAL_FLOOR_CAVEAT"] = PER_INTERVAL_FLOOR_CAVEAT
    _ENV = env
    return env


def render(screen: str, context: Mapping[str, Any] | None = None) -> str:
    """Render one of the five screens against `context`, a dict shaped like the
    corresponding `src/service` route response (see the module docstring).

    `context` may carry a top-level `error` dict, in which case the template renders
    the error banner instead of pretending the missing data is zero. Every screen
    shares that branch through one Jinja macro, `screen_body()` in `_macros.html`, so
    the error-vs-success decision is made in exactly one place.

    Raises `ValueError` for a screen name outside `SCREENS` -- there is no fallback
    template, because rendering *something* for an unknown screen is exactly the kind
    of silent degradation `contracts/CONVENTIONS.md` forbids.
    """
    if screen not in _TEMPLATE_BY_SCREEN:
        raise ValueError(f"unknown screen {screen!r}; must be one of {SCREENS}")
    ctx: dict[str, Any] = dict(context or {})
    ctx["screen"] = screen
    template = jinja_env().get_template(_TEMPLATE_BY_SCREEN[screen])
    return template.render(**ctx)


def static_assets_referenced(html: str) -> list[str]:
    """Every `STATIC_URL_PREFIX/...` path referenced by `src=` or `href=` in a rendered
    page, for tests to confirm each one actually exists under `STATIC_DIR` (an
    acceptance criterion: "every static asset the templates reference exists").
    Deliberately a plain string scan, not an HTML-parser dependency -- this lane adds
    no new third-party packages.
    """
    pattern = rf'''(?:src|href)=["']({re.escape(STATIC_URL_PREFIX)}/[^"']+)["']'''
    return sorted(set(re.findall(pattern, html)))


def external_resource_refs(html: str) -> list[str]:
    """Every `<script>` / `<link>` / `<img>` / `<iframe>` tag in `html` that points at
    an absolute http(s) URL.

    `contracts/src/ui.md`: the page must load "with the API reachable and *nothing
    else* — no analytics, no fonts, no calls to anything but the API and the pinned
    CDN". Returns the raw tag text so a test can assert either that the list is empty
    or that every entry satisfies `cdn_ref_is_pinned()`.
    """
    tags = re.findall(r"<(?:script|link|img|iframe)\b[^>]*>", html, flags=re.IGNORECASE)
    return [t for t in tags if re.search(r'(?:src|href)=["\']https?://', t, re.IGNORECASE)]


def cdn_ref_is_pinned(tag: str) -> bool:
    """True when an external resource tag is safe under `contracts/src/ui.md`: it
    carries a Subresource-Integrity hash AND its URL contains an explicit version
    segment (e.g. `/leaflet@1.9.4/`, `/d3/7.8.5/`). A `latest`/unversioned CDN URL is
    not pinned even with an integrity hash, because the hash would simply stop matching
    the day upstream moves -- which on conference wifi is a blank screen in front of
    judges.
    """
    if not re.search(r'\bintegrity=["\'][^"\']+["\']', tag, re.IGNORECASE):
        return False
    url_match = re.search(r'(?:src|href)=["\'](https?://[^"\']+)["\']', tag, re.IGNORECASE)
    if url_match is None:
        return False
    return bool(re.search(r"[@/]v?\d+\.\d+(\.\d+)?(?=[/@?\"']|$)", url_match.group(1)))


def unpinned_external_refs(html: str) -> list[str]:
    """The external resource tags in `html` that violate the CDN rule. Empty list means
    the page is demo-safe; anything in it is a tag a reviewer must reject.
    """
    return [tag for tag in external_resource_refs(html) if not cdn_ref_is_pinned(tag)]


def screens_for(paths: Iterable[str] | None = None) -> tuple[str, ...]:
    """The screen order the demo runs in, optionally filtered to `paths`. Exposed so
    `src/service` can build navigation without hard-coding this lane's ordering.
    """
    if paths is None:
        return SCREENS
    wanted = set(paths)
    return tuple(s for s in SCREENS if s in wanted)
