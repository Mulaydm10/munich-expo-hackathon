"""A cached scenario must not outlive the inputs it was computed from (issue #48.1).

`ScenarioSpec.id` is the hash of the *request*. Nothing in it moves when the tables
underneath move, so before this guard existed the moment real data landed every
scenario cached during the synthetic era stayed warm and kept serving pre-real numbers
with no signal at all -- not a slow answer, a confidently wrong one.

These tests pin the guard at both ends: a changed table must miss, and an *unchanged*
one must still hit. The second half matters as much as the first. A fingerprint that
moved on every call (a timestamp, a file mtime) would also make the first test pass,
while quietly turning the cache off and blowing the contract's warm-dispatch budget.
"""

from __future__ import annotations

import json

import pytest

from src.service import _cache as cache
from src.service._pipeline import CANONICAL_TABLES

from .conftest import DAY, build_root, prices_frame, span_index, write_table


def _doc(scenario_id: str = "abc123") -> dict:
    """The minimum `ScenarioResult.from_doc` reads, so a route can serve it."""
    return {
        "result": {
            "id": scenario_id,
            "spec": {"date": DAY, "n_sites": 2, "seed": 7},
            "totals": {"capacity_revenue_eur": 1234.0},
            "scorecard": None,
            "calibration": {},
            "warnings": [],
        }
    }


def test_a_changed_table_is_not_served_from_cache(tmp_path):
    """The defect itself: same spec id, different tables underneath."""
    root = build_root(tmp_path / "root")
    cache.write("abc123", _doc(), root)
    assert cache.read("abc123", root) is not None, "a fresh write must be readable"

    # the same table rebuilt from a different source -- what wiring real data does.
    # A real rebuild goes through `canonicalise()`, which rewrites the provenance
    # sidecar; here the row count moves, as it does the moment a fixture-shaped table
    # is replaced by a real German one.
    write_table(root, "prices", prices_frame(span_index()).iloc[:-4])

    assert cache.read("abc123", root) is None, (
        "a scenario computed against the previous `prices` table was served after the "
        "table changed -- these are the pre-real numbers issue #48.1 is about"
    )


def test_an_unchanged_root_still_hits(tmp_path):
    """The other end: rewriting a table with identical content must NOT invalidate.

    Provenance is identity, not freshness. Re-running `canonicalise()` over unchanged
    raw files is a no-op on the sidecar, so the fingerprint must not move either -- if
    it did, every warm read would miss and the <500 ms warm guarantee would go with it.
    """
    root = build_root(tmp_path / "root")
    cache.write("abc123", _doc(), root)
    before = cache.inputs_fingerprint(root)

    write_table(root, "prices", prices_frame(span_index()))  # byte-identical rebuild

    assert cache.inputs_fingerprint(root) == before
    assert cache.read("abc123", root) is not None, "an unchanged root must still hit"


def test_a_table_appearing_changes_the_fingerprint(tmp_path):
    """"no `carbon` table" and "a `carbon` table" are different worlds.

    An absent table is part of the identity, not an absence of identity: a scenario run
    with carbon missing reports `co2_kg_saved` as null, and that answer must not be
    served once the table exists.
    """
    without = cache.inputs_fingerprint(build_root(tmp_path / "without", carbon=False))
    with_ = cache.inputs_fingerprint(build_root(tmp_path / "with", carbon=True))
    assert without != with_


def test_a_document_written_before_the_fingerprint_existed_is_a_miss(tmp_path):
    """Documents already on disk were computed against inputs nobody recorded."""
    root = build_root(tmp_path / "root")
    path = cache.cache_path("legacy1", root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_doc("legacy1")), encoding="utf-8")  # no INPUTS_KEY

    assert cache.read("legacy1", root) is None


def test_write_does_not_mutate_the_callers_document(tmp_path):
    root = build_root(tmp_path / "root")
    doc = _doc()
    cache.write("abc123", doc, root)
    assert cache.INPUTS_KEY not in doc


def test_the_read_back_after_a_write_is_not_verified(tmp_path):
    """`verify_inputs=False` is the write-then-read-back path only."""
    root = build_root(tmp_path / "root")
    cache.write("abc123", _doc(), root)
    write_table(root, "prices", prices_frame(span_index()).iloc[:-4])

    assert cache.read("abc123", root) is None
    assert cache.read("abc123", root, verify_inputs=False) is not None


def test_the_pipeline_and_the_cache_agree_on_which_tables_matter():
    """Two hand-maintained copies of this list would eventually disagree, and the
    disagreement would be a table that can change a number but not invalidate it."""
    assert CANONICAL_TABLES == cache.FINGERPRINTED_TABLES


def test_a_route_does_not_serve_a_scenario_whose_inputs_moved(tmp_path, client_on):
    """The defect through the HTTP surface, which is where a judge would meet it."""
    root = build_root(tmp_path / "root")
    cache.write("abc123", _doc(), root)

    client = client_on(root)
    assert client.get("/api/scenario/abc123").status_code == 200

    write_table(root, "prices", prices_frame(span_index()).iloc[:-4])

    response = client.get("/api/scenario/abc123")
    assert response.status_code == 404, (
        "the route served a scenario computed against tables that have since changed"
    )


def test_identity_comes_from_the_provenance_sidecar_not_the_parquet_bytes(tmp_path):
    """The boundary of this guard, pinned so it is a decision and not a surprise.

    A table's identity is its sidecar (`src.data.meta`), so a parquet edited in place
    while its sidecar is left untouched does NOT invalidate the cache. That is
    acceptable here for one reason and it is worth being able to check: `_write_canonical`
    is the only writer of a canonical table, and it always writes the parquet and its
    sidecar together. Reading six sidecars on every warm request is cheap; hashing six
    parquet files is not, and the contract budgets warm dispatch at <500 ms.

    If a second writer of canonical tables ever appears, this test is the one that
    should start failing.
    """
    root = build_root(tmp_path / "root")
    before = cache.inputs_fingerprint(root)

    frame = prices_frame(span_index()).assign(price_eur_mwh=99.0)
    frame.to_parquet(root / "canonical" / "prices.parquet", index=False)  # sidecar untouched

    assert cache.inputs_fingerprint(root) == before


# ---------------------------------------------------------------------------
# a document must never be stamped with a fingerprint it was not computed under
# (Qodo review on PR #61, findings 3 and 5)
# ---------------------------------------------------------------------------


def test_a_table_changing_during_the_build_does_not_get_stamped_as_valid(tmp_path):
    """The mirror of the defect this PR fixes, and just as silent.

    `pipeline.build` reads the tables, then the document is written. A rebuild landing
    in that window would stamp the *new* identity onto numbers computed from the *old*
    tables -- a document that then validates for the rest of its life. Nothing is
    cached rather than caching a lie.
    """
    root = build_root(tmp_path / "root")
    before = cache.inputs_fingerprint(root)

    write_table(root, "prices", prices_frame(span_index()).iloc[:-4])  # landed mid-build

    assert cache.write("abc123", _doc(), root, expect_fingerprint=before) is None
    assert cache.read("abc123", root) is None
    assert not cache.cache_path("abc123", root).exists()


def test_a_build_on_unchanged_tables_still_caches(tmp_path):
    """The other end again: the guard must not refuse every write."""
    root = build_root(tmp_path / "root")
    before = cache.inputs_fingerprint(root)

    assert cache.write("abc123", _doc(), root, expect_fingerprint=before) is not None
    assert cache.read("abc123", root) is not None


def test_unreadable_provenance_never_compares_equal_to_itself(tmp_path):
    """Provenance that cannot be read must not become a reusable identity.

    Collapsing every unreadable sidecar to its exception class gives two *different*
    unknown states the same fingerprint, so a document written while a sidecar was
    corrupt validates later while it is still corrupt -- asserting validity exactly
    where provenance is unknown, which is the one thing this must never do.
    """
    root = build_root(tmp_path / "root")
    (root / "canonical" / "prices.meta.json").write_text("{not json", encoding="utf-8")

    assert cache.inputs_fingerprint(root) != cache.inputs_fingerprint(root)


def test_a_scenario_written_while_provenance_is_unreadable_is_never_served(tmp_path):
    root = build_root(tmp_path / "root")
    (root / "canonical" / "carbon.meta.json").write_text("{not json", encoding="utf-8")

    cache.write("abc123", _doc(), root)
    assert cache.read("abc123", root) is None, (
        "a scenario was served on the strength of provenance nothing could read"
    )
