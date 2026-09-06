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


def test_day_carries_the_stream_url_without_claiming_it_is_the_playback_source(
    intervals: list[dict],
) -> None:
    """#37 item 8. `play()` scrubs the recorded response on a setInterval; it does not
    consume the SSE stream contracts/src/ui.md asks for. The URL stays in `data-stream`
    for the module that will eventually read it, but the sentence a judge reads must not
    call it the playback source while a timer drives the cursor. An unimplemented
    feature described as working is the same defect class as a chart drawing zero for
    missing data -- see the PR: the feature itself is deferred, only the claim is fixed.
    """
    html = api.render("day", {"intervals": intervals, "stream_url": "/api/scenario/xyz/stream"})
    assert 'data-stream="/api/scenario/xyz/stream"' in html
    assert 'data-testid="play-button"' in html
    note = re.search(r'data-testid="stream-url"[^>]*>(.*?)</span>', html, re.S)
    assert note is not None
    assert "playback source:" not in note.group(1)
    assert "does not" in note.group(1) and "recorded response" in note.group(1)


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


# --- #37 item 3: unknown capacity is not the smallest bucket ---------------------

def test_the_map_legend_names_unknown_capacity_as_its_own_mark() -> None:
    """`screen-map.js` drew a site with no `firm_kw` as the 0-50 kW dot. The legend had
    no way to say otherwise because it had no row for it. The unknown row deliberately
    carries no kW figure -- there is none -- so it cannot be read as a range."""
    html = api.render("map", CONTEXTS["map"])
    legend = re.search(r'data-testid="map-legend">(.*?)</table>', html, re.S)
    assert legend is not None
    row = re.search(r'data-testid="legend-unknown">(.*?)</tr>', legend.group(1), re.S)
    assert row is not None, "the legend must name the unknown mark"
    assert api.MAP_UNKNOWN_LABEL in row.group(1)
    assert api.MAP_UNKNOWN_MARK in row.group(1)
    assert "kW" not in row.group(1), "an unknown capacity has no range and no unit"


def test_the_unknown_mark_is_not_the_mark_of_any_capacity_bucket() -> None:
    """Zero and unknown must never look the same, so their legend marks must differ --
    including from the 0-50 kW bucket a zero-capacity site actually falls in."""
    marks = [mark for _low, _high, mark in api.MAP_LEGEND_BINS]
    assert api.MAP_UNKNOWN_MARK not in marks


def test_the_map_still_hands_over_a_site_whose_capacity_is_absent(sites: list[dict]) -> None:
    """Both ends of the coercion, in one fixture: DE-HAM-0031 carries firm_kw 0.0 and
    DE-KOE-0114 carries no firm_kw at all. The payload must preserve that difference --
    a template that filled in a default would make the module's fix unreachable."""
    html = api.render("map", {"sites": sites})
    payload = re.search(r'id="payload-sites">(.*?)</script>', html, re.S)
    assert payload is not None
    rows = json.loads(payload.group(1).replace("\\u003c", "<").replace("\\u003e", ">").replace("\\u0026", "&"))
    by_id = {r["site_id"]: r for r in rows}
    assert by_id["DE-HAM-0031"]["firm_kw"] == 0.0
    assert "firm_kw" not in by_id["DE-KOE-0114"]


# --- #37 item 5: the dispatch button POSTs a real ReductionEvent -----------------

def test_call_hands_the_module_the_reduction_event_to_post(reduction_event: dict) -> None:
    """The button used to POST the literal body "{}". A validating service rejects that,
    so the demo's headline action could not produce promised-vs-delivered at all. The
    event is the API's, rendered here verbatim: this lane may not invent a reduction
    size or a notice period (contracts/src/ui.md forbids computing anything)."""
    html = api.render("call", CONTEXTS["call"])
    payload = re.search(r'id="payload-reduction-event">(.*?)</script>', html, re.S)
    assert payload is not None, "no ReductionEvent was handed to screen-call.js"
    assert json.loads(payload.group(1)) == reduction_event
    for field in ("call_t", "notice_min", "duration_min", "reduction_kw"):
        assert field in reduction_event


def test_call_states_the_event_it_will_post_in_words(reduction_event: dict) -> None:
    html = api.render("call", CONTEXTS["call"])
    line = re.search(r'data-testid="dispatch-request">(.*?)</p>', html, re.S)
    assert line is not None
    assert "1,000.0" in line.group(1)      # reduction_kw, formatted for a projector
    assert "240" in line.group(1)          # duration_min
    assert "15" in line.group(1)           # notice_min
    # call_t is 16:00Z on the DST-fallback day. The clocks went back at 01:00 UTC, so
    # Berlin is CET (+0100) by then: 17:00, not the 18:00 a CEST reflex would write.
    assert "2026-10-25 17:00" in line.group(1)


def test_a_call_screen_with_no_reduction_event_disables_the_button(dispatch: dict) -> None:
    """The avoid-it end of the pair. With no event the page must say so and refuse to
    fire, rather than fall back to an empty body the service must reject."""
    html = api.render("call", {"dispatch": dispatch, "dispatch_url": "/d"})
    assert 'data-testid="reduction-event-missing"' in html
    assert "payload-reduction-event" not in html
    button = re.search(r'data-testid="dispatch-button"(.*?)>', html, re.S)
    assert button is not None and "disabled" in button.group(1)


def test_a_partial_reduction_event_is_refused_rather_than_completed(reduction_event: dict) -> None:
    """Three of four fields is not a ReductionEvent. Defaulting the fourth would be this
    page choosing a grid commitment parameter."""
    for missing in ("call_t", "notice_min", "duration_min", "reduction_kw"):
        partial = {k: v for k, v in reduction_event.items() if k != missing}
        html = api.render("call", {"dispatch_url": "/d", "reduction_event": partial})
        assert 'data-testid="reduction-event-missing"' in html, missing
        assert "payload-reduction-event" not in html, missing


def test_a_complete_event_leaves_the_button_live(reduction_event: dict) -> None:
    button = re.search(
        r'data-testid="dispatch-button"(.*?)>', api.render("call", CONTEXTS["call"]), re.S
    )
    assert button is not None and "disabled" not in button.group(1)


# --- #37 item 7: "not drawn" is decided over every interval, not over the first ---

def _intervals_missing(rows: list[dict], key: str, keep: set[int]) -> list[dict]:
    """`rows` with `key` removed everywhere except at the row indices in `keep`."""
    return [row if i in keep else {k: v for k, v in row.items() if k != key} for i, row in enumerate(rows)]


def test_a_series_absent_from_the_first_interval_but_drawn_later_is_not_called_missing(
    intervals: list[dict],
) -> None:
    """The chart plots any finite point, so a series starting at interval 2 IS on the
    canvas. Reading only `intervals[0]` printed "series not drawn" beside a drawn line,
    which teaches a judge to distrust the table rather than the chart."""
    keep = {i for i in range(len(intervals)) if i >= 2}
    html = api.render("day", {"intervals": _intervals_missing(intervals, "envelope_kw", keep)})
    row = re.search(r'data-testid="series-envelope_kw">(.*?)</tr>', html, re.S)
    assert row is not None
    assert 'data-testid="missing-series-envelope_kw"' not in row.group(1)
    assert "series not drawn" not in row.group(1)
    assert 'data-testid="partial-series-envelope_kw"' in row.group(1)
    # The count is computed here from the fixture, not read back off the page.
    assert f"drawn from {len(keep)} of {len(intervals)} intervals" in " ".join(row.group(1).split())


def test_a_fully_populated_series_is_not_labelled_partial(intervals: list[dict]) -> None:
    """The other end: the partial marker must be able to be absent, or it proves
    nothing."""
    row = re.search(
        r'data-testid="series-optimised_load_kw">(.*?)</tr>',
        api.render("day", {"intervals": intervals}),
        re.S,
    )
    assert row is not None
    assert api.series_point_count(intervals, "optimised_load_kw") == len(intervals)
    assert 'data-testid="partial-series-optimised_load_kw"' not in row.group(1)


def test_a_series_absent_from_every_interval_is_still_called_not_drawn(intervals: list[dict]) -> None:
    html = api.render("day", {"intervals": _intervals_missing(intervals, "firm_kw", set())})
    row = re.search(r'data-testid="series-firm_kw">(.*?)</tr>', html, re.S)
    assert row is not None
    assert "series not drawn" in row.group(1)
    assert 'data-testid="partial-series-firm_kw"' not in row.group(1)


def test_a_null_valued_point_is_a_gap_not_a_plotted_zero(intervals: list[dict]) -> None:
    """None is not a point on the canvas (`typeof v === "number"` rejects it), so it must
    not be counted as one in the table either."""
    nulled = [{**row, "price_eur_mwh": None} for row in intervals[:3]] + list(intervals[3:])
    assert api.series_point_count(nulled, "price_eur_mwh") == len(intervals) - 3
    row = re.search(
        r'data-testid="series-price_eur_mwh">(.*?)</tr>', api.render("day", {"intervals": nulled}), re.S
    )
    assert row is not None
    assert f"drawn from {len(intervals) - 3} of {len(intervals)} intervals" in " ".join(row.group(1).split())
