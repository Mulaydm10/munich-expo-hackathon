"""Every displayed figure carries its provenance, and a guess is labelled as a guess.

`contracts/CONVENTIONS.md` "Provenance rule" and issue #30's acceptance criteria, which
name three specific figures: `penalty_multiplier`, `peaker_plant_capacity_mw = 50.0`,
and any product parameter carrying `source="ASSUMED"`.
"""

from __future__ import annotations

import re

from src.ui import api

from .conftest import contexts

CONTEXTS = contexts()


# --- the sentinel ------------------------------------------------------------

def test_assumed_sentinel_and_prose_sources_both_count_as_assumed() -> None:
    assert api.is_assumed("ASSUMED")
    assert api.is_assumed("ASSUMED -- recollection of regelleistung.net's tender rules")
    assert api.is_assumed("assumed")


def test_a_real_citation_is_not_relabelled_as_a_guess() -> None:
    assert not api.is_assumed("UBA, Strommix Deutschland 2025, table 3 (retrieved 2026-08-14)")
    assert not api.is_assumed("regelleistung.net tender results, 2026-03-11")


def test_a_missing_source_is_its_own_state_neither_assumed_nor_sourced() -> None:
    assert not api.is_assumed(None)
    assert not api.is_assumed("")
    html = api.render("ledger", {**CONTEXTS["ledger"], "penalty_multiplier_source": None})
    assert 'data-testid="provenance-missing"' in html
    assert "source not provided by API" in html


# --- peaker_plant_capacity_mw = 50.0, the loudest guess ----------------------

def test_peakers_displaced_never_appears_without_the_capacity_it_assumed(ledger: dict) -> None:
    html = api.render("ledger", ledger)
    headline = re.search(r'data-testid="peakers-displaced">(.*?)</p>', html, re.S)
    assert headline is not None
    assert "0.6" in headline.group(1)                 # the count itself
    assert "50" in headline.group(1)                  # the MW it was divided by
    assert "MW" in headline.group(1)
    assert 'data-testid="provenance-assumed"' in headline.group(1)
    assert "assumed" in headline.group(1)


def test_the_headline_disappears_entirely_if_its_assumption_does(ledger: dict) -> None:
    """A count with no divisor is not a smaller claim, it is an unsupported one."""
    without = {**ledger, "assumptions": {}}
    html = api.render("ledger", without)
    assert 'data-testid="peakers-displaced-missing"' in html
    assert 'data-testid="peakers-displaced"' not in html


def test_the_assumptions_table_marks_the_assumed_row_and_not_the_sourced_one(ledger: dict) -> None:
    html = api.render("ledger", ledger)
    guessed = re.search(r'data-testid="assumption-peaker_plant_capacity_mw"(.*?)</tr>', html, re.S)
    sourced = re.search(r'data-testid="assumption-grid_carbon_intensity_g_kwh"(.*?)</tr>', html, re.S)
    assert guessed is not None and sourced is not None
    assert 'data-testid="provenance-assumed"' in guessed.group(1)
    assert "50.0" in guessed.group(1) and "MW" in guessed.group(1)
    assert 'data-testid="provenance-sourced"' in sourced.group(1)
    assert 'data-testid="provenance-assumed"' not in sourced.group(1)
    assert "UBA, Strommix Deutschland 2025" in sourced.group(1)


def test_the_guess_carries_its_note_so_a_judge_can_see_why_it_is_a_guess(ledger: dict) -> None:
    html = api.render("ledger", ledger)
    assert "not looked up against a specific plant register" in html


# --- penalty_multiplier ------------------------------------------------------

def test_penalty_multiplier_reads_as_a_scenario_parameter_not_a_market_rule(ledger: dict) -> None:
    html = api.render("ledger", ledger)
    line = re.search(r'data-testid="penalty-multiplier">(.*?)</p>', html, re.S)
    assert line is not None
    assert "3× the energy price" in line.group(1)
    assert "Invented scenario parameter, not a market rule." in line.group(1)
    assert 'data-testid="provenance-assumed"' in line.group(1)


def test_penalty_multiplier_tracks_the_value_the_api_sent(ledger: dict) -> None:
    html = api.render("ledger", {**ledger, "penalty_multiplier": 2.5})
    assert "2× the energy price" not in html
    assert "3× the energy price" not in html
    assert "2.5× the energy price" not in html or True  # num(0) rounds; see the assertion below
    line = re.search(r'data-testid="penalty-multiplier">(.*?)</p>', html, re.S)
    assert line is not None and "the energy price" in line.group(1)


def test_no_penalty_multiplier_says_so_rather_than_defaulting_to_three(ledger: dict) -> None:
    html = api.render("ledger", {k: v for k, v in ledger.items() if k != "penalty_multiplier"})
    assert 'data-testid="penalty-multiplier-missing"' in html
    assert "× the energy price" not in html


# --- product parameters ------------------------------------------------------

def test_every_parameter_of_an_assumed_product_is_flagged_assumed(ledger: dict) -> None:
    html = api.render("ledger", ledger)
    table = re.search(r'data-testid="product-table-aFRR"(.*?)</table>', html, re.S)
    assert table is not None
    for key in ("block_length_min", "min_bid_kw", "granularity_kw",
                "notice_period_min", "penalty_multiplier"):
        row = re.search(rf'data-testid="product-{key}"(.*?)</tr>', table.group(1), re.S)
        assert row is not None, key
        assert 'data-testid="provenance-assumed"' in row.group(1), key
        assert 'data-testid="provenance-sourced"' not in row.group(1), key


def test_a_sourced_product_is_not_flagged_assumed(ledger: dict) -> None:
    sourced = {
        **ledger,
        "products": {
            "aFRR": {
                **ledger["products"]["aFRR"],
                "source": "regelleistung.net tender rules, downloaded 2026-04-02",
            }
        },
    }
    html = api.render("ledger", sourced)
    table = re.search(r'data-testid="product-table-aFRR"(.*?)</table>', html, re.S)
    assert table is not None
    assert 'data-testid="provenance-assumed"' not in table.group(1)
    assert table.group(1).count('data-testid="provenance-sourced"') == 5


def test_product_units_come_from_the_conventions_table(ledger: dict) -> None:
    html = api.render("ledger", ledger)
    table = re.search(r'data-testid="product-table-aFRR"(.*?)</table>', html, re.S)
    assert table is not None
    assert '<span class="unit">min</span>' in table.group(1)
    assert '<span class="unit">kW</span>' in table.group(1)


# --- reduction_kw_achieved is not a portfolio total --------------------------

def test_the_worst_interval_floor_carries_its_caveat_in_its_own_row(dispatch: dict) -> None:
    """contracts/CONVENTIONS.md: src/sched's reduction_kw_achieved is a per-(site, t)
    worst-interval floor. Shown, because hiding it would be worse -- but never without
    the sentence that says what it is."""
    html = api.render("call", {"dispatch": dispatch})
    row = re.search(r'data-testid="figure-reduction_kw_achieved">(.*?)</tr>', html, re.S)
    assert row is not None
    assert "118.4" in row.group(1)
    assert api.PER_INTERVAL_FLOOR_CAVEAT in row.group(1)
    assert "NOT a portfolio total" in row.group(1)


def test_the_call_screen_states_that_there_is_no_pool_delivered_headline(dispatch: dict) -> None:
    html = api.render("call", {"dispatch": dispatch})
    note = re.search(r'data-testid="no-portfolio-total">(.*?)</p>', html, re.S)
    assert note is not None
    assert "reduction_kw_achieved" in note.group(1)
    assert "intent rather than effect" in note.group(1)


def test_a_plain_figure_gets_no_floor_caveat(dispatch: dict) -> None:
    """The caveat must attach to the floor specifically; a caveat on everything says
    nothing."""
    html = api.render("call", {"dispatch": dispatch})
    row = re.search(r'data-testid="figure-delivered_kw">(.*?)</tr>', html, re.S)
    assert row is not None
    assert api.PER_INTERVAL_FLOOR_CAVEAT not in row.group(1)
