from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROTOTYPE = ROOT / "experiments" / "product-discovery" / "ai-native-production-gate"


def test_product_discovery_surface_is_isolated_and_complete() -> None:
    required = {
        "index.html",
        "styles.css",
        "scenario.json",
        "src/app.mjs",
        "src/model.mjs",
        "design/desktop-concept.png",
        "design/mobile-concept.png",
    }
    assert required <= {
        path.relative_to(PROTOTYPE).as_posix()
        for path in PROTOTYPE.rglob("*")
        if path.is_file()
    }
    html = (PROTOTYPE / "index.html").read_text(encoding="utf-8")
    assert "/studio/" not in html
    assert "src/app.mjs" in html


def test_scenario_freezes_provider_free_same_task_truth() -> None:
    scenario = json.loads((PROTOTYPE / "scenario.json").read_text(encoding="utf-8"))
    story = "\n\n".join(scenario["story_blocks"])
    assert 2000 <= len(story) <= 5000
    assert scenario["simulation"] is True
    assert scenario["provider_dispatch_count"] == 0
    assert len(scenario["plan_tasks"]) == 3
    assert [task["seed_execution_state"] for task in scenario["plan_tasks"]] == [
        "completed",
        "waiting-human",
        "running",
    ]
    assert len(scenario["shots"]) == 15


def test_ui_exposes_required_creator_truth_without_model_prerequisites() -> None:
    app = (PROTOTYPE / "src" / "app.mjs").read_text(encoding="utf-8")
    model = (PROTOTYPE / "src" / "model.mjs").read_text(encoding="utf-8")
    css = (PROTOTYPE / "styles.css").read_text(encoding="utf-8")

    for phrase in (
        "模拟执行 · 未调用 Provider",
        "批准计划并启动 3 条任务",
        "等待人工决策",
        "暂停任务",
        "从检查点重试",
        "镜头 8 默认冻结",
        "缺少 25 项真实素材",
    ):
        assert phrase in app

    for state in (
        '"queued"',
        '"running"',
        '"waiting-human"',
        '"retrying"',
        '"blocked"',
        '"completed"',
        '"cancelled"',
    ):
        assert state in model

    assert 'provider_dispatch_count: 0' in model
    assert "shot-008@shot-008-v1" in model
    assert "@media (max-width: 620px)" in css
    assert "min-width: 320px" in css
