from pathlib import Path


def test_frontend_does_not_place_raw_item_url_in_href():
    """API/crawler URLs can be untrusted; href needs protocol validation, not only escaping."""
    js = Path("app/static/app.js").read_text(encoding="utf-8")

    assert 'href="${esc(item.url)}"' not in js
    assert "javascript:" not in js.lower()
