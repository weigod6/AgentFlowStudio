from __future__ import annotations

from pathlib import Path


SITE_ROOT = Path("apps/site")


def test_site_homepage_is_distinct_from_studio_workspace() -> None:
    index = (SITE_ROOT / "index.html").read_text(encoding="utf-8")

    assert SITE_ROOT.exists()
    assert '<html lang="zh-CN">' in index
    assert "<title>AFS Studio" in index
    assert 'class="brand" href="/site/"' in index
    assert 'href="/studio/"' in index
    assert 'href="/site/social-square.html"' not in index
    assert 'href="/site/styles/site.css"' in index
    assert 'href="/site/styles/site-preview.css"' in index
    assert 'href="/site/styles/social-square.css"' not in index
    assert 'href="/site/styles/site-responsive.css"' in index
    assert 'src="/site/site.js"' in index
    assert 'src="/site/social-square.js"' not in index
    assert "data-auth-action" in index
    assert "Agent-native Creative Workspace" not in index
    assert "进入制作工作空间" in index
    assert "hero-visual" in index
    assert "studio-wall" in index
    assert "wall-node director" in index
    assert "Production Spine" not in index
    assert "制作团队" in index
    assert "主创决策" in index
    assert "审核与交付" in index
    assert "社交广场" not in index
    assert "Runtime Service" not in index
    assert 'class="algorithm-section"' not in index
    assert "provider raw" not in index


def test_site_homepage_styles_remain_small_and_safe() -> None:
    style_paths = sorted((SITE_ROOT / "styles").glob("*.css"))
    combined = "\n".join(path.read_text(encoding="utf-8") for path in style_paths).lower()

    assert {path.name for path in style_paths} == {
        "site.css",
        "site-preview.css",
        "site-responsive.css",
        "social-square.css",
    }
    for path in style_paths:
        assert len(path.read_text(encoding="utf-8").splitlines()) <= 300, path

    for marker in ("api_key", "token", "signed_url", "provider raw", "d:\\", "c:\\"):
        assert marker not in combined


def test_site_homepage_auth_entry_script_is_safe_and_status_only() -> None:
    script = (SITE_ROOT / "site.js").read_text(encoding="utf-8")

    assert 'fetch("/auth/status"' in script
    assert "data-auth-action" in script
    assert "entryLabel" in script
    assert "afs_auth_session_token" in script
    assert "Authorization" in script
    assert "http://" not in script
    assert "https://" not in script
    assert "provider raw" not in script.lower()


def test_site_homepage_preview_uses_non_overlapping_flow_layout() -> None:
    index = (SITE_ROOT / "index.html").read_text(encoding="utf-8")
    preview = (SITE_ROOT / "styles" / "site-preview.css").read_text(encoding="utf-8")
    responsive = (SITE_ROOT / "styles" / "site-responsive.css").read_text(encoding="utf-8")

    assert "hero-visual" in index
    assert "studio-wall" in preview
    assert ".wall-node.director" in preview
    assert ".director-teaser" in (SITE_ROOT / "styles" / "site.css").read_text(encoding="utf-8")
    assert "position: absolute;" in preview
    assert "left: 46px;" not in preview
    assert "grid-template-columns: 1fr;" in responsive
    assert ".hero-visual" in responsive
    assert ".square-dashboard" in responsive
