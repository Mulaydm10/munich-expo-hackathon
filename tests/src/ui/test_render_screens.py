"""The five screens render, and what they render comes from the payload.

`contracts/src/ui.md` names the screens and their order; `DEMO.md` is the order a judge
sees them in. These tests assert on rendered markup, not on "did it raise".
"""

from __future__ import annotations

import json
import re

import pytest

from src.ui import api

from .conftest import contexts

CONTEXTS = contexts()


def test_screens_are_the_five_the_contract_names_in_demo_order() -> None:
    assert api.SCREENS == ("map", "day", "call", "pooling", "ledger")


@pytest.mark.parametrize("screen", api.SCREENS)
def test_every_screen_renders_a_full_page_with_its_own_title(screen: str) -> None:
    html = api.render(screen, CONTEXTS[screen])
    assert html.lstrip().startswith("<!DOCTYPE html>")
    assert f"<title>FlexGrid — {api.SCREEN_TITLES[screen]}</title>" in html
    assert f'<body class="screen-{screen}">' in html


@pytest.mark.parametrize("screen", api.SCREENS)
def test_every_screen_marks_itself_current_in_the_nav_and_links_the_others(screen: str) -> None:
    html = api.render(screen, CONTEXTS[screen])
    current = re.search(r'data-testid="nav-current">([^<]*)<', html)
    assert current is not None and screen in current.group(1)
    for other in api.SCREENS:
        if other != screen:
            assert f'href="/{other}"' in html


@pytest.mark.parametrize("screen", api.SCREENS)
def test_keyboard_happy_path_is_advertised_on_every_screen(screen: str) -> None:
    # contracts/src/ui.md: "space = play/pause, `d` = dispatch" so the demo does not
    # depend on a trackpad in front of judges.
    html = api.render(screen, CONTEXTS[screen])
    hint = re.search(r'data-testid="kbd-hint">(.*?)</p>', html, re.S)
    assert hint is not None
    assert "<kbd>space</kbd>" in hint.group(1)
    assert "<kbd>d</kbd>" in hint.group(1)


def test_unknown_screen_raises_rather_than_rendering_something() -> None:
    with pytest.raises(ValueError, match="unknown screen"):
        api.render("dashboard", {})


# --- map ---------------------------------------------------------------------

def test_map_hands_every_site_to_the_module_verbatim(sites: list[dict]) -> None:
    html = api.render("map", {"sites": sites})
    payload = re.search(r'id="payload-sites">(.*?)</script>', html, re.S)
    assert payload is not None
    # Jinja's tojson escapes < > & for safety; nothing else is altered.
    raw = payload.group(1).replace("\\u003c", "<").replace("\\u003e", ">").replace("\\u0026", "&")
    assert json.loads(raw) == sites


def test_map_reports_the_number_of_sites_in_the_response(sites: list[dict]) -> None:
    html = api.render("map", {"sites": sites})
    line = re.search(r'data-testid="site-count">(.*?)</p>', html, re.S)
    assert line is not None
    assert f"<strong>{len(sites)}</strong>" in line.group(1)


def test_map_markup_does_not_grow_with_the_number_of_sites(sites: list[dict]) -> None:
    """contracts/src/ui.md: the map must stay interactive with tens of thousands of
    points, which rules out one DOM node per site. The only thing that may grow is the
    JSON payload the canvas module reads."""
    many = [dict(s, site_id=f"{s['site_id']}-{i}") for i in range(1200) for s in sites[:1]]
    few_html = api.render("map", {"sites": sites[:1]})
    many_html = api.render("map", {"sites": many})

    def strip_payload(html: str) -> str:
        return re.sub(r'<script type="application/json".*?</script>', "", html, flags=re.S)

    def tag_count(html: str) -> int:
        return len(re.findall(r"<[a-zA-Z]", html))

    assert tag_count(strip_payload(many_html)) == tag_count(strip_payload(few_html))


def test_map_legend_labels_every_bucket_with_a_unit_and_a_non_colour_mark() -> None:
    html = api.render("map", CONTEXTS["map"])
    legend = re.search(r'data-testid="map-legend">(.*?)</table>', html, re.S)
    assert legend is not None
    for index, (_low, _high, mark) in enumerate(api.MAP_LEGEND_BINS, start=1):
        assert f'data-testid="legend-bin-{index}"' in legend.group(1)
        assert mark in legend.group(1)
    assert legend.group(1).count("kW") == len(api.MAP_LEGEND_BINS)


# --- day ---------------------------------------------------------------------

def test_day_states_the_interval_count_and_the_span_in_berlin(intervals: list[dict]) -> None:
    html = api.render("day", {"intervals": intervals})
    lede = re.search(r'data-testid="interval-count">(.*?)</p>', html, re.S)
    assert lede is not None
    assert f"<strong>{len(intervals)}</strong>" in lede.group(1)
    # 2026-10-24T22:00Z is 2026-10-25 00:00 in Berlin (still CEST, +0200).
    assert "2026-10-25 00:00" in lede.group(1)
    assert "2026-10-25 04:00" in lede.group(1)


def test_day_names_the_field_behind_every_drawn_series(intervals: list[dict]) -> None:
    html = api.render("day", {"intervals": intervals})
    for key, unit in [
        ("baseline_load_kw", "kW"),
        ("optimised_load_kw", "kW"),
        ("envelope_kw", "kW"),
        ("firm_kw", "kW"),
        ("price_eur_mwh", "EUR/MWh"),
    ]:
        row = re.search(rf'data-testid="series-{key}">(.*?)</tr>', html, re.S)
        assert row is not None, key
        assert f"<code>{key}</code>" in row.group(1)
        assert f'<span class="unit">{unit}</span>' in row.group(1)


def test_day_offers_playback_from_the_stream_url_the_api_gave(intervals: list[dict]) -> None:
    html = api.render("day", {"intervals": intervals, "stream_url": "/api/scenario/xyz/stream"})
    assert 'data-stream="/api/scenario/xyz/stream"' in html
    assert 'data-testid="play-button"' in html


# --- call --------------------------------------------------------------------

def test_call_shows_promised_and_delivered_as_returned(dispatch: dict) -> None:
    html = api.render("call", {"dispatch": dispatch, "dispatch_url": "/d"})
    assert re.search(r'data-testid="value-promised_kw">1,000\.0<', html)
    assert re.search(r'data-testid="value-delivered_kw">941\.2', html)
    assert re.search(r'data-testid="value-shortfall_kwh">58\.75<', html)


def test_call_button_carries_the_route_the_api_supplied(dispatch: dict) -> None:
    html = api.render("call", {"dispatch": dispatch, "dispatch_url": "/api/scenario/xyz/dispatch"})
    assert 'data-dispatch-url="/api/scenario/xyz/dispatch"' in html


def test_call_shows_the_vans_finishing_on_time(dispatch: dict) -> None:
    html = api.render("call", {"dispatch": dispatch})
    assert re.search(r'data-testid="value-sessions_on_time">214<', html)
    assert re.search(r'data-testid="value-deadline_misses">0<', html)


# --- pooling -----------------------------------------------------------------

def test_pooling_table_prints_every_row_of_the_api_curve(curve: list[dict]) -> None:
    html = api.render("pooling", {"curve": curve, "pool_method": "empirical"})
    for i, row in enumerate(curve):
        cell = re.search(rf'data-testid="curve-row-{i}">(.*?)</tr>', html, re.S)
        assert cell is not None, i
        assert f"{row['firm_kw_per_site']:,.2f}" in cell.group(1)
        assert f"{row['shortfall_rate'] * 100:,.2f} %" in cell.group(1)
        assert f"{row['n_sites']:,.0f}" in cell.group(1)


def test_pooling_values_track_the_payload_not_a_hard_coded_curve(curve: list[dict]) -> None:
    """Change the payload, the page changes. A curve baked into the template or fitted
    in the browser would keep printing the original numbers."""
    bumped = [dict(r, firm_kw_per_site=r["firm_kw_per_site"] + 100.0) for r in curve]
    html = api.render("pooling", {"curve": bumped, "pool_method": "empirical"})

    def cell_pattern(value: float) -> str:
        # Guard against matching 12.40 inside 112.40.
        return rf'(?<![\d.]){re.escape(f"{value:,.2f}")} <span class="unit">kW</span>'

    for row in curve:
        assert re.search(cell_pattern(row["firm_kw_per_site"] + 100.0), html), row
        assert not re.search(cell_pattern(row["firm_kw_per_site"]), html), row


def test_pooling_names_the_method_or_says_it_cannot(curve: list[dict]) -> None:
    with_method = api.render("pooling", {"curve": curve, "pool_method": "gaussian_copula"})
    assert "<code>gaussian_copula</code>" in with_method
    without = api.render("pooling", {"curve": curve})
    assert 'data-testid="pool-method-missing"' in without
    assert api.MISSING_LABEL in without


def test_pooling_slider_spans_the_rows_returned(curve: list[dict]) -> None:
    html = api.render("pooling", {"curve": curve})
    slider = re.search(r'data-testid="n-sites-slider"([^>]*)>', html)
    assert slider is not None
    assert f'max="{len(curve) - 1}"' in slider.group(1)


# --- ledger ------------------------------------------------------------------

def test_ledger_prints_every_total_with_its_unit(ledger: dict) -> None:
    html = api.render("ledger", ledger)
    for key, digits, unit in [
        ("energy_cost_eur", 2, "EUR"),
        ("capacity_revenue_eur", 2, "EUR"),
        ("net_eur", 2, "EUR"),
        ("co2_kg_saved", 1, "kg"),
        ("peak_kw_baseline", 1, "kW"),
        ("pool_firm_mw", 3, "MW"),
    ]:
        row = re.search(rf'data-testid="figure-{key}">(.*?)</tr>', html, re.S)
        assert row is not None, key
        assert f"{ledger['totals'][key]:,.{digits}f}" in row.group(1)
        assert f'<span class="unit">{unit}</span>' in row.group(1)


def test_ledger_shows_the_sched_scorecard_and_the_forecast_calibration(ledger: dict) -> None:
    html = api.render("ledger", ledger)
    assert 'data-testid="scorecard-table"' in html
    assert re.search(r'data-testid="value-floor_shortfall_kw_min">58\.75<', html)
    assert 'data-testid="calibration-0.05"' in html
    assert "94.82 %" in html
