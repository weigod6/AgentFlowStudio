from __future__ import annotations

import re
from typing import Any

from apps.api.runtime_asset_extraction import (
    normalize_asset_ref_for_contract,
    normalize_asset_refs_with_diagnostics,
    principal_asset_refs_with_diagnostics,
)
from apps.api.runtime_storyboard_grounding import (
    grounding_status_for_unsupported,
    storyboard_source_span,
    unsupported_additions_for_description,
)
from apps.api.runtime_storyboard_asset_coverage import reconcile_storyboard_asset_coverage
from apps.api.runtime_storyboard_planning import storyboard_plan_fields


ASSET_RE = re.compile(r"@([A-Za-z0-9_\-\u4e00-\u9fff·]+)")
SCENE_HINTS = ("主要场景", "场景", "办公室", "房间", "街道", "巷口", "窄巷", "巷子", "青石台阶", "青砖", "屋顶", "楼顶", "天台", "城市", "天际线", "森林", "海边", "山谷", "山巅", "山脊", "石台", "战场", "云海", "云栈洞口", "洞口", "洞内", "山洞", "餐厅", "车内", "走廊", "宫殿", "庭院", "广场", "屏幕")
KNOWN_CHARACTER_NAMES = ("唐僧", "白骨精", "孙悟空", "猪八戒", "沙僧", "金刚狼", "林晚")
CHARACTER_HINTS = ("主角", "角色", "人物", "女孩", "女生", "男孩", "女人", "男人", "老人", "孩子", "机器人", "队长", "老师", "学生", "皇帝", "侦探", *KNOWN_CHARACTER_NAMES)
ANIMAL_CHARACTER_HINTS = (
    "拉布拉多",
    "金毛",
    "边牧",
    "柯基",
    "哈士奇",
    "贵宾犬",
    "萨摩耶",
    "橘猫",
    "流浪猫",
    "狸花猫",
    "黑猫",
    "白猫",
    "小猫",
    "猫咪",
    "奶狗",
    "小狗",
    "幼犬",
    "柴犬幼崽",
    "柴犬",
    "狗狗",
    "猫",
    "狗",
    "犬",
)
ANIMAL_ENTITY_LABELS = tuple(sorted(ANIMAL_CHARACTER_HINTS, key=len, reverse=True))
HUMAN_ROLE_LABELS = ("邻居阿姨", "阿姨", "女人", "男人", "男孩", "女孩", "高中生", "学生", "老师", "老人", "孩子")
SPEECH_VERBS_RE = re.compile(r"(?:说|说道|喊|叫|问|答|低声|大喊|呼喊|喃喃|嘀咕|台词|对白|旁白)")
PROP_HINTS = ("荧光绿网球", "网球", "毛线团", "红绳", "牵引绳", "狗绳", "项圈", "断绳", "金箍棒", "手机", "电脑", "键盘", "刀", "剑", "棍", "棒", "车辆", "汽车", "信件", "信封", "信纸", "照片", "路灯", "台灯", "灯具", "灯柱", "书", "门", "地图")
GENERIC_CHARACTER_LABELS = {"主角", "角色", "人物"}
GENERIC_SCENE_LABELS = {"主要场景", "场景"}


def local_storyboard_shots(script_text: str, shot_count_hint: int | None = None) -> list[dict[str, Any]]:
    source = _clean(script_text)
    chunks = _script_chunks(script_text, shot_count_hint=shot_count_hint)
    global_refs = _asset_refs(source)
    total_count = len(chunks[:80])
    shots = [
        structured_shot(
            chunk,
            index + 1,
            global_refs=global_refs,
            full_source=source,
            total_count=total_count,
            shot_count_hint=shot_count_hint,
        )
        for index, chunk in enumerate(chunks[:80])
    ]
    return reconcile_storyboard_asset_coverage(shots)


def structured_shot(
    text: str,
    index: int,
    global_refs: list[dict[str, Any]] | None = None,
    *,
    full_source: str = "",
    total_count: int | None = None,
    shot_count_hint: int | None = None,
) -> dict[str, Any]:
    source = _clean(text)
    raw_refs = _resolve_shot_refs(source, _asset_refs(source), global_refs or [])
    refs, dropped_refs = normalize_asset_refs_with_diagnostics(raw_refs, context=source, include_inferred=True)
    refs, dropped_refs = principal_asset_refs_with_diagnostics(refs, dropped_refs)
    plan_fields = storyboard_plan_fields(source, index)
    description = _description_with_assets(source, refs)
    source_span = storyboard_source_span(source, full_source or source, index)
    unsupported = unsupported_additions_for_description(description, source_span["text"])
    return {
        "shot_id": f"shot_{index:02d}",
        "index": index,
        "duration": _duration(source),
        "description": description,
        "shot_size": _shot_size(source),
        "light_atmosphere": _lighting(source),
        "camera_motion": _camera_motion(source),
        "dialogue": _dialogue(source),
        "sound": _sound(source),
        "asset_refs": refs,
        "dropped_asset_ref_diagnostics": dropped_refs,
        "source_text": source,
        "source_span": source_span,
        "grounding_status": grounding_status_for_unsupported(unsupported),
        "unsupported_additions": unsupported,
        "planning_agent": {
            "agent_id": "storyboard_local_fallback",
            "mode": "deterministic_grounded_fallback",
            "dynamic_shot_count": shot_count_hint is None,
            "shot_count_hint": shot_count_hint,
            "resolved_shot_count": total_count,
            "evidence_policy": "source_span_required",
        },
        **plan_fields,
    }


def _resolve_shot_refs(source: str, refs: list[dict[str, Any]], global_refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    named_characters = [ref for ref in global_refs if ref["asset_type"] == "character" and ref["label"] not in GENERIC_CHARACTER_LABELS]
    named_scenes = [ref for ref in global_refs if ref["asset_type"] == "scene" and ref["label"] not in GENERIC_SCENE_LABELS]
    has_generic_character_ref = any(ref["asset_type"] == "character" and ref["label"] in GENERIC_CHARACTER_LABELS for ref in refs)
    has_group_coreference = bool(re.search(r"两人|二人|双方|对方|他们|她们|主角|主体", source))
    if named_characters and (has_generic_character_ref or has_group_coreference or any(ref["label"] in source for ref in named_characters)):
        refs = [ref for ref in refs if ref["asset_type"] != "character" or ref["label"] not in GENERIC_CHARACTER_LABELS]
        for ref in named_characters[:3]:
            if ref["label"] in source or (has_generic_character_ref and len(named_characters) == 1):
                _push_ref(refs, ref["label"], "character", "context", source)
    if named_scenes and any(ref["asset_type"] == "scene" and ref["label"] in GENERIC_SCENE_LABELS for ref in refs):
        refs = [ref for ref in refs if ref["asset_type"] != "scene" or ref["label"] not in GENERIC_SCENE_LABELS]
        _push_ref(refs, named_scenes[0]["label"], "scene", "context", source)
    return refs


def normalize_asset_ref(asset: Any, index: int, context: str = "") -> dict[str, Any]:
    if not isinstance(asset, dict):
        return {}
    normalized, _diagnostic = normalize_asset_ref_for_contract(asset, index, context=context)
    return normalized or {}


def _script_chunks(text: str, shot_count_hint: int | None = None) -> list[str]:
    source = str(text or "").strip()
    if not source:
        return []
    paragraphs = [_clean(part) for part in re.split(r"\n\s*\n", source) if _clean(part)]
    if len(paragraphs) > 1:
        units = paragraphs
    else:
        line_units = [_clean(part) for part in source.splitlines() if _clean(part)]
        if _looks_like_line_based_script(line_units):
            units = line_units
            target_count = _line_based_target_count(units, shot_count_hint)
            return _balanced_chunks(units, target_count)
        else:
            units = [_clean(part) for part in re.split(r"(?<=[。！？!?；;])\s*", source) if _clean(part)]
        if not units:
            return [source]
    target_count = _target_shot_count(units, source, shot_count_hint)
    return _balanced_chunks(units, target_count)


def _target_shot_count(units: list[str], source: str, shot_count_hint: int | None = None) -> int:
    if not units:
        return 0
    if shot_count_hint:
        return max(1, min(int(shot_count_hint), len(units), 80))
    by_units = (len(units) + 1) // 2
    by_length = max(1, (len(source) + 179) // 180)
    return max(1, min(max(by_units, by_length), len(units), 12))


def _looks_like_line_based_script(lines: list[str]) -> bool:
    if len(lines) < 6:
        return False
    meaningful = [line for line in lines if len(line) >= 8]
    if len(meaningful) < 6:
        return False
    numbered = sum(1 for line in meaningful if re.match(r"^\s*(?:\d{1,3}[.、)]|镜(?:头|号)?\s*\d{1,3}|[一二三四五六七八九十]+[、.])", line))
    return numbered >= 3 or len(meaningful) >= 10


def _line_based_target_count(units: list[str], shot_count_hint: int | None = None) -> int:
    if shot_count_hint:
        return max(1, min(int(shot_count_hint), len(units), 80))
    return max(1, min(len(units), 80))


def _balanced_chunks(units: list[str], target_count: int) -> list[str]:
    if target_count <= 0:
        return []
    if target_count >= len(units):
        return units
    chunks: list[str] = []
    for index in range(target_count):
        start = round(index * len(units) / target_count)
        end = round((index + 1) * len(units) / target_count)
        chunk = "".join(units[start:end]).strip()
        if chunk:
            chunks.append(chunk)
    return chunks


def _asset_refs(text: str) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for match in ASSET_RE.finditer(text):
        _push_ref(refs, match.group(1), _classify_asset(match.group(1), text), "explicit", text)
    for label in _infer_character_labels(text):
        if not any(ref["asset_type"] == "character" and ref["label"] == label for ref in refs):
            subtype = _character_subtype_for_label(label, text)
            _push_ref(
                refs,
                label,
                "character",
                "grounded_mention" if subtype == "animal" else "candidate",
                text,
                character_subtype=subtype,
            )
    if not any(ref["asset_type"] == "character" for ref in refs) and any(hint in text for hint in CHARACTER_HINTS):
        label = _infer_character_label(text) or "主角"
        if label not in GENERIC_CHARACTER_LABELS:
            _push_ref(refs, label, "character", "candidate", text, character_subtype=_character_subtype_for_label(label, text))
    if not any(ref["asset_type"] == "scene" for ref in refs) and any(hint in text for hint in SCENE_HINTS):
        label = _infer_scene_label(text)
        if label and label not in GENERIC_SCENE_LABELS:
            _push_ref(refs, label, "scene", "candidate", text)
    return refs


def _push_ref(
    refs: list[dict[str, Any]],
    label: str,
    asset_type: str,
    source: str,
    context: str = "",
    *,
    character_subtype: str = "",
) -> None:
    clean = _semantic_asset_label(label, asset_type, context)
    if not clean or any(ref["label"] == clean for ref in refs):
        return
    evidence = _asset_evidence_for_label(context, clean)
    ref = {
        "label": clean,
        "asset_id": f"candidate:{asset_type}:{_indexable_slug(clean)}",
        "asset_type": asset_type,
        "status": "mentioned" if source in {"explicit", "grounded_mention"} else "candidate",
        "source": source,
        "scope": "shot_tree",
        "confidence": _asset_confidence(asset_type, clean, context),
        "evidence_text": evidence,
    }
    subtype = character_subtype if character_subtype in {"human", "animal", "robot", "subject"} else ""
    if asset_type == "character" and subtype:
        ref["character_subtype"] = subtype
    refs.append(ref)


def _description_with_assets(source: str, refs: list[dict[str, Any]]) -> str:
    return _replace_generic_asset_tokens(source, refs)


def _replace_generic_asset_tokens(source: str, refs: list[dict[str, Any]]) -> str:
    text = str(source or "")
    character = next((ref for ref in refs if ref["asset_type"] == "character" and ref["label"] not in GENERIC_CHARACTER_LABELS), None)
    scene = next((ref for ref in refs if ref["asset_type"] == "scene" and ref["label"] not in GENERIC_SCENE_LABELS), None)
    if character:
        for label in GENERIC_CHARACTER_LABELS:
            text = text.replace(f"@{label}", f"@{character['label']}")
    if scene:
        for label in GENERIC_SCENE_LABELS:
            text = text.replace(f"@{label}", f"@{scene['label']}")
    return text


def _semantic_asset_label(label: str, asset_type: str, context: str) -> str:
    clean = re.sub(r"[，。；:：,.!?！？]+$", "", str(label or "")).strip()[:24]
    if asset_type == "character" and clean in GENERIC_CHARACTER_LABELS:
        return _infer_character_label(context) or clean
    if asset_type == "scene" and clean in GENERIC_SCENE_LABELS:
        return _infer_scene_label(context) or clean
    return clean


def _asset_confidence(asset_type: str, label: str, context: str) -> float:
    if f"@{label}" in context:
        return 0.92
    if label and label in context:
        return 0.82
    if asset_type == "scene" and any(hint in context for hint in SCENE_HINTS):
        return 0.68
    if asset_type == "character" and any(hint in context for hint in CHARACTER_HINTS):
        return 0.68
    return 0.6


def _asset_evidence_for_label(text: str, label: str) -> str:
    clean = str(text or "").strip()
    if not clean:
        return ""
    sentences = [part.strip() for part in re.split(r"(?<=[。！？!?；;])\s*", clean) if part.strip()]
    for sentence in sentences:
        if label and label in sentence:
            return sentence[:240]
    for sentence in sentences:
        if re.search(r"机器人|人物|角色|屋顶|天台|农村|乡村|城市|山巅|石台|战场|金箍棒|钢爪", sentence):
            return sentence[:240]
    return sentences[0][:240] if sentences else clean[:240]


def _infer_character_labels(text: str) -> list[str]:
    source = str(text or "")
    labels: list[str] = []
    for _position, label in _positioned_visible_character_labels(source):
        _append_label(labels, label)
    for left, right in re.findall(
        r"([\u4e00-\u9fffA-Za-z0-9·]{2,12})(?:大战|对决|迎娶|娶了|娶|嫁给|爱上|遇见|面对|追击|追杀|营救|守护)([\u4e00-\u9fffA-Za-z0-9·]{2,12})",
        source,
    ):
        for item in (left, right):
            _append_label(labels, _trim_character_name(item))
    for name, _index in sorted(
        ((name, source.find(name)) for name in KNOWN_CHARACTER_NAMES if source.find(name) >= 0),
        key=lambda item: item[1],
    ):
        _append_label(labels, name)
    if re.search(r"女生|女孩|少女", source):
        _append_label(labels, "女生" if "女生" in source else "女孩")
    for name in _repeated_actor_names(source):
        _append_label(labels, name)
    return labels[:6]


def _positioned_visible_character_labels(source: str) -> list[tuple[int, str]]:
    labels: list[tuple[int, str]] = []
    aliased_animal_spans: list[tuple[int, int]] = []
    animal_pattern = "|".join(re.escape(item) for item in ANIMAL_ENTITY_LABELS)
    for match in re.finditer(rf"({animal_pattern})[“\"]([\u4e00-\u9fffA-Za-z0-9·]{{1,8}})[”\"]", source):
        aliased_animal_spans.append((match.start(1), match.end(2)))
        labels.append((match.start(2), match.group(2)))
    for match in re.finditer(
        r"(?<![\u4e00-\u9fff])((?:小|阿)[\u4e00-\u9fff]{1,2}?)(?=蹲|站|踮|跃|抬|低|追|愣|伸|转|看|摸|攥|抱|走|跑|说|喊|把|在|的|，|。|；|：|、|——|$)",
        source,
    ):
        label = match.group(1)
        if not _is_animal_entity_label(label) and label not in {"小狗", "小猫", "小犬"}:
            labels.append((match.start(1), label))
    for role in HUMAN_ROLE_LABELS:
        index = source.find(role)
        if index >= 0:
            labels.append((index, role))
    for match in re.finditer(
        r"((?:黑色|白色|灰色|棕色|黄色|金色|灰白相间|黑白相间)?(?:拉布拉多|金毛|边牧|柯基|哈士奇|贵宾犬|萨摩耶|柴犬)(?:幼崽|幼犬)?)",
        source,
    ):
        labels.append((match.start(1), match.group(1)))
    for label in ANIMAL_ENTITY_LABELS:
        if len(label) <= 1:
            continue
        start = 0
        while True:
            index = source.find(label, start)
            if index < 0:
                break
            if not any(span_start <= index <= span_end for span_start, span_end in aliased_animal_spans):
                labels.append((index, label))
            start = index + len(label)
    seen: set[str] = set()
    result: list[tuple[int, str]] = []
    for position, label in sorted(labels, key=lambda item: item[0]):
        if label and label not in seen:
            seen.add(label)
            result.append((position, label))
    return result


def _character_subtype_for_label(label: str, context: str) -> str:
    clean = str(label or "").strip()
    source = str(context or "")
    if not clean:
        return ""
    if _is_animal_entity_label(clean):
        return "animal"
    if _looks_like_human_action_label(clean, source):
        return ""
    animal_pattern = "|".join(re.escape(item) for item in ANIMAL_ENTITY_LABELS)
    if re.search(rf"({animal_pattern})[“\"]{re.escape(clean)}[”\"]", source):
        return "animal"
    if re.search(rf"{re.escape(clean)}[^。！？!?]{{0,16}}(?:猫|狗|犬|肉垫|尾巴|爪|耳朵|鼻头|叼|弓背|炸毛|呼噜)", source):
        return "animal"
    if re.search(rf"(?:猫|狗|犬|肉垫|尾巴|爪|耳朵|鼻头|叼|弓背|炸毛|呼噜)[^。！？!?]{{0,16}}{re.escape(clean)}", source):
        return "animal"
    if "机器人" in clean or ("机器人" in source and clean in source):
        return "robot"
    return ""


def _looks_like_human_action_label(label: str, source: str) -> bool:
    return bool(
        re.search(
            rf"{re.escape(label)}[^\u3002\uff01\uff1f!?]{{0,18}}"
            r"(?:蹲|站|坐|跪|抬头|低头|回头|喉结|手指|指尖|目光|眼神|肩|校服|口袋|手机|试卷|说|喊|追|走|跑)",
            source,
        )
    )


def _is_animal_entity_label(label: str) -> bool:
    clean = str(label or "").strip()
    return clean in ANIMAL_CHARACTER_HINTS or any(len(item) > 1 and item in clean for item in ANIMAL_CHARACTER_HINTS)


def _infer_character_label(text: str) -> str:
    labels = _infer_character_labels(text)
    if labels:
        return labels[0]
    source = str(text or "")
    if re.search(r"未来.*机器人|机器人.*未来", source):
        return "未来机器人"
    if re.search(r"机器人|机械人|仿生人", source):
        return "机器人"
    if re.search(r"女生|女孩|少女", source):
        return "女生" if "女生" in source else "女孩"
    if re.search(r"男孩|少年", source):
        return "男孩"
    if "老人" in source:
        return "老人"
    if "孩子" in source:
        return "孩子"
    return ""


def _infer_prop_labels(text: str) -> list[str]:
    source = str(text or "")
    labels: list[str] = []
    for hint in PROP_HINTS:
        if hint in source:
            if hint in {"棒", "棍"} and "金箍棒" in source:
                continue
            _append_label(labels, hint)
    return labels[:6]


def _append_label(labels: list[str], value: str) -> None:
    clean = re.sub(r"^[以把将和与及、，。；：:\s]+|[的与和及、，。；：:\s]+$", "", str(value or "")).strip()
    if clean and clean not in labels:
        labels.append(clean[:24])


def _trim_character_name(value: str) -> str:
    clean = re.sub(r"^(以|把|将|当|用|和|与|及|、)+", "", str(value or "")).strip()
    clean = re.sub(r"^.*(?:是|讲述|关于|围绕)", "", clean).strip()
    clean = re.sub(r"(为核心|为主题|为主|展开|对决|战斗|格斗|碰撞).*$", "", clean).strip()
    clean = re.sub(r"(但是|但|却|旁观|观战|从旁).*$", "", clean).strip()
    return clean


def _infer_scene_label(text: str) -> str:
    source = str(text or "")
    is_night = bool(re.search(r"夜|星空|月光|霓虹|灯火", source))
    is_city = bool(re.search(r"城市|高楼|天际线|霓虹|楼宇", source))
    is_rooftop = bool(re.search(r"屋顶|楼顶|天台", source))
    if is_night and is_city and is_rooftop:
        return "夜晚城市屋顶"
    if is_city and is_rooftop:
        return "城市屋顶"
    if is_night and is_city:
        return "夜晚城市"
    if is_rooftop:
        return "屋顶平台"
    if is_city:
        return "城市场景"
    if re.search(r"云栈洞口|洞口|洞内|山洞", source):
        return "云栈洞口" if "云栈" in source else "山洞场景"
    if re.search(r"暗办公室|昏暗办公室", source):
        return "暗办公室"
    if "办公室" in source:
        return "办公室"
    if re.search(r"房间|室内", source):
        return "室内空间"
    if re.search(r"老城区巷口|巷口|窄巷|巷子|青石台阶|青砖", source):
        return "老城区巷口" if re.search(r"老城区|巷口", source) else "巷道空间"
    if re.search(r"街道|街区|路面", source):
        return "街道空间"
    if re.search(r"海边|海面|沙滩|灯塔", source):
        return "海边"
    if "餐厅" in source:
        return "餐厅"
    if "古战场" in source:
        return "古战场"
    if "战场" in source:
        return "战场"
    if _looks_like_mountain_battle_scene(source):
        return "山巅石台战场"
    return ""


def _looks_like_mountain_battle_scene(source: str) -> bool:
    if re.search(r"山巅|山脊|云海", source):
        return True
    return bool("石台" in source and re.search(r"山|峰|云|战|大战|对决|破碎", source))


def _repeated_actor_names(source: str) -> list[str]:
    candidates: dict[str, int] = {}
    pattern = re.compile(
        r"([\u4e00-\u9fff]{2,4})(?=(?:在|从|向|朝|把|将|对|低头|抬头|伸出|手持|推进|冲|走|跑|跃|后撤|咆哮|看|望|眼神|侧身|跪|坐|握|拿))"
    )
    for match in pattern.finditer(source):
        name = match.group(1)
        if name in {"镜头", "画面", "远处", "两人", "双方", "办公室", "山巅", "石台", "战场"}:
            continue
        candidates[name] = candidates.get(name, 0) + 1
    return [name for name, count in sorted(candidates.items(), key=lambda item: (-item[1], source.find(item[0]))) if count >= 2][:4]


def _duration(text: str) -> str:
    match = re.search(r"(\d{1,2})\s*(?:s|秒)", text, flags=re.I)
    if match:
        return f"{match.group(1)}s"
    if len(text) > 130:
        return "8s"
    if len(text) > 70:
        return "6s"
    return "5s"


def _shot_size(text: str) -> str:
    if re.search(r"大远景|远景|全貌|城市|山谷|天空", text):
        return "远景"
    if re.search(r"全景|全身|环境", text):
        return "全景"
    if re.search(r"近景|脸|眼神|表情", text):
        return "近景"
    if re.search(r"特写|手指|瞳孔|细节|屏幕|地图", text):
        return "特写"
    if re.search(r"半身|肩", text):
        return "半身景"
    return "中景"


def _lighting(text: str) -> str:
    if re.search(r"夜|霓虹|暗|阴影", text):
        return "低照度，冷色阴影压低环境"
    if re.search(r"晨|清晨|阳光|明亮", text):
        return "自然主光，明亮通透"
    if re.search(r"雨|雾|烟|尘", text):
        return "柔散光，空气颗粒增强层次"
    if re.search(r"紧张|压迫|冲突", text):
        return "高反差侧光，氛围紧张"
    return "自然光影，氛围服务情绪推进"


def _camera_motion(text: str) -> str:
    if re.search(r"推近|逼近|靠近", text):
        return "缓慢推近"
    if re.search(r"拉远|退后", text):
        return "缓慢拉远"
    if re.search(r"跟随|追|奔跑", text):
        return "跟拍移动"
    if re.search(r"摇|环绕", text):
        return "轻微摇移"
    if re.search(r"切|闪回|突然", text):
        return "快速切入"
    return "固定机位，轻微呼吸感"


def _dialogue(text: str) -> str:
    line = re.search(r"(?:对白|旁白|台词)\s*[:：]\s*(.+)$", text)
    if line:
        return line.group(1)[:80]
    for quote in re.finditer(r"[“\"](.*?)[”\"]", text):
        if _is_spoken_quote(text, quote):
            return quote.group(1)[:80]
    return "无明确对白"


def _is_spoken_quote(text: str, quote: re.Match[str]) -> bool:
    content = quote.group(1).strip()
    before = text[max(0, quote.start() - 14) : quote.start()]
    after = text[quote.end() : quote.end() + 10]
    if not content:
        return False
    if SPEECH_VERBS_RE.search(before) or SPEECH_VERBS_RE.match(after.strip()):
        return True
    if len(content) <= 4 and _looks_like_alias_quote(before):
        return False
    return bool(re.search(r"[，。！？,.!?]|吧|吗|呢|呀|啊|喂|救命|不要|快", content))


def _looks_like_alias_quote(before: str) -> bool:
    return bool(re.search(r"(?:橘猫|流浪猫|狸花猫|黑猫|白猫|小猫|猫咪|猫|奶狗|小狗|幼犬|柴犬幼崽|柴犬|狗狗|狗|犬)\s*$", before))


def _sound(text: str) -> str:
    if re.search(r"键盘|电脑|屏幕|地图", text):
        return "设备低频与电子提示音"
    if re.search(r"雨|海|风", text):
        return "环境自然声持续铺底"
    if re.search(r"冲突|奔跑|撞|爆", text):
        return "急促动作音与低频冲击"
    return "环境底噪，动作音随画面同步"


def _classify_asset(label: str, context: str) -> str:
    if any(hint in label or f"{label}里" in context or f"{label}中" in context for hint in SCENE_HINTS):
        return "scene"
    if label == "灯":
        return "prop" if re.search(r"@灯|路灯|台灯|灯具|灯柱|灯盏", context) else "scene"
    if label == "信":
        return "prop" if re.search(r"@信|信件|信封|信纸|一封信|书信", context) else "character"
    if any(hint in label for hint in PROP_HINTS):
        return "prop"
    return "character"


def _indexable_slug(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", value).lower()[:32] or "asset"


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


__all__ = (
    "grounding_status_for_unsupported",
    "local_storyboard_shots",
    "normalize_asset_ref",
    "storyboard_source_span",
    "structured_shot",
    "unsupported_additions_for_description",
)
