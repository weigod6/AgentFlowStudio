from __future__ import annotations

import json

import pytest

from apps.api.runtime_storyboard_provider_parse import shots_from_provider_text


def test_storyboard_provider_parser_localizes_english_shot_fields() -> None:
    payload = {
        "shots": [
            {
                "shot_id": "shot_01",
                "index": 1,
                "duration": "2.8s",
                "description": "@小红。特写：一只少女左手紧攥试卷右下角，指节发白，纸面剧烈抖动；试卷上‘59分’被褶皱扭曲变形。",
                "shot_size": "extreme_close_up",
                "light_atmosphere": "冷灰高反差，雨滴在画面边缘高速拖影",
                "camera_motion": "static, shallow_depth_of_field_focus_on_fingertips_and_score",
                "dialogue": "班主任（OS）：心比天高，基础不牢……",
                "sound": "暴雨白噪音持续，纸张哗哗震颤声高频叠加",
                "source_span": {"text": "暴雨倾盆的放学路上，高中生小红攥着被风吹得哗哗作响的试卷，低头疾走。"},
                "asset_refs": [
                    {
                        "label": "小红",
                        "asset_type": "character",
                        "status": "mentioned",
                        "source": "explicit",
                    }
                ],
            }
        ]
    }

    shots = shots_from_provider_text(
        json.dumps(payload, ensure_ascii=False),
        source_script_text="暴雨倾盆的放学路上，高中生小红攥着被风吹得哗哗作响的试卷，低头疾走。",
    )

    assert shots[0]["shot_size"] == "极近特写"
    assert shots[0]["camera_motion"] == "固定机位，浅景深，焦点锁定指尖和分数"
    assert shots[0]["dialogue"] == "班主任（画外音）：心比天高，基础不牢……"
    assert "extreme_close_up" not in json.dumps(shots[0], ensure_ascii=False)
    assert "shallow_depth_of_field" not in json.dumps(shots[0], ensure_ascii=False)


def test_storyboard_provider_parser_localizes_lighting_sound_and_compacts_camera_motion() -> None:
    payload = {
        "shots": [
            {
                "shot_id": "shot_01",
                "index": 1,
                "duration": "2.4s",
                "description": "@古战场。暴雨倾盆的古战场俯拍：断戟斜插泥泞，残旗半埋于焦黑土中，妖气如墨色浓雾翻涌蒸腾。",
                "shot_size": "medium_shot",
                "light_atmosphere": (
                    "high-contrast chiaroscuro; cold blue-green key light from storm clouds, "
                    "deep indigo shadows pooling in craters and weapon grooves, mist diffusing highlights"
                ),
                "camera_motion": "tilt_up, tilt",
                "dialogue": "无明确对白",
                "sound": "thunder rumble (low-frequency), torrential rain on metal/earth, distant guttural growls layered beneath",
                "source_span": {"text": "暴雨倾盆的古战场上，断戟斜插泥泞，残旗半埋于焦黑土中。"},
                "asset_refs": [
                    {
                        "label": "古战场",
                        "asset_type": "scene",
                        "status": "mentioned",
                        "source": "explicit",
                    }
                ],
            }
        ]
    }

    shots = shots_from_provider_text(
        json.dumps(payload, ensure_ascii=False),
        source_script_text="暴雨倾盆的古战场上，断戟斜插泥泞，残旗半埋于焦黑土中。",
    )

    shot = shots[0]
    assert shot["shot_size"] == "中景"
    assert shot["camera_motion"] == "向上摇镜"
    assert shot["light_atmosphere"] == "高反差明暗对照，暴风云层投下冷蓝绿色主光，深靛色阴影在弹坑与兵器沟槽中堆积，雾气柔化高光"
    assert shot["sound"] == "低频雷鸣轰隆，暴雨击打金属与泥土，远处喉音低吼在底层铺陈"
    serialized = json.dumps(shot, ensure_ascii=False)
    assert "high-contrast" not in serialized
    assert "thunder rumble" not in serialized
    assert "俯仰摇镜" not in shot["camera_motion"]


def test_storyboard_provider_parser_localizes_mixed_english_chinese_camera_motion() -> None:
    payload = {
        "shots": [
            {
                "shot_id": "shot_03",
                "index": 3,
                "duration": "2.2s",
                "description": "@阿团 @厨房。低角度跟拍：阿团踮脚跃起，后腿绷直，小臂肌肉线条清晰。",
                "shot_size": "medium_shot",
                "light_atmosphere": "暖色主光",
                "camera_motion": "handheld slight rise mimicking阿团's jump",
                "dialogue": "无明确对白",
                "sound": "环境底噪，动作音随画面同步",
                "source_span": {"text": "阿团踮脚跃起，指尖触到橱柜顶层麦片罐底部。"},
                "asset_refs": [
                    {
                        "label": "阿团",
                        "asset_type": "character",
                        "status": "mentioned",
                        "source": "explicit",
                    },
                    {
                        "label": "厨房",
                        "asset_type": "scene",
                        "status": "mentioned",
                        "source": "explicit",
                    },
                ],
            }
        ]
    }

    shots = shots_from_provider_text(
        json.dumps(payload, ensure_ascii=False),
        source_script_text="阿团踮脚跃起，指尖触到橱柜顶层麦片罐底部。",
    )

    assert shots[0]["camera_motion"] == "手持轻晃，轻微上升，模拟阿团跳跃"
    assert "handheld" not in json.dumps(shots[0], ensure_ascii=False)
    assert "mimicking" not in json.dumps(shots[0], ensure_ascii=False)


def test_storyboard_provider_parser_rejects_untranslated_display_english() -> None:
    payload = {
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
                    {
                        "label": "阿团",
                        "asset_type": "character",
                        "status": "mentioned",
                        "source": "explicit",
                    },
                    {
                        "label": "厨房",
                        "asset_type": "scene",
                        "status": "mentioned",
                        "source": "explicit",
                    },
                ],
            }
        ]
    }

    with pytest.raises(ValueError, match="untranslated English in camera_motion"):
        shots_from_provider_text(
            json.dumps(payload, ensure_ascii=False),
            source_script_text="阿团踮脚跃起，指尖触到橱柜顶层麦片罐底部。",
        )


@pytest.mark.parametrize(
    "field",
    ("description", "shot_size", "light_atmosphere", "camera_motion", "dialogue", "sound"),
)
@pytest.mark.parametrize("leak", ("EnglishLeak9", "cinematic9"))
def test_storyboard_provider_parser_rejects_arbitrary_alphanumeric_english_in_display_fields(
    field: str,
    leak: str,
) -> None:
    payload = {
        "shots": [
            {
                "shot_id": "shot_01",
                "index": 1,
                "duration": "5s",
                "description": "@阿团 @厨房。阿团走进厨房。",
                "shot_size": "中景",
                "light_atmosphere": "暖色主光",
                "camera_motion": "固定机位",
                "dialogue": "无明确对白",
                "sound": "环境底噪",
                "source_span": {"text": "阿团走进厨房。"},
                "asset_refs": [
                    {"label": "阿团", "asset_type": "character", "status": "mentioned", "source": "explicit"},
                    {"label": "厨房", "asset_type": "scene", "status": "mentioned", "source": "explicit"},
                ],
            }
        ]
    }
    payload["shots"][0][field] = f"中文内容 {leak}"

    with pytest.raises(ValueError, match=rf"untranslated English in {field}"):
        shots_from_provider_text(
            json.dumps(payload, ensure_ascii=False),
            source_script_text="阿团走进厨房。",
        )


def test_storyboard_provider_parser_preserves_allowed_numeric_units_and_formats() -> None:
    description = "@阿团。输出5s、720p、1080p、4K、16:9与1920x1080预览。"
    payload = {
        "shots": [
            {
                "shot_id": "shot_01",
                "index": 1,
                "duration": "5s",
                "description": description,
                "shot_size": "中景",
                "light_atmosphere": "暖色主光",
                "camera_motion": "固定机位",
                "dialogue": "无明确对白",
                "sound": "环境底噪",
                "source_span": {"text": "阿团查看预览。"},
                "asset_refs": [
                    {"label": "阿团", "asset_type": "character", "status": "mentioned", "source": "explicit"},
                ],
            }
        ]
    }

    shots = shots_from_provider_text(
        json.dumps(payload, ensure_ascii=False),
        source_script_text="阿团查看预览。",
    )

    assert shots[0]["description"] == description


def test_storyboard_provider_parser_rejects_unmentioned_assets_props_and_counts() -> None:
    source_script = "小明有一只猫，小猫捡到了一只狗。"
    payload = {
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
    }

    with pytest.raises(ValueError, match="unsupported source additions"):
        shots_from_provider_text(json.dumps(payload, ensure_ascii=False), source_script_text=source_script)


def test_storyboard_provider_parser_preserves_source_script_english() -> None:
    source_script = "Bob把AI camera放在厨房桌面上，随后说AI camera ready。"
    payload = {
        "shots": [
            {
                "shot_id": "shot_01",
                "index": 1,
                "duration": "3s",
                "description": "@Bob @厨房。Bob把AI camera放在桌面上，屏幕亮起。",
                "shot_size": "中景",
                "light_atmosphere": "暖色主光",
                "camera_motion": "固定机位",
                "dialogue": "Bob：AI camera ready。",
                "sound": "环境底噪，轻微电子提示音",
                "source_span": {"text": source_script},
                "asset_refs": [
                    {
                        "label": "Bob",
                        "asset_type": "character",
                        "status": "mentioned",
                        "source": "explicit",
                    },
                    {
                        "label": "厨房",
                        "asset_type": "scene",
                        "status": "mentioned",
                        "source": "explicit",
                    },
                ],
            }
        ]
    }

    shots = shots_from_provider_text(json.dumps(payload, ensure_ascii=False), source_script_text=source_script)

    serialized = json.dumps(shots[0], ensure_ascii=False)
    assert "Bob" in serialized
    assert "AI camera" in serialized


def test_storyboard_provider_parser_types_animals_props_and_resolves_dog_coreference() -> None:
    source_script = (
        "小华蹲在公园长椅旁。"
        "一只黑色拉布拉多突然从斜坡草甸冲下，嘴里叼着一只磨损严重的荧光绿网球。"
        "狗直奔小华，在距她拖鞋鞋尖三十厘米处骤然刹住，球被轻轻吐在拖鞋边。"
    )
    payload = {
        "shots": [
            {
                "shot_id": "shot_01",
                "index": 1,
                "duration": "2.2",
                "description": "@小华。小华蹲在公园长椅旁，眼神空落。",
                "shot_size": "中景",
                "light_atmosphere": "自然光影",
                "camera_motion": "固定机位",
                "dialogue": "无明确对白",
                "sound": "环境底噪",
                "source_span": {"text": "小华蹲在公园长椅旁。"},
                "asset_refs": [
                    {"label": "小华", "asset_type": "character", "status": "mentioned", "source": "explicit"},
                ],
            },
            {
                "shot_id": "shot_02",
                "index": 2,
                "duration": "2.8",
                "description": "@黑色拉布拉多 @荧光绿网球 @斜坡草甸。一只黑色拉布拉多突然从斜坡草甸冲下，嘴里叼着一只磨损严重的荧光绿网球。",
                "shot_size": "中景",
                "light_atmosphere": "正午强光",
                "camera_motion": "跟拍移动",
                "dialogue": "无明确对白",
                "sound": "奔跑踏草声",
                "source_span": {"text": "一只黑色拉布拉多突然从斜坡草甸冲下，嘴里叼着一只磨损严重的荧光绿网球。"},
                "asset_refs": [
                    {"label": "黑色拉布拉多", "asset_type": "character", "status": "mentioned", "source": "explicit"},
                    {"label": "荧光绿网球", "asset_type": "character", "status": "prop_relevant", "source": "explicit"},
                    {"label": "斜坡草甸", "asset_type": "scene", "status": "mentioned", "source": "explicit"},
                ],
            },
            {
                "shot_id": "shot_03",
                "index": 3,
                "duration": "2.1",
                "description": "@小华。狗直奔小华，在距她拖鞋鞋尖三十厘米处骤然刹住，球被轻轻吐在拖鞋边。",
                "shot_size": "特写",
                "light_atmosphere": "正午高光",
                "camera_motion": "固定机位",
                "dialogue": "无明确对白",
                "sound": "急停爪地声",
                "source_span": {"text": "狗直奔小华，在距她拖鞋鞋尖三十厘米处骤然刹住，球被轻轻吐在拖鞋边。"},
                "asset_refs": [
                    {"label": "小华", "asset_type": "character", "status": "mentioned", "source": "explicit"},
                ],
            },
        ]
    }

    shots = shots_from_provider_text(json.dumps(payload, ensure_ascii=False), source_script_text=source_script)

    shot2_refs = {item["label"]: item for item in shots[1]["asset_refs"]}
    shot3_refs = {item["label"]: item for item in shots[2]["asset_refs"]}
    shot2_dropped = shots[1]["dropped_asset_ref_diagnostics"]

    assert shot2_refs["黑色拉布拉多"]["character_subtype"] == "animal"
    assert shot2_refs["荧光绿网球"]["asset_type"] == "prop"
    assert not any(item["display_name"] == "荧光绿网球" for item in shot2_dropped)
    assert "小华" in shot3_refs
    assert shot3_refs["黑色拉布拉多"]["character_subtype"] == "animal"
    assert shot3_refs["黑色拉布拉多"]["source"] == "cross_shot_coreference"


def test_storyboard_provider_parser_supplements_grounded_scene_and_key_props_when_provider_omits_them() -> None:
    span1 = (
        "暴雨倾泻，泥浆翻涌的古战场俯拍全景；沈砚单膝深陷泥中，右臂青筋暴起，"
        "死攥半截断戟，指节泛白；左肩甲裂开焦痕，血混雨水淌入衣领褶皱"
    )
    span5 = "他咬牙撑戟欲起，断戟忽震，嗡鸣刺耳，戟尖泥下赫然露出半枚青铜虎符，刻纹凸起"
    payload = {
        "shots": [
            {
                "shot_id": "shot_01",
                "index": 1,
                "duration": "2.8",
                "description": span1,
                "shot_size": "全景",
                "light_atmosphere": "冷灰主调",
                "camera_motion": "缓慢下压俯角",
                "dialogue": "无明确对白",
                "sound": "暴雨轰鸣",
                "source_span": {"text": span1},
                "asset_refs": [
                    {"label": "沈砚", "asset_type": "character", "status": "mentioned", "source": "explicit"}
                ],
            },
            {
                "shot_id": "shot_05",
                "index": 5,
                "duration": "2.3",
                "description": "低角度特写：沈砚咬牙撑戟欲起，断戟突然震颤嗡鸣；戟尖下方赫然露出半枚青铜虎符。",
                "shot_size": "特写",
                "light_atmosphere": "虎符表面湿漉反光",
                "camera_motion": "急速下摇",
                "dialogue": "无明确对白",
                "sound": "断戟高频嗡鸣",
                "source_span": {"text": span5},
                "asset_refs": [],
            },
        ]
    }

    shots = shots_from_provider_text(
        json.dumps(payload, ensure_ascii=False),
        source_script_text=f"{span1}。{span5}。",
    )
    serialized = json.dumps(shots, ensure_ascii=False)
    shot1_refs = {(item["label"], item["asset_type"]) for item in shots[0]["asset_refs"]}
    shot5_refs = {(item["label"], item["asset_type"]) for item in shots[1]["asset_refs"]}

    assert ("沈砚", "character") in shot1_refs
    assert ("古战场", "scene") in shot1_refs
    assert ("断戟", "prop") in shot1_refs
    assert ("断戟", "prop") in shot5_refs
    assert ("青铜虎符", "prop") in shot5_refs
    assert "山巅石台战场" not in serialized
    assert "可见人物" not in serialized
