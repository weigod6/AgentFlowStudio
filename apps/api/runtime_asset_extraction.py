from __future__ import annotations

import re
from typing import Any


ASSET_TYPES = {"character", "scene", "prop"}
CHARACTER_SUBTYPES = {"human", "animal", "robot", "subject"}
GENERIC_CHARACTER_LABELS = {"人", "人物", "主角", "角色", "主体"}
GENERIC_SCENE_LABELS = {"场景", "主要场景"}
PRONOUN_LABELS = {"他", "她", "它", "他们", "她们", "ta", "they", "he", "she"}
ANIMAL_REFERENCE_TERMS = (
    "拉布拉多",
    "金毛",
    "边牧",
    "柯基",
    "哈士奇",
    "柴犬",
    "奶狗",
    "幼犬",
    "小狗",
    "狗狗",
    "橘猫",
    "狸花猫",
    "黑猫",
    "白猫",
    "小猫",
    "猫咪",
    "猫",
    "狗",
    "犬",
    "兔",
    "鸟",
    "马",
    "鹿",
    "狐",
    "狼",
    "熊",
    "虎",
    "狮",
    "蛇",
    "龙",
    "鱼",
    "cat",
    "dog",
    "puppy",
    "kitten",
    "animal",
    "pet",
)
PROP_REFERENCE_TERMS = (
    "荧光绿网球",
    "网球",
    "红绳",
    "牵引绳",
    "狗绳",
    "毛线团",
    "项圈",
    "断绳",
    "断戟",
    "青铜虎符",
    "虎符",
    "竹简",
    "军旗",
    "残旗",
    "旧军籍册",
    "军籍册",
    "试卷",
    "草稿纸",
    "寻狗启事",
    "启事",
    "手机",
    "地图",
    "钥匙",
    "信件",
    "信封",
    "照片",
    "金箍棒",
    "钢爪",
    "刀",
    "剑",
    "棍",
    "棒",
    "武器",
    "道具",
)
GENERIC_PROP_NOUN_TERMS = (
    "数学试卷",
    "试卷",
    "草稿纸",
    "纸张",
    "启事",
    "寻狗启事",
    "照片",
    "信件",
    "信封",
    "手机",
    "钥匙",
    "地图",
    "竹简",
    "虎符",
    "军旗",
    "残旗",
    "旗",
    "断戟",
    "戟",
    "剑",
    "刀",
    "枪",
    "弓",
    "棍",
    "棒",
    "网球",
    "球",
    "红绳",
    "牵引绳",
    "狗绳",
    "绳",
    "毛线团",
    "纸盒",
    "纸箱",
    "项圈",
    "断绳",
    "香炉",
    "面包",
    "耳机线",
    "雨伞",
    "伞",
)
KEY_PROP_ACTION_TERMS = (
    "手持",
    "死攥",
    "攥",
    "握",
    "拿",
    "捧",
    "叼",
    "吐",
    "顶",
    "勾",
    "勾着",
    "拾起",
    "翻转",
    "展开",
    "散开",
    "露出",
    "震颤",
    "嗡鸣",
    "照亮",
    "反射",
    "检查",
    "查看",
    "写着",
    "批注",
    "锁定",
    "递",
)
KEY_PROP_LABEL_TERMS = (
    "断戟",
    "青铜虎符",
    "虎符",
    "竹简",
    "军旗",
    "残旗",
    "旧军籍册",
    "军籍册",
    "金箍棒",
    "钢爪",
    "荧光绿网球",
    "网球",
    "红绳",
    "牵引绳",
    "狗绳",
    "寻狗启事",
    "启事",
    "地图",
)
HUMAN_REFERENCE_TERMS = (
    "高中生",
    "学生",
    "女孩",
    "女生",
    "少女",
    "男孩",
    "少年",
    "女人",
    "男人",
    "阿姨",
    "老师",
    "人物",
    "人类",
    "person",
    "human",
    "girl",
    "boy",
    "woman",
    "man",
)
ROBOT_REFERENCE_TERMS = ("机器人", "机械人", "仿生人", "机甲", "robot", "android", "mecha")

AUDIO_ONLY_TERMS = (
    "城市噪音",
    "城市环境底噪",
    "环境底噪",
    "底噪",
    "噪音",
    "环境音",
    "ambience",
    "ambient",
    "city noise",
    "distant city noise",
    "audio",
    "sound",
    "black screen",
)
CITY_TERMS = ("城市", "city", "街道", "street", "road")
KNOWN_CHARACTER_NAMES = ("唐僧", "白骨精", "孙悟空", "猪八戒", "沙僧", "金刚狼", "林晚")
VISUAL_CITY_TERMS = (
    "rain-night city street",
    "city street",
    "skyline",
    "building",
    "buildings",
    "neon",
    "wet road",
    "visible lights",
    "rooftop",
    "雨夜",
    "街道",
    "屋顶",
    "天际线",
    "建筑",
    "高楼",
    "霓虹",
    "湿路",
    "路面",
    "灯光",
)
VISUAL_CHARACTER_TERMS = (
    "walks",
    "runs",
    "face",
    "coat",
    "hand",
    "under the visible lights",
    "站",
    "走",
    "奔跑",
    "穿",
    "低头",
    "手部",
    "展开",
    "外套",
    "侧脸",
    "女孩",
    "林晚",
    "机器人",
    "唐僧",
    "白骨精",
    "孙悟空",
    "猪八戒",
)
ACTION_FRAGMENT_LABEL_TERMS = (
    "挣脱",
    "转身",
    "轻巧",
    "跃下",
    "落地",
    "掏出",
    "本能",
    "悬停",
    "低头",
    "抬头",
    "回头",
    "侧身",
    "伸手",
    "抬手",
    "咬牙",
)
BODY_PART_LABEL_TERMS = (
    "右眼",
    "左眼",
    "瞳孔",
    "眼睛",
    "指尖",
    "手指",
    "指节",
    "爪子",
    "耳朵",
    "鼻尖",
    "鼻头",
    "喉结",
    "下颌",
    "肩",
    "手腕",
    "后颈",
)
NON_CHARACTER_LABEL_TERMS = (
    "手机",
    "屏幕",
    "试卷",
    "草稿",
    "启事",
    "断戟",
    "虎符",
    "竹简",
    "军旗",
    "残旗",
)


def normalize_asset_refs_with_diagnostics(
    asset_refs: list[Any],
    *,
    context: str = "",
    include_inferred: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates = [item for item in asset_refs if isinstance(item, dict)]
    if include_inferred:
        candidates = [*candidates, *_inferred_asset_refs(context)]

    accepted: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for index, candidate in enumerate(candidates):
        normalized, diagnostic = normalize_asset_ref_for_contract(candidate, index, context=context)
        if normalized:
            key = (normalized["asset_type"], normalized["display_name"])
            if key in seen:
                continue
            seen.add(key)
            accepted.append(normalized)
        elif diagnostic:
            diagnostic_key = (diagnostic["asset_type"], diagnostic["display_name"], diagnostic["reason"])
            if diagnostic_key not in {(item["asset_type"], item["display_name"], item["reason"]) for item in diagnostics}:
                diagnostics.append(diagnostic)
    return _drop_subsumed_asset_refs(accepted), diagnostics


def principal_asset_refs_with_diagnostics(
    asset_refs: list[dict[str, Any]],
    dropped_refs: list[dict[str, Any]] | None = None,
    *,
    max_auto_characters: int = 2,
    max_auto_scenes: int = 1,
    max_auto_props: int = 2,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    accepted: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = list(dropped_refs or [])
    auto_character_count = 0
    auto_scene_count = 0
    auto_prop_count = 0
    for ref in asset_refs:
        asset_type = str(ref.get("asset_type") or "")
        if _is_manual_or_fixed_asset_ref(ref):
            accepted.append(ref)
            continue
        explicit_named = _is_explicit_named_asset_ref(ref)
        if asset_type == "prop":
            if auto_prop_count < max_auto_props and _is_key_prop_ref(ref):
                accepted.append({**ref, "status": str(ref.get("status") or "candidate")})
                auto_prop_count += 1
            else:
                diagnostics.append(_principal_diagnostic(ref, "prop_requires_manual_asset_entry"))
            continue
        if asset_type == "character":
            if explicit_named or auto_character_count < max_auto_characters:
                accepted.append(ref)
                if not explicit_named:
                    auto_character_count += 1
            else:
                diagnostics.append(_principal_diagnostic(ref, "secondary_character_requires_manual_asset_entry"))
            continue
        if asset_type == "scene":
            if explicit_named or auto_scene_count < max_auto_scenes:
                accepted.append(ref)
                if not explicit_named:
                    auto_scene_count += 1
            else:
                diagnostics.append(_principal_diagnostic(ref, "secondary_scene_requires_manual_asset_entry"))
            continue
        diagnostics.append(_principal_diagnostic(ref, "unsupported_asset_type_requires_manual_entry"))
    return accepted, diagnostics


def normalize_asset_ref_for_contract(
    asset: dict[str, Any],
    index: int,
    *,
    context: str = "",
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    asset_type = _asset_type(asset.get("asset_type"))
    raw_label = _clean_label(asset.get("display_name") or asset.get("label") or asset.get("name") or "")
    if not raw_label:
        return None, None

    evidence = _clean_text(asset.get("evidence_text") or asset.get("visual_evidence_span") or context)
    context_text = _clean_text(context or evidence)
    display_name = raw_label
    provisional_name = bool(asset.get("provisional_name"))
    name_source = str(asset.get("name_source") or asset.get("source") or "candidate")

    if asset_type == "character" and _looks_like_prop_reference(raw_label, evidence, context_text):
        asset_type = "prop"
        display_name = _clean_prop_label(raw_label) or raw_label
    elif asset_type == "prop" and _looks_like_prop_phrase_label(display_name):
        display_name = _clean_prop_label(display_name) or display_name

    if asset_type == "scene" and _is_audio_only_city_reference(raw_label, evidence, context_text):
        return None, _diagnostic(raw_label, asset_type, "audio_only_non_visual_city_reference", evidence or context_text)

    if asset_type == "character" and raw_label in PRONOUN_LABELS:
        return None, _diagnostic(raw_label, asset_type, "ambiguous_alias_not_auto_merged", evidence or context_text)

    if asset_type == "character" and _looks_like_action_fragment_label(raw_label):
        return None, _diagnostic(raw_label, asset_type, "action_fragment_not_asset", evidence or context_text)

    if asset_type == "character" and raw_label in GENERIC_CHARACTER_LABELS:
        provisional = _provisional_character_name(context_text)
        if not provisional:
            return None, _diagnostic(raw_label, asset_type, "unresolved_generic_character", evidence or context_text)
        display_name = provisional
        provisional_name = True
        name_source = "visual_context_provisional"

    if asset_type == "scene" and raw_label in GENERIC_SCENE_LABELS:
        scene_name = _visual_scene_name(context_text)
        if not scene_name:
            return None, _diagnostic(raw_label, asset_type, "unresolved_generic_scene", evidence or context_text)
        display_name = scene_name
        provisional_name = True
        name_source = "visual_context_provisional"

    visual_span = _visual_evidence_span(context_text, evidence, display_name, asset_type)
    if asset_type == "scene" and _has_audio_only_terms(evidence or context_text) and not visual_span:
        return None, _diagnostic(raw_label, asset_type, "audio_only_non_visual_reference", evidence or context_text)
    if not visual_span and asset_type == "scene":
        visual_span = (evidence or context_text)[:240]
    evidence_modality = "visual"

    normalized = {
        "label": display_name,
        "display_name": display_name,
        "asset_id": str(asset.get("asset_id") or f"candidate:{asset_type}:{_slug(display_name)}"),
        "graph_asset_id": str(asset.get("graph_asset_id") or asset.get("graphAssetId") or ""),
        "asset_type": asset_type,
        "status": str(asset.get("status") or "candidate"),
        "source": str(asset.get("source") or "candidate"),
        "scope": str(asset.get("scope") or "shot_tree"),
        "confidence": _confidence(asset.get("confidence"), provisional_name=provisional_name),
        "evidence_text": (visual_span or evidence or context_text)[:240],
        "descriptive_signature": _descriptive_signature(asset, visual_span or evidence or context_text),
        "evidence_modality": evidence_modality,
        "visual_evidence_span": visual_span,
        "modality_gate_status": "accepted",
        "name_source": name_source,
        "provisional_name": provisional_name,
    }
    character_subtype = _character_subtype(asset.get("character_subtype"))
    if asset_type == "character" and not character_subtype:
        character_subtype = _inferred_character_subtype(display_name, evidence or context_text)
    if asset_type == "character" and character_subtype:
        normalized["character_subtype"] = character_subtype
    return normalized, None


def _inferred_asset_refs(context: str) -> list[dict[str, Any]]:
    text = _clean_text(context)
    refs: list[dict[str, Any]] = []
    for name in _named_characters(text):
        refs.append({"label": name, "asset_type": "character", "source": "candidate", "evidence_text": text})
    for name in _named_animal_characters(text):
        refs.append(
            {
                "label": name,
                "asset_type": "character",
                "character_subtype": "animal",
                "source": "grounded_mention",
                "evidence_text": text,
            }
        )
    scene_name = _visual_scene_name(text)
    if scene_name:
        refs.append({"label": scene_name, "asset_type": "scene", "source": "candidate", "evidence_text": text})
    for name in _visual_prop_names(text):
        refs.append(
            {
                "label": name,
                "asset_type": "prop",
                "status": "prop_relevant",
                "source": "grounded_mention",
                "evidence_text": text,
            }
        )
    return _drop_subsumed_asset_refs(refs)


def _specific_asset_types(candidates: list[dict[str, Any]]) -> set[str]:
    result: set[str] = set()
    for item in candidates:
        asset_type = _asset_type(item.get("asset_type"))
        label = _clean_label(item.get("display_name") or item.get("label") or item.get("name") or "")
        if label and label not in GENERIC_CHARACTER_LABELS and label not in GENERIC_SCENE_LABELS and label not in PRONOUN_LABELS:
            result.add(asset_type)
    return result


def _named_characters(text: str) -> list[str]:
    names: list[str] = []
    for left, right in re.findall(
        r"([\u4e00-\u9fffA-Za-z0-9·]{2,12})(?:大战|对决|迎娶|娶了|娶|嫁给|爱上|遇见|面对|追击|追杀|营救|守护)([\u4e00-\u9fffA-Za-z0-9·]{2,12})",
        text,
    ):
        names.extend([_trim_character_name(left), _trim_character_name(right)])
    names.extend(_known_characters_in_source_order(text))
    names.extend(_action_bound_character_names(text))
    if re.search(r"\bLin\s+Wan\b", text, flags=re.I):
        names.append("Lin Wan")
    if "女孩" in text:
        names.append("女孩")
    if "机器人" in text:
        names.append("机器人")
    if re.search(r"\bfuture robot\b|\brobot\b", text, flags=re.I):
        names.append("Future Robot")
    return _dedupe([name for name in names if name])


def _action_bound_character_names(text: str) -> list[str]:
    names: list[str] = []
    action_re = re.compile(
        r"(?<![\u4e00-\u9fff])([\u4e00-\u9fff]{2,4}?)(?="
        r"单膝|双膝|抬头|低头|转身|侧身|回头|凝视|望向|看向|站|蹲|跪|坐|"
        r"走|跑|追|冲|跃|扑|伸手|抬手|握|攥|死攥|拿|捧|抱|咬牙|喉结|瞳孔|"
        r"肩|右臂|左臂|指节|手指|下颌|呼吸|开口|呛出|怔住|愣住"
        r")"
    )
    for match in action_re.finditer(str(text or "")):
        name = _trim_character_name(match.group(1))
        if _looks_like_character_name(name):
            names.append(name)
    return _dedupe(names)


def _looks_like_character_name(value: str) -> bool:
    clean = str(value or "").strip()
    if not clean or clean in GENERIC_CHARACTER_LABELS or clean in GENERIC_SCENE_LABELS:
        return False
    if clean in PRONOUN_LABELS:
        return False
    if _contains_any(
        clean,
        (
            "暴雨",
            "泥浆",
            "古战场",
            "战场",
            "城墙",
            "城垛",
            "雷声",
            "雨声",
            "镜头",
            "画面",
            "远处",
            "血色",
            "残旗",
            "军旗",
            "断戟",
            "虎符",
            "竹简",
            "试卷",
            "草稿",
            "启事",
            "挣脱",
            "转身",
            "轻巧",
            "怀抱",
            "右眼",
            "左眼",
            "屏幕",
        ),
    ):
        return False
    if _looks_like_action_fragment_label(clean):
        return False
    return bool(re.fullmatch(r"[\u4e00-\u9fff]{2,4}", clean))


def _looks_like_action_fragment_label(value: str) -> bool:
    clean = str(value or "").strip()
    if not clean:
        return False
    if re.search(r"^(?:他|她|它|这|那|其|我|你)", clean):
        return True
    if _contains_any(clean, ACTION_FRAGMENT_LABEL_TERMS):
        return True
    if _contains_any(clean, BODY_PART_LABEL_TERMS):
        return True
    if _contains_any(clean, NON_CHARACTER_LABEL_TERMS):
        return True
    return False


def _looks_like_prop_phrase_label(value: str) -> bool:
    clean = str(value or "").strip()
    if not clean:
        return False
    if re.search(r"^(?:他|她|它|这|那|其|我|你)", clean):
        return True
    return _contains_any(
        clean,
        (
            "掏出",
            "叼着",
            "吐出",
            "吐在",
            "捧着",
            "拿着",
            "握着",
            "攥着",
            "撑着",
            "拾起",
            "翻转",
            "露出",
            "放在",
            "压住",
            "勾着",
        ),
    )


def _named_animal_characters(text: str) -> list[str]:
    source = str(text or "")
    names: list[str] = []
    quoted_alias = re.compile(
        r"(?:拉布拉多|金毛|边牧|柯基|哈士奇|柴犬|奶狗|幼犬|小狗|狗狗|橘猫|狸花猫|黑猫|白猫|小猫|猫咪|猫|狗|犬)[“\"]([\u4e00-\u9fffA-Za-z0-9·]{1,8})[”\"]"
    )
    names.extend(match.group(1) for match in quoted_alias.finditer(source))
    breed_pattern = re.compile(
        r"((?:黑色|白色|灰色|棕色|黄色|金色|橘色|灰白相间|黑白相间)?(?:拉布拉多|金毛|边牧|柯基|哈士奇|贵宾犬|萨摩耶|柴犬)(?:幼崽|幼犬)?)"
    )
    names.extend(match.group(1) for match in breed_pattern.finditer(source))
    longer_species_present = any(term in source for term in ("拉布拉多", "金毛", "边牧", "柯基", "哈士奇", "柴犬", "奶狗", "幼犬", "小狗", "橘猫", "狸花猫", "黑猫", "白猫", "小猫"))
    for term in ("奶狗", "幼犬", "小狗", "狗狗", "橘猫", "狸花猫", "黑猫", "白猫", "小猫", "猫咪", "猫", "狗", "犬"):
        if term in source and (len(term) > 1 or not longer_species_present):
            names.append(term)
    return _dedupe([name for name in names if name and name not in PRONOUN_LABELS])


def _known_characters_in_source_order(text: str) -> list[str]:
    return [
        name
        for name, _index in sorted(
            ((name, text.find(name)) for name in KNOWN_CHARACTER_NAMES if text.find(name) >= 0),
            key=lambda item: item[1],
        )
    ]


def _trim_character_name(value: str) -> str:
    clean = re.sub(r"^(以|把|将|当|用|和|与|及|、)+", "", str(value or "")).strip()
    clean = re.sub(r"^.*(?:是|讲述|关于|围绕)", "", clean).strip()
    clean = re.sub(r"(为核心|为主题|为主|展开|对决|战斗|格斗|碰撞).*$", "", clean).strip()
    clean = re.sub(r"(但是|但|却|旁观|观战|从旁).*$", "", clean).strip()
    return clean[:24]


def _provisional_character_name(text: str) -> str:
    for name in _named_characters(text):
        if name not in {"他", "她", "它"}:
            return name
    lowered = text.lower()
    if any(term in text for term in ("红色外套", "侧脸", "霓虹")):
        return "红色外套人物"
    if "女孩" in text:
        return "女孩"
    if "robot" in lowered or "机器人" in text:
        return "Future Robot" if "robot" in lowered else "机器人"
    if _has_visual_character_context(text):
        return "可见人物"
    return ""


def _visual_scene_name(text: str) -> str:
    if _has_negated_visual_context(text):
        return ""
    lowered = text.lower()
    grounded_scene = _grounded_scene_name(text)
    if grounded_scene:
        return grounded_scene
    if "rain-night city street" in lowered:
        return "rain-night city street"
    if "city street" in lowered or ("street" in lowered and "city" in lowered):
        return "city street"
    if "rooftop" in lowered and "city" in lowered:
        return "city rooftop"
    if "雨夜" in text and ("城市" in text or "街道" in text):
        return "雨夜城市街道"
    if "城市" in text and "屋顶" in text:
        return "城市屋顶"
    if "城市" in text and any(term in text for term in ("街道", "天际线", "建筑", "高楼", "霓虹", "湿路", "路面", "灯光")):
        return "城市街道"
    return ""


def _grounded_scene_name(text: str) -> str:
    source = str(text or "")
    if "古战场" in source:
        return "古战场"
    if "老城区巷口" in source:
        return "老城区巷口"
    if "斜坡草甸" in source:
        return "斜坡草甸"
    if re.search(r"山巅|山脊|云海", source) and "战场" in source:
        return "山巅石台战场"
    if "战场" in source:
        return "战场"
    patterns = (
        r"([\u4e00-\u9fffA-Za-z0-9·]{0,10}(?:校门口|巷口|窄巷|巷子|公园长椅旁|公园长椅|公园|草甸|草坪|厨房|房间|屋顶|楼顶|天台|城墙|城垛|街道|走廊|宫殿|庭院|广场|餐厅|山洞|洞口|洞内))",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, source):
            label = _clean_scene_label(match.group(1))
            if label:
                return label
    return ""


def _clean_scene_label(value: str) -> str:
    clean = re.sub(r"^(?:在|从|向|朝|远处|路对面|空荡|焦黑|破碎|湿漉漉|梧桐树影斑驳的)+", "", str(value or "")).strip()
    clean = re.sub(r"^.*(?:站在|坐在|蹲在|躺在|停在|来到|走进|冲向|落在|映着|在)", "", clean).strip()
    clean = re.sub(r"(?:上|里|中|旁|边|外|内)$", lambda m: m.group(0) if clean.endswith(("旁", "边")) else "", clean)
    if clean in GENERIC_SCENE_LABELS or len(clean) < 2:
        return ""
    if clean in {"青石台阶", "青砖", "石台"}:
        return ""
    return clean[:24]


def _visual_prop_names(text: str) -> list[str]:
    source = str(text or "")
    names: list[str] = []
    terms = sorted(PROP_REFERENCE_TERMS, key=len, reverse=True)
    for term in terms:
        if term and term in source:
            names.append(_clean_prop_label(term))
    names.extend(_generic_visual_prop_names(source))
    object_pattern = re.compile(
        r"(?:半截|半枚|一卷|一张|一只|一柄|一根|那柄|那张|那只|那截)?([\u4e00-\u9fffA-Za-z0-9·]{0,8}(?:断戟|青铜虎符|虎符|竹简|军旗|残旗|军籍册|试卷|草稿纸|寻狗启事|启事|网球|红绳|牵引绳|狗绳|毛线团|金箍棒|钢爪|地图|钥匙))"
    )
    names.extend(_clean_prop_label(match.group(1)) for match in object_pattern.finditer(source))
    return _dedupe_non_overlapping([name for name in names if name])[:4]


def _generic_visual_prop_names(text: str) -> list[str]:
    source = str(text or "")
    if not source:
        return []
    noun_pattern = "|".join(re.escape(term) for term in sorted(GENERIC_PROP_NOUN_TERMS, key=len, reverse=True))
    context_prefix = (
        r"(?:手中|手里|嘴里|怀里|脚边|身旁|面前|指尖|掌心|画面中|镜头中|"
        r"叼着|吐出|吐在|捧着|拿着|握着|攥着|撑着|拾起|翻转|露出|放在|顶了顶|压住|反射|写着|批注)"
    )
    generic_re = re.compile(
        rf"(?:{context_prefix})[^\u3002\uff01\uff1f!?；;]{{0,18}}?"
        rf"((?:[\u4e00-\u9fff]{{0,8}})?(?:{noun_pattern}))"
    )
    names = [
        name
        for match in generic_re.finditer(source)
        if (name := _clean_prop_label(match.group(1))) and not _is_animal_alias_name(name, source)
    ]
    measure_re = re.compile(
        rf"(?:一|半|那|这|其)?(?:个|只|张|卷|枚|截|根|柄|把|块|团|盒|箱)?"
        rf"((?:[\u4e00-\u9fff]{{0,8}})?(?:{noun_pattern}))"
    )
    for match in measure_re.finditer(source):
        window = source[max(0, match.start() - 16) : min(len(source), match.end() + 16)]
        if _contains_any(window, KEY_PROP_ACTION_TERMS) or re.search(r"手中|手里|嘴里|怀里|脚边|面前|画面|镜头", window):
            name = _clean_prop_label(match.group(1))
            if name and not _is_animal_alias_name(name, source):
                names.append(name)
    return _dedupe_non_overlapping([name for name in names if name])


def _is_animal_alias_name(label: str, text: str) -> bool:
    clean = re.escape(str(label or "").strip())
    if not clean:
        return False
    animal_pattern = "|".join(re.escape(term) for term in sorted(ANIMAL_REFERENCE_TERMS, key=len, reverse=True))
    return bool(re.search(rf"(?:{animal_pattern})[“\"']{clean}[”\"']", str(text or ""), flags=re.I))


def _clean_prop_label(value: str) -> str:
    clean = re.sub(
        r"^(?:叼着|吐出|吐在|捧着|拿着|握着|攥着|撑着|拾起|翻转|露出|放在|顶了顶|压住|反射|写着|批注|捏着|"
        r"磨损严重的|没吃完的|湿漉漉的|湿透的|湿透|褪色的|褪色|发光的|发光|半截|半枚|一卷|一张|一只|一柄|一根|一块|一团|一盒|一箱|那柄|那张|那只|那截|那根|这根|这张|这只)+",
        "",
        str(value or ""),
    ).strip()
    for term in sorted((*KEY_PROP_LABEL_TERMS, *PROP_REFERENCE_TERMS), key=len, reverse=True):
        if term and term in clean:
            return term[:24]
    for term in sorted(GENERIC_PROP_NOUN_TERMS, key=len, reverse=True):
        if term and clean.endswith(term):
            return clean[-min(len(clean), len(term) + 8) :][:24]
    return clean[:24]


def _dedupe_non_overlapping(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in sorted(_dedupe(values), key=len, reverse=True):
        if any(value != other and value in other for other in result):
            continue
        result.append(value)
    return sorted(result, key=lambda item: values.index(item))


def _drop_subsumed_asset_refs(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for ref in refs:
        label = str(ref.get("label") or ref.get("display_name") or "").strip()
        asset_type = str(ref.get("asset_type") or "")
        if not label:
            continue
        if any(
            asset_type == str(other.get("asset_type") or "")
            and label != str(other.get("label") or other.get("display_name") or "")
            and label in str(other.get("label") or other.get("display_name") or "")
            for other in refs
        ):
            continue
        result.append(ref)
    return result


def _visual_evidence_span(context: str, evidence: str, display_name: str, asset_type: str) -> str:
    source = _clean_text(context or evidence)
    if not source:
        return ""
    candidates = [part.strip() for part in re.split(r"(?<=[。！？.!?])\s*", source) if part.strip()]
    candidates = candidates or [source]
    for sentence in candidates:
        if display_name and display_name in sentence:
            return sentence[:240]
    if asset_type == "scene":
        for sentence in candidates:
            if _has_visual_city_context(sentence):
                return sentence[:240]
    if asset_type == "character":
        for sentence in candidates:
            if _has_visual_character_context(sentence):
                return sentence[:240]
    if asset_type == "prop":
        return candidates[0][:240]
    return ""


def _is_audio_only_city_reference(label: str, evidence: str, context: str) -> bool:
    text = f"{label} {evidence} {context}"
    return _has_city_terms(text) and _has_audio_only_terms(text) and not _has_visual_city_context(text)


def _has_city_terms(text: str) -> bool:
    lowered = text.lower()
    return any(term in lowered if term.isascii() else term in text for term in CITY_TERMS)


def _has_audio_only_terms(text: str) -> bool:
    lowered = text.lower()
    return any(term in lowered if term.isascii() else term in text for term in AUDIO_ONLY_TERMS)


def _has_visual_city_context(text: str) -> bool:
    if _has_negated_visual_context(text):
        return False
    lowered = text.lower()
    return any(term in lowered if term.isascii() else term in text for term in VISUAL_CITY_TERMS)


def _has_visual_character_context(text: str) -> bool:
    if _has_negated_visual_context(text):
        return False
    lowered = text.lower()
    return any(term in lowered if term.isascii() else term in text for term in VISUAL_CHARACTER_TERMS)


def _has_negated_visual_context(text: str) -> bool:
    lowered = text.lower()
    return any(
        term in lowered if term.isascii() else term in text
        for term in (
            "没有可见",
            "不可见",
            "无可见",
            "没有画面",
            "no visible",
            "not visible",
            "black screen",
        )
    )


def _looks_like_prop_reference(label: str, evidence: str, context: str) -> bool:
    text = f"{label} {evidence} {context}".casefold()
    label_text = str(label or "").casefold()
    if _contains_any(label_text, PROP_REFERENCE_TERMS):
        return True
    if label_text in {"球", "ball"} and re.search(r"网球|球面|球体|吐在|叼着|tennis\s+ball", text, flags=re.I):
        return True
    return False


def _is_key_prop_ref(ref: dict[str, Any]) -> bool:
    label = str(ref.get("display_name") or ref.get("label") or "").strip()
    evidence = _clean_text(
        " ".join(
            str(ref.get(key) or "")
            for key in ("evidence_text", "visual_evidence_span", "descriptive_signature", "source_text")
        )
    )
    status = str(ref.get("status") or "").lower()
    source = str(ref.get("source") or "").lower()
    if not label:
        return False
    if _contains_any(label, KEY_PROP_LABEL_TERMS) and (label in evidence or "explicit" in source or status in {"mentioned", "prop_relevant", "key_prop"}):
        return True
    window = f"{label} {evidence}"
    if _contains_any(window, KEY_PROP_ACTION_TERMS) and _contains_any(label, PROP_REFERENCE_TERMS):
        return True
    if status in {"prop_relevant", "key_prop"} and label in evidence:
        return True
    return False


def _inferred_character_subtype(label: str, evidence: str) -> str:
    text = f"{label} {evidence}"
    lowered = text.casefold()
    if _contains_any(lowered, ROBOT_REFERENCE_TERMS):
        return "robot"
    if _looks_like_animal_label(label) or _animal_alias_bound_to_label(label, evidence):
        return "animal"
    if _contains_any(f"{label} {evidence}", HUMAN_REFERENCE_TERMS):
        return "human"
    return ""


def _looks_like_animal_label(label: str) -> bool:
    return _contains_any(str(label or ""), ANIMAL_REFERENCE_TERMS)


def _animal_alias_bound_to_label(label: str, evidence: str) -> bool:
    clean = re.escape(str(label or "").strip())
    if not clean:
        return False
    animal_pattern = "|".join(re.escape(term) for term in sorted(ANIMAL_REFERENCE_TERMS, key=len, reverse=True))
    return bool(
        re.search(rf"(?:{animal_pattern})[“\"']{clean}[”\"']", evidence, flags=re.I)
        or re.search(rf"{clean}[^，。；,;\n]{{0,12}}(?:是一只|这只|那只)(?:{animal_pattern})", evidence, flags=re.I)
    )


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    lowered = str(text or "").casefold()
    return any(term.casefold() in lowered for term in terms)


def _diagnostic(label: str, asset_type: str, reason: str, evidence: str) -> dict[str, Any]:
    return {
        "label": label,
        "display_name": label,
        "asset_type": asset_type,
        "reason": reason,
        "evidence_text": _clean_text(evidence)[:240],
        "evidence_modality": "audio" if _has_audio_only_terms(evidence) else "textual",
        "modality_gate_status": "held",
    }


def _principal_diagnostic(ref: dict[str, Any], reason: str) -> dict[str, Any]:
    label = str(ref.get("display_name") or ref.get("label") or "").strip()
    asset_type = str(ref.get("asset_type") or "prop").strip() or "prop"
    evidence = str(ref.get("evidence_text") or ref.get("visual_evidence_span") or "")
    return _diagnostic(label, asset_type, reason, evidence)


def _is_manual_or_fixed_asset_ref(ref: dict[str, Any]) -> bool:
    return (
        str(ref.get("source") or "").lower() == "manual"
        or str(ref.get("status") or "").lower() == "fixed"
        or bool(ref.get("graph_asset_id") or ref.get("graphAssetId"))
    )


def _is_explicit_named_asset_ref(ref: dict[str, Any]) -> bool:
    asset_type = str(ref.get("asset_type") or "")
    if asset_type == "prop":
        return False
    source = str(ref.get("source") or "").lower()
    status = str(ref.get("status") or "").lower()
    if "explicit" not in source and status != "mentioned":
        return False
    label = str(ref.get("display_name") or ref.get("label") or "").strip()
    return label not in GENERIC_CHARACTER_LABELS and label not in GENERIC_SCENE_LABELS


def _descriptive_signature(asset: dict[str, Any], fallback: str) -> str:
    return _clean_text(
        asset.get("descriptive_signature")
        or asset.get("signature")
        or asset.get("visual_description_seed")
        or fallback
    )[:240]


def _asset_type(value: Any) -> str:
    asset_type = str(value or "").strip()
    aliases = {
        "角色": "character",
        "人物": "character",
        "动物角色": "character",
        "animal_character": "character",
        "scene": "scene",
        "场景": "scene",
        "location": "scene",
        "prop": "prop",
        "道具": "prop",
        "object": "prop",
        "item": "prop",
    }
    normalized = aliases.get(asset_type) or aliases.get(asset_type.lower())
    if normalized:
        return normalized
    return asset_type if asset_type in ASSET_TYPES else "character"


def _character_subtype(value: Any) -> str:
    subtype = str(value or "").strip()
    return subtype if subtype in CHARACTER_SUBTYPES else ""


def _confidence(value: Any, *, provisional_name: bool = False) -> float:
    if isinstance(value, (int, float)):
        return max(0.0, min(float(value), 1.0))
    return 0.72 if provisional_name else 0.82


def _clean_label(value: Any) -> str:
    return re.sub(r"^[\s@]+|[\s，。；:：.!?！？]+$", "", str(value or "")).strip()[:40]


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _slug(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", str(value or "")).lower()[:48] or "asset"


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


__all__ = (
    "GENERIC_CHARACTER_LABELS",
    "GENERIC_SCENE_LABELS",
    "normalize_asset_ref_for_contract",
    "normalize_asset_refs_with_diagnostics",
    "principal_asset_refs_with_diagnostics",
)
