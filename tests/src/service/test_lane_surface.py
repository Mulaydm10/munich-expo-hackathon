"""Baseline for lane src/service.

Asserts only that the lane's single public module is importable and self-identifying —
enough for `docs/verify.txt` to be green on a clean checkout with no data downloaded.
Replace or extend as the contract gets implemented; do not delete this file, it is what
keeps an empty lane from reporting a passing suite of zero tests.
"""

from src.service import api


def test_lane_module_is_importable_and_names_itself() -> None:
    assert api.LANE == "src/service"
