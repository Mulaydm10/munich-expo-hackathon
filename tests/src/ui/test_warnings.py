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
