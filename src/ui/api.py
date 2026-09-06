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
    templates_dir = ui.TEMPLATES_DIR         # Jinja2Templates(directory=...)
    static_dir = ui.STATIC_DIR                # StaticFiles(directory=...)
    html = ui.render("ledger", context)       # context shaped like the JSON below

`render()` never fetches anything and never computes a displayed figure — it only
formats numbers that are already in `context` and renders the provenance that came
with them. If a number is not present, the template says so; it does not invent one.
Nothing here imports `src.service` (unmerged, written concurrently — see issue #28);
this lane is tested entirely against recorded fixture payloads shaped like the
contract, never against a running service.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape

LANE = "src/ui"

_LANE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = _LANE_DIR / "templates"
STATIC_DIR = _LANE_DIR / "static"

# Display timezone per contracts/CONVENTIONS.md: the wire is always tz-aware UTC: a
# human never sees anything else.
BERLIN_TZ = ZoneInfo("Europe/Berlin")

# The five screens, in the order DEMO.md uses them (contracts/src/ui.md "Screens").
SCREENS: tuple[str, ...] = ("map", "day", "call", "pooling", "ledger")

_TEMPLATE_BY_SCREEN: dict[str, str] = {
    "map": "map.html",
    "day": "day.html",
    "call": "call.html",
    "pooling": "pooling.html",
    "ledger": "ledger.html",
}

# contracts/CONVENTIONS.md "Units" table: column-suffix -> the unit a judge should
# read next to it. Used to label any field the frozen contract didn't already name a
# unit for (see the dispatch-response gap noted in the PR description) -- an
# unrecognised suffix renders with NO unit rather than a guessed one.
UNIT_SUFFIXES: dict[str, str] = {
    "_eur_mwh": "EUR/MWh",
    "_eur_mw_h": "EUR/MW/h",
    "_g_kwh": "gCO₂/kWh",
    "_kwh": "kWh",
    "_kw": "kW",
    "_eur": "EUR",
    "_c": "°C",
    "_min": "min",
}


def unit_for_key(key: str) -> str:
    """Best-effort unit label for a wire field name, from the suffix table above.

    Longest suffix wins (`_eur_mwh` before `_eur`). Returns "" when the key carries no
    recognised suffix -- callers must display the bare field name in that case, never
    a guessed unit the contract never promised.
    """
    for suffix in sorted(UNIT_SUFFIXES, key=len, reverse=True):
        if key.endswith(suffix):
            return UNIT_SUFFIXES[suffix]
    return ""


def is_assumed(source: Any) -> bool:
    """True if a `source` string (an `Assumption.source`, a `Product.source`, a
    `penalty_multiplier_source`, ...) marks the figure it travels with as
    unsourced/invented. Case-insensitive substring match on "ASSUMED" so both the bare
    sentinel (`source="ASSUMED"`) and the longer prose sources in
    `src/market/api.py::ASSUMPTIONS`/`PRODUCTS` (which all start "ASSUMED -- ...")
    match the same way.
    """
    return isinstance(source, str) and "assumed" in source.lower()


def to_berlin(value: Any) -> _dt.datetime | None:
    """Parse a tz-aware UTC wire timestamp (ISO string or datetime) and convert it to
    Europe/Berlin for display. Returns None (never a fabricated time) if `value` is
    falsy or unparseable -- a template must render "unknown", not a wrong clock.
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
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return dt.astimezone(BERLIN_TZ)


def berlin_label(value: Any, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """Format `value` (see `to_berlin`) for display, or "unknown time" if it can't be
    parsed -- never silently falling back to a bare/UTC-labelled string that a judge
    could mistake for Europe/Berlin.
    """
    dt = to_berlin(value)
    if dt is None:
        return "unknown time"
    return dt.strftime(fmt)


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
    env.globals["SCREENS"] = SCREENS
    _ENV = env
    return env


def render(screen: str, context: Mapping[str, Any] | None = None) -> str:
    """Render one of the five screens against `context`, a dict shaped like the
    corresponding `src/service` route response (see `contracts/src/service.md`).

    `context` may additionally carry a top-level `error` dict — `{error, detail,
    how_to_fix}`, exactly the shape `contracts/src/service.md` mandates for a failed
    route — in which case the template renders the error banner instead of pretending
    the missing data is zero. Every screen shares this branch through one Jinja macro,
    `screen_body()` in `_macros.html`, so the error-vs-success decision is made in
    exactly one place rather than re-implemented per template.

    Raises `ValueError` for a screen name outside `SCREENS` -- there is no fallback
    template, because rendering *something* for an unknown screen is exactly the kind
    of silent degradation `contracts/CONVENTIONS.md` forbids.
    """
    if screen not in _TEMPLATE_BY_SCREEN:
        raise ValueError(f"unknown screen {screen!r}; must be one of {SCREENS}")
    template = jinja_env().get_template(_TEMPLATE_BY_SCREEN[screen])
    return template.render(**dict(context or {}), screen=screen)


def static_assets_referenced(html: str) -> list[str]:
    """Every `/static/...` path referenced by `src=` or `href=` in a rendered page,
    for tests to confirm each one actually exists under `STATIC_DIR` (an acceptance
    criterion: "every static asset the templates reference exists"). Deliberately a
    plain string scan, not an HTML parser dependency -- this lane adds no new
    third-party packages.
    """
    import re

    paths = re.findall(r'''(?:src|href)=["'](/static/[^"']+)["']''', html)
    return sorted(set(paths))


def external_script_tags(html: str) -> list[str]:
    """Every `<script ...>` tag whose `src` is an absolute http(s) URL (i.e. a CDN
    reference, per `contracts/src/ui.md`'s "CDN with an integrity hash, pinned by
    version" rule). Returns the raw tag text so a test can assert each one carries
    both `integrity=` and a version-looking segment in the URL.
    """
    import re

    return re.findall(r"<script\b[^>]*\bsrc=[\"']https?://[^\"']+[\"'][^>]*>", html)
