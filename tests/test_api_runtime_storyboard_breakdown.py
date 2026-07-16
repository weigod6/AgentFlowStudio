from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from agentflow_studio.model_gateway.errors import ModelProviderError
from apps.api.openapi_export import export_openapi_schema
from apps.api.runtime_errors import response_contains_unsafe_marker
from apps.api.runtime_service import create_runtime_app
from apps.api.runtime_storyboard_local import local_storyboard_shots


def test_storyboard_breakdown_gate_closed_uses_safe_local_fallback(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("AFS_ALLOW_REMOTE_LLM", raising=False)
    client = TestClient(create_runtime_app(runtime_root=tmp_path))
    client.post(
        "/projects",
        json={
            "project_id": "proj_storyboard_gate_closed",
            "project_type": "short_video_campaign",
            "goal": "Create a visual story from a complete script.",
        },
    )

    response = client.post(
        "/projects/proj_storyboard_gate_closed/storyboard-breakdowns",
        json={
            "node_id": "text_001",
            "script_text": "机器人站在城市屋顶看星星。它低头检查手里的发光地图。远处霓虹灯亮起。",
            "target_platform": "short_video",
            "style": "cinematic",
            "generated_at": "2026-06-22T10:00:00+08:00",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    serialized = json.dumps(payload, ensure_ascii=False).lower()

    assert payload["job"]["action"] == "storyboard_breakdown"
    assert payload["provider_calls_started"] is False
    assert payload["safe_manifest"]["status"] == "local_fallback"
    assert payload["safe_manifest"]["fallback_visible_to_user"] is True
    assert payload["safe_manifest"]["fallback_reason"] == "llm_gate_blocked"
    assert "LLM gate" in payload["safe_manifest"]["fallback_message"]
    assert payload["safe_manifest"]["raw_provider_response_stored"] is False
    assert payload["safe_manifest"]["asset_nodes_created"] is False
    assert payload["safe_manifest"]["knowledgebase_version"] == "creative_prompt_knowledgebase_v1"
    assert "storyboard_shot_numbering_handoff_v1" in payload["safe_manifest"]["knowledge_rule_ids"]
    assert len(payload["shots"]) >= 1
    first = payload["shots"][0]
    for field in ("shot_id", "index", "duration", "description", "shot_size", "light_atmosphere", "camera_motion", "asset_refs"):
        assert field in first
    assert any(asset["label"] for asset in first["asset_refs"])
    assert "api_key" not in serialized
    assert "bearer " not in serialized
    assert "d:\\" not in serialized
    assert response_contains_unsafe_marker(payload) is False


def test_storyboard_local_fallback_does_not_turn_signal_or_city_lights_into_props(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("AFS_ALLOW_REMOTE_LLM", raising=False)
    client = TestClient(create_runtime_app(runtime_root=tmp_path))
    project_id = "proj_storyboard_local_asset_boundaries"
    client.post(
        "/projects",
        json={
            "project_id": project_id,
            "project_type": "short_video_campaign",
            "goal": "Create a visual story from a complete script.",
        },
    )

    response = client.post(
        f"/projects/{project_id}/storyboard-breakdowns",
        json={
            "node_id": "text_001",
            "script_text": "未来机器人站在城市屋顶仰望星空，远处高楼灯火闪烁，像是在等待遥远信号。",
            "target_platform": "short_video",
            "style": "cinematic",
            "generated_at": "2026-06-22T10:03:00+08:00",
        },
    )

    assert response.status_code == 200
    refs = response.json()["shots"][0]["asset_refs"]
    assert [item["asset_type"] for item in refs] == ["character", "scene"]
    assert [item["label"] for item in refs] == ["未来机器人", "夜晚城市屋顶"]


def test_storyboard_local_fallback_uses_adaptive_shot_count_not_three_sentence_chunks() -> None:
    script = "镜头一。镜头二。镜头三。镜头四。镜头五。镜头六。镜头七。镜头八。镜头九。"

    shots = local_storyboard_shots(script)

    assert len(shots) == 5
    assert shots[0]["shot_id"] == "shot_01"
    assert shots[-1]["shot_id"] == "shot_05"


def test_storyboard_local_fallback_adds_source_grounding_and_asset_evidence() -> None:
    script = "未来机器人站在农村屋顶仰望星空。毛绒头部外壳被月光照亮。远处村庄灯火慢慢熄灭。"

    shots = local_storyboard_shots(script)

    assert shots
    assert all(shot["source_span"]["text"] for shot in shots)
    assert all(shot["source_span"]["grounding_status"] == "source_grounded" for shot in shots)
    assert all(shot["unsupported_additions"] == [] for shot in shots)
    assert all(shot["planning_agent"]["dynamic_shot_count"] is True for shot in shots)
    first_refs = shots[0]["asset_refs"]
    assert all(ref["evidence_text"] for ref in first_refs)
    assert all(isinstance(ref["confidence"], float) for ref in first_refs)


def test_storyboard_local_fallback_does_not_treat_bluestone_steps_as_battlefield() -> None:
    script = (
        "片名：《猫捡到狗那天》小明蹲在老城区巷口的青石台阶上，指尖沾着猫粮碎屑，"
        "正给蜷在纸箱里的橘猫顺毛。夕阳斜切过窄巷高墙，在青砖地面投下细长影子。"
    )

    shots = local_storyboard_shots(script)
    serialized = json.dumps(shots, ensure_ascii=False)
    first_refs = {(ref["label"], ref["asset_type"]) for ref in shots[0]["asset_refs"]}

    assert "山巅石台战场" not in serialized
    assert ("老城区巷口", "scene") in first_refs


def test_storyboard_local_fallback_keeps_named_people_animals_and_aliases_out_of_dialogue() -> None:
    script = (
        "片名：《猫捡到狗那天》 小明蹲在老城区巷口的青石台阶上，指尖沾着猫粮碎屑，"
        "怀里橘猫“煤球”正用肉垫按他手腕——它刚叼回一只湿漉漉的奶狗，狗耳朵还滴着水，"
        "爪子悬在半空蹬踹。阳光斜切过晾衣绳，把猫耳尖和狗鼻头照得发亮。"
        "小明喉结滚动，手指僵在半空，没敢碰那团颤抖的温热；"
        "他目光从奶狗缺耳的左耳滑向煤球绷紧的后颈。"
    )

    shots = local_storyboard_shots(script)
    first = shots[0]
    first_refs = {(ref["label"], ref["asset_type"]) for ref in first["asset_refs"]}
    refs_by_label = {ref["label"]: ref for ref in first["asset_refs"]}

    assert first["dialogue"] == "无明确对白"
    assert ("小明", "character") in first_refs
    assert ("煤球", "character") in first_refs
    assert ("奶狗", "character") in first_refs
    assert ("老城区巷口", "scene") in first_refs
    assert refs_by_label["煤球"]["character_subtype"] == "animal"
    assert refs_by_label["奶狗"]["character_subtype"] == "animal"


def test_storyboard_local_fallback_still_recognizes_mountain_battlefield() -> None:
    script = "孙悟空大战金刚狼，破碎山巅石台上云雾翻卷。孙悟空手持金箍棒向前压低身形。"

    shots = local_storyboard_shots(script)
    refs = {(ref["label"], ref["asset_type"]) for shot in shots for ref in shot["asset_refs"]}

    assert ("山巅石台战场", "scene") in refs


def test_storyboard_local_fallback_does_not_fabricate_generic_people_or_mountain_scene_for_ancient_battlefield() -> None:
    script = (
        "《断戟惊雷》\n"
        "暴雨如注，古战场泥泞翻涌。沈砚单膝陷在泥中，右臂青筋暴起，死攥半截断戟，指节泛白如骨；"
        "左肩甲裂开一道焦痕，血混着雨水蜿蜒淌进衣领褶皱深处。他抬头刹那，残旗在狂风中撕扯拍打。\n"
        "远处焦黑城墙被惨白雷光劈亮，砖石崩塌的轮廓在电光中一闪而逝。沈砚瞳孔骤缩："
        "城垛缺口处，一袭素白衣影静立如碑，未持兵刃，只捧一卷湿透竹简。\n"
        "他喉结剧烈滚动，下颌绷紧欲吼，却只呛出一口黑血——血色浓稠发暗，顺下颌滴入泥水，"
        "漾开蛛网状墨痕。这不是新伤，是三年前那杯毒酒终于蚀穿肝胆的证印。"
    )

    shots = local_storyboard_shots(script)
    serialized = json.dumps(shots, ensure_ascii=False)
    refs = {(ref["label"], ref["asset_type"]) for shot in shots for ref in shot["asset_refs"]}

    assert "可见人物" not in serialized
    assert "山巅石台战场" not in serialized
    assert "@可见人物" not in serialized
    assert "@山巅石台战场" not in serialized
    assert ("沈砚", "character") in refs
    assert ("古战场", "scene") in refs
    assert all(not str(shot["description"]).startswith("@") for shot in shots)


def test_storyboard_local_fallback_reconciles_pronoun_assets_without_future_props() -> None:
    script = (
        "暴雨如注，古战场泥泞翻涌，沈砚单膝陷在泥中，死攥半截断戟。"
        "他喉结剧烈滚动，下颌绷紧欲吼，却只呛出一口黑血。"
        "他咬牙撑戟欲起，断戟忽震，戟尖泥下赫然露出半枚青铜虎符。"
    )

    shots = local_storyboard_shots(script, shot_count_hint=3)
    shot1_refs = {(ref["label"], ref["asset_type"]) for ref in shots[0]["asset_refs"]}
    shot2_refs = {(ref["label"], ref["asset_type"]) for ref in shots[1]["asset_refs"]}
    shot3_refs = {(ref["label"], ref["asset_type"]) for ref in shots[2]["asset_refs"]}

    assert ("沈砚", "character") in shot1_refs
    assert ("古战场", "scene") in shot1_refs
    assert ("断戟", "prop") in shot1_refs
    assert ("青铜虎符", "prop") not in shot1_refs
    assert ("沈砚", "character") in shot2_refs
    assert ("古战场", "scene") in shot2_refs
    assert ("沈砚", "character") in shot3_refs
    assert ("断戟", "prop") in shot3_refs
    assert ("青铜虎符", "prop") in shot3_refs


def test_storyboard_local_fallback_resolves_animal_and_prop_coreference_generically() -> None:
    script = (
        "小华蹲在梧桐树影斑驳的公园长椅旁，指尖捏着半块没吃完的面包。"
        "一只黑色拉布拉多突然从斜坡草甸冲下，嘴里牢牢叼着一只磨损严重的荧光绿网球。"
        "那狗直直朝她奔来，在距她拖鞋鞋尖三十厘米处骤然刹住，球被轻轻吐在拖鞋边。"
    )

    shots = local_storyboard_shots(script, shot_count_hint=3)
    shot2_refs = {ref["label"]: ref for ref in shots[1]["asset_refs"]}
    shot3_refs = {ref["label"]: ref for ref in shots[2]["asset_refs"]}

    assert "小华" in {ref["label"] for ref in shots[0]["asset_refs"]}
    assert "黑色拉布拉多" in shot2_refs
    assert shot2_refs["黑色拉布拉多"]["character_subtype"] == "animal"
    assert "斜坡草甸" in {ref["label"] for ref in shots[1]["asset_refs"] if ref["asset_type"] == "scene"}
    assert "荧光绿网球" in {ref["label"] for ref in shots[1]["asset_refs"] if ref["asset_type"] == "prop"}
    assert "黑色拉布拉多" in shot3_refs
    assert shot3_refs["黑色拉布拉多"]["source"] in {"context", "cross_shot_coreference"}
    assert "荧光绿网球" in {ref["label"] for ref in shots[2]["asset_refs"] if ref["asset_type"] == "prop"}


def test_storyboard_local_fallback_rejects_action_fragments_and_future_asset_leaks() -> None:
    script = (
        "片名：《捡到一只狗》 小明蹲在老城区巷口的青石台阶上，指尖沾着猫毛，"
        "正给怀里的橘猫顺毛。夕阳斜切过砖墙，把暖金色泼在青苔斑驳的石缝间，"
        "也镀亮猫尾尖——它懒洋洋扫过他洗得发白的牛仔裤边沿。\n"
        "这是他每天放学后最安静的十分钟：呼吸放慢，肩膀松弛，连睫毛垂落的弧度"
        "都带着一种被时间允许的倦意。突然，橘猫脊背一绷，耳朵旋成两个尖锐的三角，"
        "喉咙里滚出低沉而警惕的呼噜声。\n"
        "它挣脱怀抱，四爪无声落地，转身轻巧跃下三级台阶，叼回一只浑身湿漉漉、"
        "耳朵耷拉、项圈锈迹斑斑的土狗幼崽。小狗四肢僵直，爪子还死死勾着半截断绳，"
        "像刚从暴雨里捞出来的旧玩具，抖得几乎听不见心跳。\n"
        "小明愣住，瞳孔微缩，右手本能前伸——指尖悬停在离小狗鼻尖三寸处。"
        "他掏出手机，屏幕亮起却迟迟没有按下拍摄键。"
    )

    shots = local_storyboard_shots(script, shot_count_hint=4)
    labels_by_shot = [
        {(ref["label"], ref["asset_type"]) for ref in shot["asset_refs"]}
        for shot in shots
    ]
    all_labels = {label for labels in labels_by_shot for label, _asset_type in labels}

    assert len(shots) == 4
    assert {"它挣脱怀", "转身轻巧", "右眼", "他掏出手机"}.isdisjoint(all_labels)
    assert ("小明", "character") in labels_by_shot[0]
    assert ("橘猫", "character") in labels_by_shot[0]
    assert ("老城区巷口", "scene") in labels_by_shot[0]
    assert ("小狗", "character") in labels_by_shot[2]
    assert ("橘猫", "character") in labels_by_shot[2]
    assert ("项圈", "prop") in labels_by_shot[2]
    assert ("断绳", "prop") in labels_by_shot[2]
    assert all(("手机", "prop") not in labels for labels in labels_by_shot[:3])
    assert ("手机", "prop") in labels_by_shot[3]


def test_storyboard_breakdown_returns_asset_graph_with_cross_shot_evidence(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("AFS_ALLOW_REMOTE_LLM", raising=False)
    client = TestClient(create_runtime_app(runtime_root=tmp_path))
    project_id = "proj_storyboard_asset_graph"
    client.post("/projects", json={"project_id": project_id, "goal": "Build asset graph"})

    response = client.post(
        f"/projects/{project_id}/storyboard-breakdowns",
        json={
            "node_id": "text_001",
            "script_text": "未来机器人站在农村屋顶仰望星空。未来机器人抬起毛绒头部外壳。屋顶平台边缘映着远处村庄灯火。",
            "target_platform": "short_video",
            "style": "cinematic",
            "generated_at": "2026-06-28T11:00:00+08:00",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    graph = payload["asset_graph"]
    assets = {(item["label"], item["asset_type"]): item for item in graph["assets"]}

    assert graph["artifact_type"] == "agentflow_asset_graph"
    assert payload["safe_manifest"]["asset_graph_asset_count"] == len(graph["assets"])
    assert ("未来机器人", "character") in assets
    assert any(asset_type == "scene" for _label, asset_type in assets)
    robot = assets[("未来机器人", "character")]
    assert robot["graph_asset_id"].startswith("graph:character:")
    assert len(robot["shot_refs"]) >= 1
    assert all(item["text"] for item in robot["evidence_spans"])
    assert robot["review_state"] == "candidate_review_required"
    assert any(rel["relationship_type"] == "shot_contains_asset" for rel in graph["relationships"])
    assert graph["writes_long_term_memory"] is False
    assert graph["writes_company_kb"] is False


def test_storyboard_local_fallback_extracts_named_characters_scenes_and_dynamic_count() -> None:
    script = (
        "孙悟空大战金刚狼，破碎山巅石台上云雾翻卷。"
        "孙悟空手持金箍棒向前压低身形。"
        "金刚狼伸出钢爪迎面冲来。"
        "两人短兵相接，火花从金箍棒和钢爪之间迸出。"
        "孙悟空侧身跃起，金箍棒横扫。"
        "金刚狼后撤，脚下碎石飞溅。"
        "远处雷光照亮山脊。"
        "两人再次对峙，气氛压迫。"
        "孙悟空眼神锐利，金箍棒立在身侧。"
        "金刚狼低声咆哮，钢爪反射冷光。"
        "镜头拉远，山巅战场被云海包围。"
        "最后两人同时冲向对方。"
    )

    shots = local_storyboard_shots(script)
    refs = [ref for shot in shots for ref in shot["asset_refs"]]
    labels_by_type = {(ref["label"], ref["asset_type"]) for ref in refs}
    descriptions = "\n".join(shot["description"] for shot in shots)

    assert len(shots) > 5
    assert ("孙悟空", "character") in labels_by_type
    assert ("金刚狼", "character") in labels_by_type
    assert ("山巅石台战场", "scene") in labels_by_type
    assert ("金箍棒", "prop") in labels_by_type
    assert ("钢爪", "prop") in labels_by_type
    assert "@主角" not in descriptions
    assert "@主要场景" not in descriptions


def test_shot_asset_plan_endpoint_returns_principal_character_scene_and_key_prop_assets(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("AFS_ALLOW_REMOTE_LLM", raising=False)
    client = TestClient(create_runtime_app(runtime_root=tmp_path))
    project_id = "proj_shot_asset_plan"
    client.post(
        "/projects",
        json={
            "project_id": project_id,
            "project_type": "short_video_campaign",
            "goal": "Create a visual story from a complete script.",
        },
    )

    response = client.post(
        f"/projects/{project_id}/shot-asset-plans",
        json={
            "node_id": "shot_01",
            "shot": {
                "shot_id": "shot_01",
                "index": 1,
                "duration": "5s",
                "description": "@孙悟空 @金刚狼。以孙悟空大战金刚狼为核心，破碎山巅石台上云雾翻卷。孙悟空手持金箍棒，金刚狼压低身体迎面冲来。",
                "shot_size": "中景",
                "light_atmosphere": "自然光影，气氛服务情绪推进",
                "camera_motion": "固定机位，轻微呼吸感",
                "dialogue": "无明确对白",
                "sound": "环境底噪，动作音随画面同步",
                "asset_refs": [
                    {"label": "孙悟空", "asset_type": "character", "status": "mentioned", "source": "explicit"},
                    {"label": "金刚狼", "asset_type": "character", "status": "mentioned", "source": "explicit"},
                    {"label": "金箍棒", "asset_type": "prop", "status": "candidate", "source": "candidate"},
                ],
            },
            "script_text": "孙悟空大战金刚狼，破碎山巅石台上云雾翻卷。孙悟空手持金箍棒向前压低身形。",
            "generated_at": "2026-06-24T10:00:00+08:00",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    labels_by_type = {(item["label"], item["asset_type"]) for item in payload["asset_refs"]}

    assert payload["safe_manifest"]["status"] == "local_asset_plan"
    assert payload["safe_manifest"]["raw_provider_response_stored"] is False
    assert payload["asset_nodes_created"] is False
    assert ("孙悟空", "character") in labels_by_type
    assert ("金刚狼", "character") in labels_by_type
    assert ("山巅石台战场", "scene") in labels_by_type
    assert ("金箍棒", "prop") in labels_by_type
    assert not any(item.get("display_name") == "金箍棒" for item in payload["asset_graph"]["held_asset_refs"])
    assert all(item["evidence_text"] for item in payload["asset_refs"])
    assert response_contains_unsafe_marker(payload) is False


def test_storyboard_local_fallback_respects_line_based_script_units() -> None:
    script = "\n".join(
        f"{index}. 沈昭昭在暗办公室推进调查，镜头捕捉第{index}个动作节拍。"
        for index in range(1, 17)
    )

    shots = local_storyboard_shots(script)
    refs = [ref for shot in shots for ref in shot["asset_refs"]]

    assert len(shots) >= 12
    assert ("沈昭昭", "character") in {(ref["label"], ref["asset_type"]) for ref in refs}
    assert ("暗办公室", "scene") in {(ref["label"], ref["asset_type"]) for ref in refs}
    assert "@主要场景" not in "\n".join(shot["description"] for shot in shots)
    assert shots[0]["index"] == 1
    assert shots[-1]["index"] == len(shots)


def test_storyboard_breakdown_uses_llm_structured_json_when_gate_open(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AFS_ALLOW_REMOTE_LLM", "true")
    calls: list[object] = []

    class Descriptor:
        modality = "llm"

    class FakeRegistry:
        _descriptors = {"prompt_optimizer": Descriptor()}

        def dispatch(self, capability, service_id, request):
            calls.append(request)
            assert capability == "llm"
            assert service_id == "prompt_optimizer"
            return {
                "text": json.dumps(
                    {
                        "shots": [
                            {
                                "shot_id": "shot_01",
                                "index": 1,
                                "duration": "5s",
                                "description": "@主角 站在屋顶边缘，城市霓虹在身后打开。",
                                "shot_size": "远景",
                                "light_atmosphere": "蓝紫色霓虹与冷月光交错",
                                "camera_motion": "缓慢推近",
                                "dialogue": "无明确对白",
                                "sound": "城市低频环境音",
                                "asset_refs": [
                                    {"label": "主角", "asset_type": "character", "status": "mentioned", "source": "explicit"},
                                    {"label": "屋顶夜景", "asset_type": "scene", "status": "mentioned", "source": "explicit"},
                                ],
                            },
                            {
                                "shot_id": "shot_02",
                                "index": 2,
                                "duration": "6s",
                                "description": "@主角 展开发光地图，地图光照亮手部机械结构。",
                                "shot_size": "特写",
                                "light_atmosphere": "地图冷光作为主光，背景压暗",
                                "camera_motion": "固定机位，轻微呼吸感",
                                "dialogue": "无明确对白",
                                "sound": "电子脉冲声",
                                "asset_refs": [
                                    {"label": "主角", "asset_type": "character", "status": "mentioned", "source": "explicit"},
                                    {"label": "发光地图", "asset_type": "prop", "status": "mentioned", "source": "explicit"},
                                ],
                            },
                        ]
                    },
                    ensure_ascii=False,
                ),
                "provider_calls_started": True,
            }

    monkeypatch.setattr("apps.api.runtime_storyboard_breakdown.load_provider_registry", lambda: FakeRegistry())
    client = TestClient(create_runtime_app(runtime_root=tmp_path))
    client.post(
        "/projects",
        json={
            "project_id": "proj_storyboard_llm",
            "project_type": "short_video_campaign",
            "goal": "Create a visual story from a complete script.",
        },
    )

    response = client.post(
        "/projects/proj_storyboard_llm/storyboard-breakdowns",
        json={
            "node_id": "text_001",
            "script_text": "机器人站在城市屋顶看星星。它低头检查手里的发光地图。",
            "target_platform": "short_video",
            "style": "cinematic",
            "node_parameters": {"llm_provider": "prompt_optimizer"},
            "generated_at": "2026-06-22T10:05:00+08:00",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert len(calls) == 1
    provider_prompt = calls[0].prompt
    assert "专业知识库约束" in provider_prompt
    assert "显示字段语言约束" in provider_prompt
    assert "不要在显示字段输出英文摄影、光影、声音术语" in provider_prompt
    assert "主体优先约束" in provider_prompt
    assert "资产覆盖审计" in provider_prompt
    assert "回指判定" in provider_prompt
    assert "asset_ref.evidence_text 必须是 source_span.text" in provider_prompt
    assert "storyboard_shot_numbering_handoff_v1" in provider_prompt
    assert payload["provider_calls_started"] is True
    assert payload["safe_manifest"]["status"] == "provider_structured"
    assert payload["safe_manifest"]["raw_provider_response_stored"] is False
    assert payload["safe_manifest"]["knowledgebase_version"] == "creative_prompt_knowledgebase_v1"
    assert "storyboard_shot_numbering_handoff_v1" in payload["safe_manifest"]["knowledge_rule_ids"]
    assert payload["shots"][0]["description"].startswith("@主角")
    assert ("发光地图", "prop") in {
        (ref.get("label"), ref.get("asset_type")) for ref in payload["shots"][1]["asset_refs"]
    }
    assert not any(
        item.get("display_name") == "发光地图" and item.get("reason") == "prop_requires_manual_asset_entry"
        for item in payload["shots"][1]["dropped_asset_ref_diagnostics"]
    )


def test_storyboard_breakdown_accepts_llm_json_with_markdown_and_trailing_text(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AFS_ALLOW_REMOTE_LLM", "true")

    class Descriptor:
        modality = "llm"

    class FakeRegistry:
        _descriptors = {"prompt_optimizer": Descriptor()}

        def dispatch(self, capability, service_id, request):
            assert capability == "llm"
            assert service_id == "prompt_optimizer"
            return {
                "text": (
                    "```json\n"
                    "{\"shots\":[{\"shot_id\":\"shot_01\",\"index\":1,\"duration\":\"6s\","
                    "\"description\":\"@主角 在 @主要场景 静静仰望星空。\","
                    "\"shot_size\":\"远景\",\"light_atmosphere\":\"冷蓝月光\","
                    "\"camera_motion\":\"固定机位\",\"dialogue\":\"无明确对白\","
                    "\"sound\":\"城市环境底噪\","
                    "\"asset_refs\":["
                    "{\"label\":\"主角\",\"asset_type\":\"character\",\"status\":\"mentioned\",\"source\":\"explicit\"},"
                    "{\"label\":\"主要场景\",\"asset_type\":\"scene\",\"status\":\"mentioned\",\"source\":\"explicit\"}"
                    "]}]}\n"
                    "```\n"
                    "以上是可审查分镜。"
                ),
                "provider_calls_started": True,
            }

    monkeypatch.setattr("apps.api.runtime_storyboard_breakdown.load_provider_registry", lambda: FakeRegistry())
    client = TestClient(create_runtime_app(runtime_root=tmp_path))
    client.post(
        "/projects",
        json={
            "project_id": "proj_storyboard_llm_fenced",
            "project_type": "short_video_campaign",
            "goal": "Create a visual story from a complete script.",
        },
    )

    response = client.post(
        "/projects/proj_storyboard_llm_fenced/storyboard-breakdowns",
        json={
            "node_id": "text_001",
            "script_text": "未来机器人站在城市屋顶仰望星空。",
            "target_platform": "short_video",
            "style": "cinematic",
            "node_parameters": {"llm_provider": "prompt_optimizer"},
            "generated_at": "2026-06-22T10:06:00+08:00",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["provider_calls_started"] is True
    assert payload["safe_manifest"]["status"] == "provider_structured"
    assert payload["safe_manifest"]["discard_reason"] is None
    assert payload["shots"][0]["asset_refs"][1]["asset_type"] == "scene"


def test_storyboard_provider_parser_discards_unrequested_set_pieces(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AFS_ALLOW_REMOTE_LLM", "true")

    class Descriptor:
        modality = "llm"

    class FakeRegistry:
        _descriptors = {"prompt_optimizer": Descriptor()}

        def dispatch(self, capability, service_id, request):
            assert capability == "llm"
            assert service_id == "prompt_optimizer"
            return {
                "text": json.dumps(
                    {
                        "shots": [
                            {
                                "shot_id": "shot_01",
                                "index": 1,
                                "duration": "5s",
                                "description": "@未来机器人 坐在木椅上仰望星空，屋檐从画面右侧压下来。",
                                "shot_size": "中景",
                                "light_atmosphere": "冷月光",
                                "camera_motion": "固定机位",
                                "dialogue": "无明确对白",
                                "sound": "夜晚环境声",
                                "source_span": {
                                    "span_id": "script_span_01",
                                    "text": "未来机器人站在农村屋顶仰望星空。",
                                },
                                "asset_refs": [
                                    {"label": "未来机器人", "asset_type": "character", "status": "mentioned", "source": "explicit"},
                                    {"label": "农村屋顶", "asset_type": "scene", "status": "mentioned", "source": "explicit"},
                                ],
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                "provider_calls_started": True,
            }

    monkeypatch.setattr("apps.api.runtime_storyboard_breakdown.load_provider_registry", lambda: FakeRegistry())
    client = TestClient(create_runtime_app(runtime_root=tmp_path))
    client.post("/projects", json={"project_id": "proj_storyboard_grounding", "goal": "Ground storyboard output"})

    response = client.post(
        "/projects/proj_storyboard_grounding/storyboard-breakdowns",
        json={
            "node_id": "text_001",
            "script_text": "未来机器人站在农村屋顶仰望星空。",
            "target_platform": "short_video",
            "style": "cinematic",
            "node_parameters": {"llm_provider": "prompt_optimizer"},
            "generated_at": "2026-06-28T10:06:00+08:00",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    shots_serialized = json.dumps(payload["shots"], ensure_ascii=False)
    assert payload["provider_calls_started"] is True
    assert payload["safe_manifest"]["status"] == "local_fallback"
    assert "unsupported source additions" in payload["safe_manifest"]["discard_reason"]
    assert "木椅" not in shots_serialized
    assert "屋檐从画面右侧压下来" not in shots_serialized


def test_storyboard_breakdown_discards_provider_storyboard_with_hallucinated_story_facts(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AFS_ALLOW_REMOTE_LLM", "true")
    source_script = "小明有一只猫，小猫捡到了一只狗。"

    class Descriptor:
        modality = "llm"

    class FakeRegistry:
        _descriptors = {"prompt_optimizer": Descriptor()}

        def dispatch(self, capability, service_id, request):
            assert capability == "llm"
            assert service_id == "prompt_optimizer"
            return {
                "text": json.dumps(
                    {
                        "shots": [
                            {
                                "shot_id": "shot_01",
                                "index": 1,
                                "duration": "3.2s",
                                "description": "@小明 @煤球 @老城区巷口。小明蹲在老城区巷口，专注晃动旧毛线团；三人一猫影子细长交叠。",
                                "shot_size": "中景",
                                "light_atmosphere": "暖调斜阳",
                                "camera_motion": "缓慢横移",
                                "dialogue": "无明确对白",
                                "sound": "低频蝉鸣持续",
                                "source_span": {"text": source_script},
                                "asset_refs": [
                                    {"label": "小明", "asset_type": "character", "status": "mentioned", "source": "explicit"},
                                    {"label": "煤球", "asset_type": "character", "status": "mentioned", "source": "explicit"},
                                    {"label": "老城区巷口", "asset_type": "scene", "status": "mentioned", "source": "explicit"},
                                ],
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                "provider_calls_started": True,
            }

    monkeypatch.setattr("apps.api.runtime_storyboard_breakdown.load_provider_registry", lambda: FakeRegistry())
    client = TestClient(create_runtime_app(runtime_root=tmp_path))
    client.post("/projects", json={"project_id": "proj_sb_hallucinated_facts", "goal": "Ground storyboard output"})

    response = client.post(
        "/projects/proj_sb_hallucinated_facts/storyboard-breakdowns",
        json={
            "node_id": "text_001",
            "script_text": source_script,
            "target_platform": "short_video",
            "style": "cinematic",
            "node_parameters": {"llm_provider": "prompt_optimizer"},
            "generated_at": "2026-07-15T10:06:00+08:00",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    shots_serialized = json.dumps(payload["shots"], ensure_ascii=False)
    assert payload["provider_calls_started"] is True
    assert payload["safe_manifest"]["status"] == "local_fallback"
    assert "unsupported source additions" in payload["safe_manifest"]["discard_reason"]
    assert "煤球" not in shots_serialized
    assert "毛线团" not in shots_serialized
    assert "三人一猫" not in shots_serialized


def test_storyboard_breakdown_keeps_provider_started_when_llm_json_is_discarded(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AFS_ALLOW_REMOTE_LLM", "true")

    class Descriptor:
        modality = "llm"

    class FakeRegistry:
        _descriptors = {"prompt_optimizer": Descriptor()}

        def dispatch(self, capability, service_id, request):
            assert capability == "llm"
            assert service_id == "prompt_optimizer"
            return {"text": "not json", "provider_calls_started": True}

    monkeypatch.setattr("apps.api.runtime_storyboard_breakdown.load_provider_registry", lambda: FakeRegistry())
    client = TestClient(create_runtime_app(runtime_root=tmp_path))
    client.post(
        "/projects",
        json={
            "project_id": "proj_storyboard_llm_discard",
            "project_type": "short_video_campaign",
            "goal": "Create a visual story from a complete script.",
        },
    )

    response = client.post(
        "/projects/proj_storyboard_llm_discard/storyboard-breakdowns",
        json={
            "node_id": "text_001",
            "script_text": "机器人站在城市屋顶看星星。它低头检查手里的发光地图。",
            "target_platform": "short_video",
            "style": "cinematic",
            "node_parameters": {"llm_provider": "prompt_optimizer"},
            "generated_at": "2026-06-22T10:08:00+08:00",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["provider_calls_started"] is True
    assert payload["safe_manifest"]["status"] == "local_fallback"
    assert payload["safe_manifest"]["fallback_visible_to_user"] is True
    assert payload["safe_manifest"]["fallback_reason"] == "provider_output_discarded"
    assert "LLM 输出未被采用" in payload["safe_manifest"]["fallback_message"]
    assert payload["safe_manifest"]["raw_provider_response_stored"] is False
    assert payload["safe_manifest"]["discard_reason"]
    assert payload["shots"]


def test_storyboard_breakdown_marks_provider_call_failure_as_visible_fallback(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AFS_ALLOW_REMOTE_LLM", "true")

    class Descriptor:
        modality = "llm"

    class FakeRegistry:
        _descriptors = {"prompt_optimizer": Descriptor()}

        def dispatch(self, capability, service_id, request):
            raise ModelProviderError("temporary provider unavailable")

    monkeypatch.setattr("apps.api.runtime_storyboard_breakdown.load_provider_registry", lambda: FakeRegistry())
    client = TestClient(create_runtime_app(runtime_root=tmp_path))
    client.post(
        "/projects",
        json={
            "project_id": "proj_storyboard_provider_failed",
            "project_type": "short_video_campaign",
            "goal": "Create a visual story from a complete script.",
        },
    )

    response = client.post(
        "/projects/proj_storyboard_provider_failed/storyboard-breakdowns",
        json={
            "node_id": "text_001",
            "script_text": "小明蹲在老城区巷口，橘猫“煤球”叼回一只湿漉漉的奶狗。",
            "target_platform": "short_video",
            "style": "cinematic",
            "node_parameters": {"llm_provider": "prompt_optimizer"},
            "generated_at": "2026-07-16T10:08:00+08:00",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["provider_calls_started"] is False
    assert payload["fallback_visible_to_user"] is True
    assert payload["fallback_reason"] == "provider_call_failed"
    assert payload["safe_manifest"]["fallback_reason"] == "provider_call_failed"
    assert "provider 调用失败" in payload["safe_manifest"]["fallback_message"]
    assert payload["shots"]


def test_storyboard_breakdown_discards_provider_json_with_untranslated_display_english(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AFS_ALLOW_REMOTE_LLM", "true")

    class Descriptor:
        modality = "llm"

    class FakeRegistry:
        _descriptors = {"prompt_optimizer": Descriptor()}

        def dispatch(self, capability, service_id, request):
            assert capability == "llm"
            assert service_id == "prompt_optimizer"
            return {
                "text": json.dumps(
                    {
                        "shots": [
                            {
                                "shot_id": "shot_03",
                                "index": 3,
                                "duration": "2.2s",
                                "description": "@阿团 @厨房。低角度跟拍：阿团踮脚跃起，指尖触到橱柜顶层麦片罐底部。",
                                "shot_size": "中景",
                                "light_atmosphere": "暖色主光",
                                "camera_motion": "subtle parallax drift following 阿团's arm arc",
                                "dialogue": "无明确对白",
                                "sound": "环境底噪，动作音随画面同步",
                                "source_span": {"text": "阿团踮脚跃起，指尖触到橱柜顶层麦片罐底部。"},
                                "asset_refs": [
                                    {"label": "阿团", "asset_type": "character", "status": "mentioned", "source": "explicit"},
                                    {"label": "厨房", "asset_type": "scene", "status": "mentioned", "source": "explicit"},
                                ],
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                "provider_calls_started": True,
            }

    monkeypatch.setattr("apps.api.runtime_storyboard_breakdown.load_provider_registry", lambda: FakeRegistry())
    client = TestClient(create_runtime_app(runtime_root=tmp_path))
    client.post(
        "/projects",
        json={
            "project_id": "proj_sb_eng_discard",
            "project_type": "short_video_campaign",
            "goal": "Create a visual story from a complete script.",
        },
    )

    response = client.post(
        "/projects/proj_sb_eng_discard/storyboard-breakdowns",
        json={
            "node_id": "text_001",
            "script_text": "阿团踮脚跃起，指尖触到橱柜顶层麦片罐底部。厨房暖光照着桌面。",
            "target_platform": "short_video",
            "style": "cinematic",
            "node_parameters": {"llm_provider": "prompt_optimizer"},
            "generated_at": "2026-07-14T10:08:00+08:00",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    shots_serialized = json.dumps(payload["shots"], ensure_ascii=False)
    assert payload["provider_calls_started"] is True
    assert payload["safe_manifest"]["status"] == "local_fallback"
    assert payload["safe_manifest"]["raw_provider_response_stored"] is False
    assert "untranslated English in camera_motion" in payload["safe_manifest"]["discard_reason"]
    assert "subtle parallax" not in shots_serialized
    assert "following" not in shots_serialized


def test_storyboard_breakdown_is_exported_without_secret_surface(tmp_path) -> None:
    output_path = tmp_path / "frontend" / "afs-runtime-service.openapi.json"
    exported_path = export_openapi_schema(output_path, runtime_root=tmp_path / "openapi_runtime")
    schema = json.loads(exported_path.read_text(encoding="utf-8"))
    serialized = json.dumps(schema, ensure_ascii=False).lower()

    assert "/projects/{project_id}/storyboard-breakdowns" in schema["paths"]
    assert "storyboardbreakdownrequest" in serialized
    assert "api_key" not in serialized
    assert "signed_url" not in serialized


def test_storyboard_shots_include_keyframe_and_video_plan_fields() -> None:
    shots = local_storyboard_shots("A future robot stands on a rural rooftop and watches stars before turning its glowing face toward the sky.")

    first = shots[0]
    assert first["shot_function"] == "establish"
    assert first["keyframe_requirement"]["frame_role"] == "establish_keyframe"
    assert first["video_motion_requirement"]["time_beats"][0]["time"] == "0.0s-1.0s"
    assert "robot head shell and mechanical body proportions" in first["continuity_locks"]
    assert "no unrequested chair" in first["negative_scene_locks"]


def test_shot_asset_plan_returns_editable_asset_profiles(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("AFS_ALLOW_REMOTE_LLM", raising=False)
    client = TestClient(create_runtime_app(runtime_root=tmp_path))
    project_id = "proj_asset_profile_plan"
    client.post("/projects", json={"project_id": project_id, "goal": "Asset profile plan"})

    response = client.post(
        f"/projects/{project_id}/shot-asset-plans",
        json={
            "node_id": "shot_robot_rooftop",
            "shot": {
                "shot_id": "shot_01",
                "index": 1,
                "description": "A future robot watches stars on a rural rooftop platform.",
                "asset_refs": [
                    {"label": "Future Robot", "asset_type": "character", "status": "candidate", "source": "explicit"},
                    {"label": "Rooftop Platform", "asset_type": "scene", "status": "candidate", "source": "explicit"},
                ],
            },
            "script_text": "A future robot watches stars on a rural rooftop platform.",
            "generated_at": "2026-06-27T10:05:00+08:00",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["safe_manifest"]["asset_profile_count"] >= 2
    assert payload["safe_manifest"]["asset_graph_asset_count"] >= 2
    assert len(payload["asset_profile_plan"]) >= 2
    assert payload["asset_graph"]["artifact_type"] == "agentflow_asset_graph"
    robot = next(item for item in payload["asset_profile_plan"] if item["asset_type"] == "character")
    scene = next(item for item in payload["asset_profile_plan"] if item["asset_type"] == "scene")
    assert robot["profile_stage"] == "candidate_profile_seed"
    assert "robot head shell" in robot["identity_locks"]
    assert "forbidden geometry" in scene["editable_fields"]
    assert "do not add chairs or stools unless approved" in scene["negative_locks"]
    assert all("profile_plan" in ref for ref in payload["asset_refs"])
    assert all("graph_asset_id" in ref for ref in payload["asset_refs"])


def test_shot_asset_plan_keeps_human_and_animal_profiles_separate(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("AFS_ALLOW_REMOTE_LLM", raising=False)
    client = TestClient(create_runtime_app(runtime_root=tmp_path))
    project_id = "proj_human_animal_profile_plan"
    client.post("/projects", json={"project_id": project_id, "goal": "Asset profile plan"})

    shot_text = (
        "镜号：01\n"
        "画面描述：@小明 @橘猫 @老城区巷口。小明蹲在老城区巷口青石台阶上，"
        "指尖沾着猫粮碎屑，正低头给蜷在纸箱里的橘猫顺毛。\n"
        "资产：@小明（角色）、@橘猫（角色）、@老城区巷口（场景）"
    )
    response = client.post(
        f"/projects/{project_id}/shot-asset-plans",
        json={
            "node_id": "shot_cat_alley",
            "shot": {
                "shot_id": "S01",
                "index": 1,
                "description": shot_text,
                "asset_refs": [
                    {"label": "小明", "asset_type": "character", "status": "mentioned", "source": "explicit"},
                    {"label": "橘猫", "asset_type": "character", "status": "mentioned", "source": "explicit"},
                    {"label": "老城区巷口", "asset_type": "scene", "status": "mentioned", "source": "explicit"},
                ],
            },
            "script_text": shot_text,
            "generated_at": "2026-07-15T21:45:00+08:00",
        },
    )

    assert response.status_code == 200
    refs = response.json()["asset_refs"]
    xiaoming = next(item for item in refs if item["label"] == "小明")
    cat = next(item for item in refs if item["label"] == "橘猫")
    assert xiaoming["profile_plan"]["character_subtype"] != "animal"
    assert cat["profile_plan"]["character_subtype"] == "animal"
    assert cat["profile_plan"]["facts"]["species"] == "猫"
    assert cat["profile_plan"]["facts"]["color_pattern"] == "橘色"


def test_shot_asset_plan_uses_llm_asset_contract_for_animal_subtype(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AFS_ALLOW_REMOTE_LLM", "true")
    calls: list[object] = []

    class Descriptor:
        modality = "llm"

    class FakeRegistry:
        _descriptors = {"prompt_optimizer": Descriptor()}

        def dispatch(self, capability, service_id, request):
            calls.append(request)
            assert capability == "llm"
            assert service_id == "prompt_optimizer"
            assert "shot_asset_recognition_v1" in request.prompt
            assert "不要把“青石台阶”里的“石台”联想成山巅石台战场" in request.prompt
            evidence = "小明蹲在老城区巷口青石台阶上，指尖沾着猫粮碎屑，专注晃动旧毛线团；煤球蹲坐不动，尾巴尖微微翘起如问号，琥珀色瞳孔紧盯毛线末端。"
            return {
                "text": json.dumps(
                    {
                        "assets": [
                            {
                                "label": "小明",
                                "asset_type": "character",
                                "character_subtype": "human",
                                "evidence_text": evidence,
                                "facts": {"identity": "小明", "appearance_context": "蹲在老城区巷口青石台阶上"},
                                "continuity_locks": ["保持小明人物身份"],
                                "negative_locks": ["不要把小明改成动物"],
                                "role_in_shot": "照看猫并晃动毛线团",
                                "confidence": 0.94,
                            },
                            {
                                "label": "煤球",
                                "asset_type": "character",
                                "character_subtype": "animal",
                                "evidence_text": evidence,
                                "facts": {
                                    "species": "猫",
                                    "current_action": ["蹲坐不动", "紧盯毛线末端"],
                                    "distinctive_marks": ["尾巴尖微微翘起如问号", "琥珀色瞳孔"],
                                },
                                "continuity_locks": ["保持煤球猫的动物主体身份", "保持琥珀色瞳孔"],
                                "negative_locks": ["不要把煤球改成人类角色"],
                                "role_in_shot": "被小明逗玩的猫",
                                "confidence": 0.96,
                            },
                            {
                                "label": "老城区巷口",
                                "asset_type": "scene",
                                "character_subtype": "",
                                "evidence_text": evidence,
                                "facts": {
                                    "location_type": "老城区巷口",
                                    "spatial_structure": "青石台阶与巷口空间",
                                    "key_environment_elements": ["青石台阶"],
                                },
                                "continuity_locks": ["保持老城区巷口空间结构"],
                                "negative_locks": ["不要改成其他地点"],
                                "role_in_shot": "主要场景",
                                "confidence": 0.93,
                            },
                        ],
                        "dropped_candidates": [],
                    },
                    ensure_ascii=False,
                ),
                "provider_calls_started": True,
            }

    monkeypatch.setattr("apps.api.runtime_shot_asset_plan.load_provider_registry", lambda: FakeRegistry())
    client = TestClient(create_runtime_app(runtime_root=tmp_path))
    project_id = "proj_llm_asset_plan"
    client.post("/projects", json={"project_id": project_id, "goal": "Asset profile plan"})

    shot_text = (
        "镜号：01\n"
        "画面描述：@小明 @煤球 @老城区巷口。小明蹲在老城区巷口青石台阶上，指尖沾着猫粮碎屑，"
        "专注晃动旧毛线团；煤球蹲坐不动，尾巴尖微微翘起如问号，琥珀色瞳孔紧盯毛线末端。\n"
        "资产：@小明（角色）、@煤球（角色）、@老城区巷口（场景）"
    )
    response = client.post(
        f"/projects/{project_id}/shot-asset-plans",
        json={
            "node_id": "shot_cat_alley_llm",
            "shot": {
                "shot_id": "S01",
                "index": 1,
                "description": shot_text,
                "asset_refs": [
                    {"label": "小明", "asset_type": "character", "status": "mentioned", "source": "explicit"},
                    {"label": "煤球", "asset_type": "character", "status": "mentioned", "source": "explicit"},
                    {"label": "老城区巷口", "asset_type": "scene", "status": "mentioned", "source": "explicit"},
                ],
            },
            "script_text": shot_text,
            "generated_at": "2026-07-15T22:15:00+08:00",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    serialized = json.dumps(payload, ensure_ascii=False)
    refs = payload["asset_refs"]
    xiaoming = next(item for item in refs if item["label"] == "小明")
    cat = next(item for item in refs if item["label"] == "煤球")
    scene = next(item for item in refs if item["label"] == "老城区巷口")
    graph_cat = next(item for item in payload["asset_graph"]["assets"] if item["label"] == "煤球")

    assert len(calls) == 1
    assert payload["safe_manifest"]["status"] == "provider_structured_asset_plan"
    assert payload["safe_manifest"]["provider_calls_started"] is True
    assert payload["safe_manifest"]["raw_provider_response_stored"] is False
    assert xiaoming["profile_plan"]["character_subtype"] == "human"
    assert cat["profile_plan"]["character_subtype"] == "animal"
    assert cat["profile_plan"]["facts"]["species"] == "猫"
    assert "保持煤球猫的动物主体身份" in cat["profile_plan"]["identity_locks"]
    assert scene["asset_type"] == "scene"
    assert graph_cat["character_subtype"] == "animal"
    assert graph_cat["asset_fact_profile"]["facts"]["species"] == "猫"
    assert "山巅石台战场" not in serialized
    assert response_contains_unsafe_marker(payload) is False


def test_shot_asset_plan_merges_storyboard_animal_coreference_when_provider_omits_it(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AFS_ALLOW_REMOTE_LLM", "true")

    class Descriptor:
        modality = "llm"

    class FakeRegistry:
        _descriptors = {"prompt_optimizer": Descriptor()}

        def dispatch(self, capability, service_id, request):
            assert capability == "llm"
            assert service_id == "prompt_optimizer"
            evidence = "狗直奔小华，在距她拖鞋鞋尖三十厘米处骤然刹住，球被轻轻吐在拖鞋边。"
            return {
                "text": json.dumps(
                    {
                        "assets": [
                            {
                                "label": "小华",
                                "asset_type": "character",
                                "character_subtype": "human",
                                "evidence_text": evidence,
                                "facts": {"identity": "小华"},
                                "continuity_locks": ["保持小华人物身份"],
                                "negative_locks": ["不要把小华改成动物"],
                                "role_in_shot": "狗奔向的对象",
                                "confidence": 0.91,
                            }
                        ],
                        "dropped_candidates": [],
                    },
                    ensure_ascii=False,
                ),
                "provider_calls_started": True,
            }

    monkeypatch.setattr("apps.api.runtime_shot_asset_plan.load_provider_registry", lambda: FakeRegistry())
    client = TestClient(create_runtime_app(runtime_root=tmp_path))
    project_id = "proj_llm_asset_plan_coref_dog"
    client.post("/projects", json={"project_id": project_id, "goal": "Asset profile plan"})

    script_text = (
        "小华蹲在公园长椅旁。"
        "一只黑色拉布拉多突然从斜坡草甸冲下，嘴里叼着一只磨损严重的荧光绿网球。"
        "狗直奔小华，在距她拖鞋鞋尖三十厘米处骤然刹住，球被轻轻吐在拖鞋边。"
    )
    response = client.post(
        f"/projects/{project_id}/shot-asset-plans",
        json={
            "node_id": "shot_dog_coreference",
            "shot": {
                "shot_id": "S03",
                "index": 3,
                "description": "@小华。狗直奔小华，在距她拖鞋鞋尖三十厘米处骤然刹住，球被轻轻吐在拖鞋边。",
                "asset_refs": [
                    {"label": "小华", "asset_type": "character", "status": "mentioned", "source": "explicit"},
                ],
            },
            "script_text": script_text,
            "generated_at": "2026-07-16T14:20:00+08:00",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    refs = {item["label"]: item for item in payload["asset_refs"]}

    assert payload["safe_manifest"]["status"] == "provider_structured_asset_plan"
    assert "小华" in refs
    assert "黑色拉布拉多" in refs
    assert refs["小华"]["profile_plan"]["character_subtype"] == "human"
    assert refs["黑色拉布拉多"]["profile_plan"]["character_subtype"] == "animal"
    assert refs["黑色拉布拉多"]["profile_plan"]["facts"]["species"] == "狗"


def test_shot_asset_plan_rejects_ungrounded_provider_assets(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AFS_ALLOW_REMOTE_LLM", "true")

    class Descriptor:
        modality = "llm"

    class FakeRegistry:
        _descriptors = {"prompt_optimizer": Descriptor()}

        def dispatch(self, capability, service_id, request):
            assert capability == "llm"
            assert service_id == "prompt_optimizer"
            return {
                "text": json.dumps(
                    {
                        "assets": [
                            {
                                "label": "山巅石台战场",
                                "asset_type": "scene",
                                "evidence_text": "山巅石台战场上云海翻卷。",
                                "facts": {"location_type": "山巅石台战场"},
                                "confidence": 0.99,
                            }
                        ],
                        "dropped_candidates": [],
                    },
                    ensure_ascii=False,
                ),
                "provider_calls_started": True,
            }

    monkeypatch.setattr("apps.api.runtime_shot_asset_plan.load_provider_registry", lambda: FakeRegistry())
    client = TestClient(create_runtime_app(runtime_root=tmp_path))
    project_id = "proj_llm_asset_plan_rejects_hallucination"
    client.post("/projects", json={"project_id": project_id, "goal": "Asset profile plan"})

    shot_text = (
        "镜号：01\n"
        "画面描述：@小明 @煤球 @老城区巷口。小明蹲在老城区巷口青石台阶上，指尖沾着猫粮碎屑；"
        "煤球蹲坐不动，尾巴尖微微翘起如问号。\n"
        "资产：@小明（角色）、@煤球（角色）、@老城区巷口（场景）"
    )
    response = client.post(
        f"/projects/{project_id}/shot-asset-plans",
        json={
            "node_id": "shot_cat_alley_rejects_hallucination",
            "shot": {
                "shot_id": "S01",
                "index": 1,
                "description": shot_text,
                "asset_refs": [
                    {"label": "小明", "asset_type": "character", "status": "mentioned", "source": "explicit"},
                    {"label": "煤球", "asset_type": "character", "status": "mentioned", "source": "explicit"},
                    {"label": "老城区巷口", "asset_type": "scene", "status": "mentioned", "source": "explicit"},
                ],
            },
            "script_text": shot_text,
            "generated_at": "2026-07-15T22:20:00+08:00",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    serialized = json.dumps(payload, ensure_ascii=False)

    assert payload["safe_manifest"]["status"] == "local_asset_plan"
    assert payload["safe_manifest"]["provider_calls_started"] is True
    assert payload["safe_manifest"]["discard_reason"] == "provider asset response has no grounded usable assets"
    assert "山巅石台战场" not in serialized
    assert ("老城区巷口", "scene") in {(item["label"], item["asset_type"]) for item in payload["asset_refs"]}


def test_storyboard_plan_includes_professional_reference_for_rooftop_video() -> None:
    shots = local_storyboard_shots("A future robot stands on a rural rooftop and watches stars before turning its glowing face toward the sky.")

    reference = shots[0]["professional_reference"]

    assert {"night", "rooftop", "video"} <= set(reference["tags"])
    assert "moderate-to-deep" in reference["depth_of_field"]["decision"]
    assert reference["pacing"]["must_include"][0].startswith("0-1s")
    assert reference["writes_company_kb"] is False


def test_storyboard_plan_includes_director_scenario_for_saas_demo() -> None:
    shots = local_storyboard_shots("A SaaS product launch demo shows a dashboard workflow and the final saved-time result.")

    scenario = shots[0]["director_scenario"]

    assert scenario["primary_scenario"] == "saas_launch"
    assert "screen geometry remains readable" in scenario["quality_checks"]
    assert scenario["writes_company_kb"] is False
