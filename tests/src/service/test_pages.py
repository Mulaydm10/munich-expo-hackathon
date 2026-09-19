from __future__ import annotations


def test_cinematic_pages_and_assets(client):
    for path in ("/", "/simulator"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
    assert client.get("/static/app-api.js").status_code == 200
    assert client.get("/static/vendor/three.module.js").status_code == 200
    source = client.get("/static/app-api.js").text
    assert "/api/scenario" in source
    assert "mock/" not in source
