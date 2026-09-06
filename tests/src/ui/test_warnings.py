"""`warnings[]` is rendered, not swallowed.

`contracts/CONVENTIONS.md` requires every coercion to be observable; this lane is where
"observable" becomes visible to a judge. A page that renders a clean number over a
degraded pipeline is the failure mode the project cares most about.
"""

from __future__ import annotations

import re

import pytest

from src.ui import api

from .conftest import contexts

CONTEXTS = contexts()


@pytest.mark.parametrize("screen", api.SCREENS)
def test_every_screen_renders_every_warning_it_was_given(screen: str, warnings: list) -> None:
    html = api.render(screen, {**CONTEXTS[screen], "warnings": warnings})
    section = re.search(r'data-testid="warnings"(.*?)</section>', html, re.S)
    assert section is not None, f"{screen} swallowed warnings[]"
    body = section.group(1)
    assert html.count('data-testid="warning-item"') == len(warnings)
    for w in warnings:
        message = w["message"] if isinstance(w, dict) else w
        assert message in body, (screen, message)


@pytest.mark.parametrize("screen", api.SCREENS)
def test_warning_count_is_stated(screen: str, warnings: list) -> None:
    html = api.render(screen, {**CONTEXTS[screen], "warnings": warnings})
    assert f"Pipeline warnings — {len(warnings)}" in html


def test_a_structured_warning_shows_its_code_and_its_measured_value(warnings: list) -> None:
    html = api.render("ledger", {**CONTEXTS["ledger"], "warnings": warnings})
    assert "envelope_clipped" in html
    # The coercion rate itself, not merely the fact that something was coerced.
    assert "0.0223" in html
    assert "fraction of intervals" in html


def test_a_plain_string_warning_is_rendered_too(warnings: list) -> None:
    html = api.render("map", {**CONTEXTS["map"], "warnings": ["price table forward-filled"]})
    assert "price table forward-filled" in html
    assert html.count('data-testid="warning-item"') == 1


def test_no_warnings_says_none_rather_than_showing_nothing() -> None:
    """Absence of a warnings block and "the pipeline reported none" are different
    claims. Silence would let a swallowed warnings[] look like a clean run."""
    html = api.render("ledger", {**CONTEXTS["ledger"], "warnings": []})
    assert 'data-testid="warnings-empty"' in html
    assert "none reported for this scenario" in html
    assert 'data-testid="warning-item"' not in html


def test_warnings_appear_before_the_figures_they_qualify(warnings: list) -> None:
    html = api.render("ledger", {**CONTEXTS["ledger"], "warnings": warnings})
    assert html.index('data-testid="warnings"') < html.index('data-testid="totals-table"')


def test_warnings_are_escaped_not_executed() -> None:
    html = api.render("map", {**CONTEXTS["map"], "warnings": ["<script>alert(1)</script>"]})
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


# --- #37 item 2: an error must not swallow the warnings --------------------------
#
# The defect this section pins: `_base.html` wrapped the warnings block in
# `{% if not error %}`, so a response carrying BOTH an error and warnings[] rendered the
# error and dropped every warning. That is the exact shape contracts/src/service.md
# calls forbidden -- the pipeline degraded, then failed, and the page reported only the
# failure. The charts are what an error suppresses; the evidence never is.

@pytest.mark.parametrize("screen", api.SCREENS)
def test_an_error_does_not_swallow_the_warnings_that_came_with_it(
    screen: str, warnings: list, error: dict
) -> None:
    html = api.render(screen, {**CONTEXTS[screen], "warnings": warnings, "error": error})
    assert 'data-testid="error-banner"' in html, "the error itself must still show"
    section = re.search(r'data-testid="warnings"(.*?)</section>', html, re.S)
    assert section is not None, f"{screen} swallowed warnings[] on the error branch"
    assert html.count('data-testid="warning-item"') == len(warnings)
    for w in warnings:
        message = w["message"] if isinstance(w, dict) else w
        assert message in section.group(1), (screen, message)


@pytest.mark.parametrize("screen", api.SCREENS)
def test_an_error_still_suppresses_the_charts_it_always_did(screen: str, warnings: list, error: dict) -> None:
    """The other end of the same rule: rendering warnings under an error must not have
    smuggled the chart body back in. An error banner beside a chart reading 0 kW is the
    failure the suppression exists for."""
    html = api.render(screen, {**CONTEXTS[screen], "warnings": warnings, "error": error})
    assert "<canvas" not in html
    assert 'class="figure-value"' not in html


def test_an_error_with_no_warnings_says_unknown_rather_than_none_reported(error: dict) -> None:
    """"The pipeline reported no warnings" is a claim about a response that never
    arrived. Under an error the honest word is unknown -- the zero-vs-unknown rule
    applied to the warning list itself."""
    html = api.render("ledger", {**CONTEXTS["ledger"], "warnings": [], "error": error})
    assert 'data-testid="warnings-unknown"' in html
    assert "none reported for this scenario" not in html


def test_no_error_and_no_warnings_still_says_none_reported() -> None:
    """The other end: without an error, an empty list IS a clean run and must read as
    one. The two empty states must not collapse into a single sentence."""
    html = api.render("ledger", {**CONTEXTS["ledger"], "warnings": []})
    assert 'data-testid="warnings-empty"' in html
    assert 'data-testid="warnings-unknown"' not in html
    assert "none reported for this scenario" in html
