from __future__ import annotations

import pytest

from src.ui import api


@pytest.mark.parametrize("page", ["landing", "simulator"])
def test_cinematic_pages_use_local_assets(page: str) -> None:
    html = api.render_page(page)
    assert "/static/app.css" in html
    assert "/static/vendor/three.module.js" in html
    assert "unpkg.com" not in html
    for forbidden in ("1,467", "1467", "1305", "75%"):
        assert forbidden not in html


def test_unknown_page_is_rejected() -> None:
    with pytest.raises(ValueError):
        api.render_page("dashboard")
