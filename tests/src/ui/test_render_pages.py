from __future__ import annotations

import pytest

from src.ui import api


@pytest.mark.parametrize("page", ["landing", "simulator"])
def test_cinematic_pages_use_local_assets(page: str) -> None:
    html = api.render_page(page)
    assert "/static/app.css" in html
    assert "/static/vendor/three.module.js" in html
    assert "/static/vendor/gsap.min.js" in html
    assert "unpkg.com" not in html
    if page == "landing":
        assert "/static/vendor/ScrollTrigger.min.js" in html
        assert "/static/app-motion.js" in html
    else:
        assert "/static/app-control.js" in html
    for forbidden in ("1,467", "1467", "1305", "75%"):
        assert forbidden not in html
    for vendor in ("gsap.min.js", "ScrollTrigger.min.js"):
        path = api.STATIC_DIR / "vendor" / vendor
        assert path.is_file()
        assert path.stat().st_size > 10_000


def test_unknown_page_is_rejected() -> None:
    with pytest.raises(ValueError):
        api.render_page("dashboard")
