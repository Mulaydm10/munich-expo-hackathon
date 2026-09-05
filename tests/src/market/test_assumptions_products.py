"""ASSUMPTIONS / PRODUCTS provenance: acceptance bullet "no entry has an empty source"."""

from src.market import api


def test_every_assumption_has_a_nonempty_source_and_units():
    assert len(api.ASSUMPTIONS) > 0
    for key, a in api.ASSUMPTIONS.items():
        assert a.source, f"ASSUMPTIONS[{key!r}] has an empty source"
        assert a.unit, f"ASSUMPTIONS[{key!r}] has no unit"


def test_assumption_construction_rejects_empty_source():
    import pytest
    with pytest.raises(ValueError):
        api.Assumption(value=1.0, unit="kW", source="")


def test_every_product_has_a_nonempty_source():
    assert set(api.PRODUCTS) == {"aFRR", "mFRR"}
    for name, p in api.PRODUCTS.items():
        assert p.source, f"PRODUCTS[{name!r}] has an empty source"


def test_product_construction_rejects_empty_source():
    import pytest
    with pytest.raises(ValueError):
        api.Product(
            name="x", block_length_min=240, min_bid_kw=1000, granularity_kw=1000,
            notice_period_min=5, penalty_multiplier=3.0, source="",
        )
