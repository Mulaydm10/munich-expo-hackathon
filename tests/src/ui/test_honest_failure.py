"""Zero, empty and unknown must never look the same.

`contracts/src/ui.md`: "Degrades honestly: a failed fetch shows the error text from the
API, never an empty chart that looks like zero." Issue #30 makes it an acceptance
criterion with a test attached. These are that test.
"""

from __future__ import annotations

import re

import pytest

from src.ui import api

from .conftest import contexts

CONTEXTS = contexts()


@pytest.mark.parametrize("screen", api.SCREENS)
def test_error_branch_shows_the_apis_own_error_detail_and_fix(screen: str, error: dict) -> None:
    html = api.render(screen, {**CONTEXTS[screen], "error": error})
    banner = re.search(r'data-testid="error-banner"(.*?)</div>', html, re.S)
    assert banner is not None
    assert error["error"] in banner.group(1)
    assert error["detail"] in banner.group(1)
    assert error["how_to_fix"] in banner.group(1)


@pytest.mark.parametrize("screen", api.SCREENS)
def test_error_branch_draws_no_chart_and_no_figures(screen: str, error: dict) -> None:
    """The failure mode this criterion exists to prevent: an error banner sitting next
    to a chart that reads 0 kW. On error the screen body is replaced, not decorated."""
    ok_html = api.render(screen, CONTEXTS[screen])
    err_html = api.render(screen, {**CONTEXTS[screen], "error": error})
    assert "<canvas" in ok_html or 'data-testid="totals-table"' in ok_html
    assert "<canvas" not in err_html
    assert 'type="application/json"' not in err_html
    assert 'class="figure-value"' not in err_html


@pytest.mark.parametrize("screen", api.SCREENS)
def test_error_branch_says_it_is_unknown_not_zero(screen: str, error: dict) -> None:
    html = api.render(screen, {**CONTEXTS[screen], "error": error})
    assert "not zero, unknown" in html


def test_a_zero_total_and_a_missing_total_render_differently(ledger: dict) -> None:
    """0.00 EUR is a measurement. An absent field is not. They must not print the
    same string, in either direction."""
    zeroed = {**ledger, "totals": {**ledger["totals"], "penalty_eur": 0.0}}
    missing = {**ledger, "totals": {k: v for k, v in ledger["totals"].items() if k != "penalty_eur"}}

    zero_row = re.search(r'data-testid="figure-penalty_eur">(.*?)</tr>', api.render("ledger", zeroed), re.S)
    missing_row = re.search(r'data-testid="figure-penalty_eur">(.*?)</tr>', api.render("ledger", missing), re.S)
    assert zero_row is not None and missing_row is not None
    assert "0.00" in zero_row.group(1)
    assert 'data-testid="value-penalty_eur"' in zero_row.group(1)
    assert api.MISSING_LABEL in missing_row.group(1)
    assert "0.00" not in missing_row.group(1)
    assert 'data-testid="missing-penalty_eur"' in missing_row.group(1)


def test_a_none_valued_total_is_treated_as_missing_not_as_zero(ledger: dict) -> None:
    nulled = {**ledger, "totals": {**ledger["totals"], "net_eur": None}}
    row = re.search(r'data-testid="figure-net_eur">(.*?)</tr>', api.render("ledger", nulled), re.S)
    assert row is not None
    assert api.MISSING_LABEL in row.group(1)
    assert "0.00" not in row.group(1)


def test_an_empty_site_list_says_empty_response_not_zero() -> None:
    html = api.render("map", {"sites": []})
    assert 'data-testid="empty-site"' in html
    assert "not a measurement of zero" in html
    assert "<canvas" not in html          # nothing to draw, so nothing that reads as a drawn zero
    assert 'data-testid="error-banner"' not in html   # an empty answer is not an error


def test_an_empty_pooling_curve_says_empty_response(curve: list[dict]) -> None:
    html = api.render("pooling", {"curve": [], "pool_method": "empirical"})
    assert 'data-testid="empty-pooling curve"' in html
    assert "curve-row-0" not in html


def test_a_series_the_api_did_not_send_is_named_as_missing_not_drawn(intervals: list[dict]) -> None:
    stripped = [{k: v for k, v in row.items() if k != "envelope_kw"} for row in intervals]
    html = api.render("day", {"intervals": stripped})
    row = re.search(r'data-testid="series-envelope_kw">(.*?)</tr>', html, re.S)
    assert row is not None
    assert api.MISSING_LABEL in row.group(1)
    assert "series not drawn" in row.group(1)


def test_no_dispatch_yet_is_not_a_dispatch_that_delivered_nothing() -> None:
    html = api.render("call", {"dispatch_url": "/api/scenario/abc/dispatch"})
    assert 'data-testid="dispatch-pending"' in html
    assert "Nothing on this screen is a result until the API has answered." in html
    assert 'data-testid="value-delivered_kw"' not in html
    assert 'data-testid="dispatch-figures"' not in html


def test_a_missing_dispatch_url_is_stated_rather_than_a_dead_button(dispatch: dict) -> None:
    html = api.render("call", {"dispatch": dispatch})
    assert 'data-testid="dispatch-url-missing"' in html
    assert "data-dispatch-url" not in html


def test_missing_assumptions_invalidate_the_ledger_out_loud(ledger: dict) -> None:
    """contracts/CONVENTIONS.md provenance rule: no figure without a source. If
    /api/assumptions answered with nothing, the page must say so rather than quietly
    printing unattributed euros."""
    html = api.render("ledger", {k: v for k, v in ledger.items() if k != "assumptions"})
    assert 'data-testid="assumptions-missing"' in html
    assert "do not quote any of them" in html


def test_missing_calibration_is_flagged_as_an_unverified_promise(ledger: dict) -> None:
    html = api.render("ledger", {k: v for k, v in ledger.items() if k != "calibration"})
    assert 'data-testid="calibration-missing"' in html
    assert "unverified" in html


def test_missing_event_window_is_not_rendered_as_a_zero_kw_request(dispatch: dict) -> None:
    no_req = {**dispatch, "event": {k: v for k, v in dispatch["event"].items() if k != "required_kw"}}
    html = api.render("call", {"dispatch": no_req})
    line = re.search(r'data-testid="dispatch-event">(.*?)</p>', html, re.S)
    assert line is not None
    assert api.MISSING_LABEL in line.group(1)


def test_an_unparseable_timestamp_reads_as_unknown_time_not_as_the_epoch() -> None:
    assert api.berlin_label("not-a-timestamp") == "unknown time"
    assert api.berlin_label(None) == "unknown time"
    assert api.to_berlin("not-a-timestamp") is None


def test_a_non_numeric_figure_is_not_printed_as_if_it_were_a_number() -> None:
    assert api.fmt_number("n/a") == api.MISSING_LABEL
    assert api.fmt_number(None) == api.MISSING_LABEL
    assert api.fmt_rate(None) == api.MISSING_LABEL
