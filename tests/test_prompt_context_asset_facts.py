import pytest

from agentflow.algorithms.asset_facts import build_asset_fact_profile
from agentflow.algorithms.prompt_integrity import validate_prompt_integrity
from agentflow.algorithms.provider_gate_manifest import video_provider_prompt
from apps.api.runtime_asset_graph import build_asset_graph


SCRIPT_TEXT = (
    "片名：《捡到一只小狗》 小明蹲在老槐树根旁，指尖沾着泥，正用纸盒给流浪猫搭窝。"
    "橘猫蜷在盒底舔爪，尾巴尖轻轻晃——它刚叼回一只湿漉漉的小狗，毛色灰白相间，"
    "左耳缺了一小块，正瑟缩着打喷嚏。"
)

SHOT_TEXT = (
    "镜号：01 时长：3.2 画面描述：@老槐树 @橘猫 @小狗。"
    "老槐树粗壮盘曲的树根特写，泥土湿润微裂；"
    "一只沾着褐泥的手指悬停在纸盒边缘，指尖微微颤抖；"
    "纸盒内橘猫蜷卧，尾巴尖轻晃，嘴里叼着湿漉漉的小狗，小狗左耳缺一小块，正打喷嚏 "
    "景别：特写 光影氛围：午后柔光，树影斑驳，暖调主光斜洒在纸盒边缘，小狗毛尖泛银灰反光 "
    "运镜：缓慢推近"
)

MOJIBAKE_ASSET_SIGNATURE = "\u74a7\u52ea\u9a87\u7edb\u60e7\u6095\u951b\u6b55n"


def test_asset_fact_profile_extracts_actual_puppy_facts_from_evidence() -> None:
    profile = build_asset_fact_profile(
        asset_type="character",
        label="小狗",
        evidence_text=SHOT_TEXT,
        source_text=SCRIPT_TEXT,
    )

    facts = profile["facts"]
    assert profile["character_subtype"] == "animal"
    assert facts["species"] == "狗"
    assert facts["color_pattern"] == "灰白相间"
    assert facts["surface_state"] == "湿漉漉"
    assert facts["size_or_age"] == "幼小"
    assert any("左耳缺" in item for item in facts["distinctive_marks"])
    assert "打喷嚏" in facts["current_action"]
    assert "瑟缩" in facts["current_action"]
    assert "叼回" not in facts["current_action"]
    assert any("保持灰白相间" in item for item in profile["continuity_locks"])
    assert any("不要新增项圈、衣物或拟人化装饰" in item for item in profile["negative_locks"])


def test_storyboard_asset_graph_carries_evidence_driven_facts() -> None:
    graph = _actual_case_asset_graph()

    dog = next(asset for asset in graph["assets"] if asset["label"] == "小狗")
    cat = next(asset for asset in graph["assets"] if asset["label"] == "橘猫")
    scene = next(asset for asset in graph["assets"] if asset["label"] == "老槐树")

    assert dog["character_subtype"] == "animal"
    assert dog["facts"]["color_pattern"] == "灰白相间"
    assert any("左耳缺" in item for item in dog["facts"]["distinctive_marks"])
    assert cat["character_subtype"] == "animal"
    assert cat["facts"]["species"] == "猫"
    assert cat["facts"]["color_pattern"] == "橘色"
    assert "distinctive_marks" not in cat["facts"]
    assert cat["facts"].get("size_or_age") != "幼小"
    assert "facts" in dog["asset_fact_profile"]
    assert "粗壮盘曲" in scene["facts"]["spatial_structure"]
    assert "午后柔光" in scene["facts"]["lighting_atmosphere"]


def test_video_prompt_uses_typed_animal_renderer_without_human_template_pollution() -> None:
    prompt = video_provider_prompt(
        prompt_text=SHOT_TEXT,
        optimized_prompt="基于首帧生成5秒连续视频，保持分镜画面与缓慢推近。",
        duration_sec=5,
        motion="橘猫轻微呼吸，小狗轻微打喷嚏，树叶轻晃，镜头缓慢推近。",
        last_frame_image_asset_id=None,
        context_bundle={"asset_graph": _actual_case_asset_graph()},
    )

    assert "first frame as a strict visual anchor" in prompt
    assert "灰白相间" in prompt
    assert "湿漉漉" in prompt
    assert "左耳缺一小块" in prompt
    assert "fur/skin markings" in prompt or "fur or skin markings" in prompt
    assert MOJIBAKE_ASSET_SIGNATURE not in prompt
    assert "衣料" not in prompt
    assert "发丝" not in prompt
    assert "服饰" not in prompt
    assert "道具握持关系" not in prompt
    assert "场景1" not in prompt
    assert "wardrobe" not in prompt.lower()
    assert "hairstyle" not in prompt.lower()
    assert "clothing" not in prompt.lower()


def test_prompt_integrity_guard_blocks_mojibake_before_provider_prompt_dispatch() -> None:
    with pytest.raises(ValueError, match="integrity guard"):
        validate_prompt_integrity(f"{MOJIBAKE_ASSET_SIGNATURE}场景1: 场景1", field_name="video_provider_prompt")


def _actual_case_asset_graph() -> dict:
    return build_asset_graph(
        [
            {
                "shot_id": "S01",
                "description": SHOT_TEXT,
                "asset_refs": [
                    {"asset_type": "scene", "label": "老槐树", "evidence_text": SHOT_TEXT},
                    {"asset_type": "character", "label": "橘猫", "evidence_text": SHOT_TEXT},
                    {"asset_type": "character", "label": "小狗", "evidence_text": SHOT_TEXT},
                ],
            }
        ],
        source_text=SCRIPT_TEXT,
    )
