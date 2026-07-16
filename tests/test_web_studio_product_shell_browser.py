from __future__ import annotations

from pathlib import Path


STUDIO = Path("apps/studio")


def test_product_shell_is_chinese_first_and_hides_diagnostics_from_primary_flow() -> None:
    shell = (STUDIO / "src" / "product-shell.js").read_text(encoding="utf-8")
    i18n = (STUDIO / "src" / "i18n.js").read_text(encoding="utf-8")
    index = (STUDIO / "index.html").read_text(encoding="utf-8")

    for label in ("工作空间", "项目", "单集", "制作团队", "审核", "交付", "制作总览", "待主创决策", "剧组动态", "交付准备度"):
        assert label in i18n
    assert 'return localStorage.getItem(STORAGE_KEY) === "en" ? "en" : "zh-CN"' in i18n
    assert "runtime-status" not in shell
    assert "provider" not in shell.lower()
    assert "raw" not in shell.lower()
    assert "json" not in shell.lower()
    assert "新建项目" in shell
    assert "onCreateProject" in shell
    assert "? 40 : 0" in shell
    assert './styles/product-shell.css' in index


def test_mobile_shell_has_no_canvas_mount_and_no_horizontal_page_overflow_contract() -> None:
    main = (STUDIO / "src" / "main.js").read_text(encoding="utf-8")
    styles = (STUDIO / "styles" / "product-shell.css").read_text(encoding="utf-8")

    assert 'editorMounted = !window.matchMedia("(max-width: 760px)").matches;' in main
    assert "if (editorMounted) {" in main
    assert "@media (max-width: 760px)" in styles
    assert "html, body { max-width: 100%; overflow-x: clip; }" in styles
    assert "#studio-editor-shell { display: none !important; }" in styles
    assert "grid-template-columns: repeat(4, 1fr)" in styles
    assert "min-height: 52px" in styles


def test_product_shell_exposes_loading_empty_error_recovery_and_focus_states() -> None:
    main = (STUDIO / "src" / "main.js").read_text(encoding="utf-8")
    shell = (STUDIO / "src" / "product-shell.js").read_text(encoding="utf-8")
    styles = (STUDIO / "styles" / "product-shell.css").read_text(encoding="utf-8")

    for state in ('statePanel("loading")', 'statePanel("error")', 'statePanel("empty")'):
        assert state in shell
    assert 'document.getElementById("product-main")?.focus()' in shell
    assert 'setAttribute("aria-current"' in shell
    assert 'setAttribute("aria-label"' in shell
    assert "if (hasActiveProject())" in main
    assert "function hasActiveProject()" in main
    assert ":focus-visible" in styles
    assert "@media (prefers-reduced-motion: reduce)" in styles
