"""peakers_displaced(): the loudest claim the project makes must be inspectable in one click."""

import pytest

from src.market import api


def test_peakers_displaced_names_its_assumption():
    count, assumption_str = api.peakers_displaced(150.0)
    a = api.ASSUMPTIONS["peaker_plant_capacity_mw"]
    assert count == pytest.approx(150.0 / a.value)
    assert "peaker_plant_capacity_mw" in assumption_str
    assert a.source in assumption_str


def test_peakers_displaced_scales_linearly():
    c1, _ = api.peakers_displaced(50.0)
    c2, _ = api.peakers_displaced(100.0)
    assert c2 == pytest.approx(2 * c1)
