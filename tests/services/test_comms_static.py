from pathlib import Path


INDEX_HTML = Path("services/comms/static/index.html").read_text()


def test_mobile_backstage_uses_floating_toggle_and_right_drawer():
    assert 'id="backstage-fab"' in INDEX_HTML
    assert 'translateX(100%)' in INDEX_HTML
    assert "touchstart" in INDEX_HTML
    assert "touchend" in INDEX_HTML


def test_mobile_pending_items_have_main_stage_container():
    assert 'id="mobile-sticky"' in INDEX_HTML
    assert "renderPendingSurface()" in INDEX_HTML
    assert "isMobileLayout() ? mobileSticky : backstageSticky" in INDEX_HTML
