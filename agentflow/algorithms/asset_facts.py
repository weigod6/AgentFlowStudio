from __future__ import annotations

import re
from typing import Any


FACT_PROFILE_SCHEMA_VERSION = "0.1.0"

ANIMAL_TAXONOMY: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("猫", ("猫", "狸花猫", "橘猫", "黑猫", "白猫", "小猫", "猫咪", "kitten", "cat", "feline")),
    ("狗", ("狗", "犬", "小狗", "幼犬", "奶狗", "拉布拉多", "金毛", "边牧", "柯基", "哈士奇", "柴犬", "puppy", "dog", "canine")),
    ("兔", ("兔", "兔子", "rabbit", "bunny")),
    ("鸟", ("鸟", "雀", "鹰", "鸦", "鹤", "bird", "eagle", "crow")),
    ("马", ("马", "horse")),
    ("鹿", ("鹿", "deer")),
    ("狐", ("狐", "狐狸", "fox")),
    ("狼", ("狼", "wolf")),
    ("熊", ("熊", "熊猫", "bear", "panda")),
    ("虎", ("虎", "老虎", "tiger")),
    ("狮", ("狮", "狮子", "lion")),
    ("蛇", ("蛇", "snake")),
    ("龙", ("龙", "dragon")),
    ("鱼", ("鱼", "fish")),
)

ROBOT_TERMS = ("机器人", "机械", "机甲", "robot", "android", "mecha")
HUMAN_TERMS = (
    "人",
    "人物",
    "人类",
    "男孩",
    "女孩",
    "男人",
    "女人",
    "少女",
    "少年",
    "高中生",
    "学生",
    "老师",
    "speaker",
    "person",
    "human",
    "girl",
    "boy",
    "woman",
    "man",
)
HUMAN_CONTEXT_TERMS = (
    "高中生",
    "学生",
    "放学",
    "指尖",
    "手指",
    "手悬",
    "手停",
    "手里",
    "口袋",
    "手机",
    "指节",
    "肩颈",
    "蹲在",
    "追到",
    "门环",
)

COLOR_DESCRIPTORS = (
    "灰白相间",
    "黑白相间",
    "黄白相间",
    "棕白相间",
    "灰黑相间",
    "橘白相间",
    "银灰",
    "灰白",
    "黑色",
    "白色",
    "灰色",
    "棕色",
    "褐色",
    "橘色",
    "橙色",
    "黄色",
    "金色",
    "银色",
    "虎斑",
    "斑点",
    "条纹",
    "花纹",
    "black",
    "white",
    "gray",
    "grey",
    "brown",
    "orange",
    "tabby",
    "spotted",
    "striped",
)

SURFACE_STATE_TERMS = (
    "湿漉漉",
    "湿透",
    "潮湿",
    "泥泞",
    "沾着泥",
    "脏兮兮",
    "蓬松",
    "短毛",
    "长毛",
    "卷毛",
    "发抖",
    "瑟缩",
    "wet",
    "soaked",
    "muddy",
    "fluffy",
    "short-haired",
    "long-haired",
)

ANIMAL_ACTION_TERMS = (
    "打喷嚏",
    "瑟缩",
    "蜷卧",
    "蜷缩",
    "舔爪",
    "叼着",
    "叼回",
    "低吼",
    "弓背",
    "炸毛",
    "轻晃",
    "摇尾",
    "跳起",
    "奔跑",
    "嗅闻",
    "舔水",
    "呼吸",
    "趴着",
    "抬头",
    "回头",
    "sniff",
    "sneeze",
    "crouch",
    "curl",
    "carry",
    "growl",
    "jump",
    "run",
)

SCENE_STRUCTURE_TERMS = (
    "树根",
    "纸盒",
    "泥土",
    "屋檐",
    "水洼",
    "街道",
    "厨房",
    "橱柜",
    "地面",
    "天幕",
    "台阶",
    "石阶",
    "空间",
    "入口",
    "边缘",
    "background",
    "layout",
    "ground",
)

LIGHTING_TERMS = (
    "柔光",
    "暖调",
    "冷调",
    "主光",
    "高光",
    "反光",
    "阴影",
    "树影",
    "午后",
    "黄昏",
    "夜",
    "暴雨",
    "雨",
    "雾",
    "风",
    "light",
    "shadow",
    "storm",
    "rain",
    "mist",
)


def build_asset_fact_profile(
    *,
    asset_type: str,
    label: str,
    evidence_text: str = "",
    source_text: str = "",
) -> dict[str, Any]:
    normalized_type = _clean_token(asset_type) or "asset"
    normalized_label = _clean_label(label)
    contexts = _label_contexts(normalized_label, evidence_text, source_text)
    context_text = " ".join(contexts) or _clean_text(" ".join(part for part in (evidence_text, source_text) if part))[:600]
    subtype = infer_character_subtype(normalized_label, normalized_type, context_text)
    facts = _facts_for_asset(normalized_type, normalized_label, subtype, context_text)
    continuity_locks = continuity_locks_from_facts(normalized_type, normalized_label, subtype, facts)
    negative_locks = negative_locks_from_facts(normalized_type, normalized_label, subtype, facts)
    return {
        "artifact_type": "agentflow_asset_fact_profile",
        "schema_version": FACT_PROFILE_SCHEMA_VERSION,
        "asset_type": normalized_type,
        "label": normalized_label,
        "character_subtype": subtype,
        "facts": facts,
        "fact_evidence": contexts[:6],
        "continuity_locks": continuity_locks,
        "negative_locks": negative_locks,
        "missing_fact_fields": _missing_fact_fields(normalized_type, subtype, facts),
        "writes_long_term_memory": False,
        "writes_company_kb": False,
    }


def infer_character_subtype(label: str, asset_type: str, evidence_text: str = "") -> str:
    if str(asset_type or "") != "character":
        return ""
    text = f"{label} {evidence_text}".casefold()
    if _contains_any(text, ROBOT_TERMS):
        return "robot"
    if _animal_species(label, ""):
        return "animal"
    if _contains_any(text, HUMAN_TERMS) or _has_human_context(label, evidence_text):
        return "human"
    if _animal_species_bound_to_label(label, evidence_text):
        return "animal"
    return "subject"


def continuity_locks_from_facts(
    asset_type: str,
    label: str,
    character_subtype: str,
    facts: dict[str, Any],
) -> list[str]:
    locks: list[str] = []
    if asset_type == "character":
        if character_subtype == "animal":
            species = _text(facts.get("species"))
            color = _text(facts.get("color_pattern"))
            state = _text(facts.get("surface_state"))
            age = _text(facts.get("size_or_age"))
            marks = _strings(facts.get("distinctive_marks"), limit=4)
            locks.append(f"保持{label}动物主体身份")
            if species:
                locks.append(f"保持物种为{species}")
            if color:
                locks.append(f"保持{color}毛色/斑纹")
            if age:
                locks.append(f"保持{age}体型或年龄感")
            if state:
                locks.append(f"本镜头保持{state}毛发/表面状态")
            locks.extend(f"保持{mark}" for mark in marks)
            if not any("毛色" in lock or "斑纹" in lock for lock in locks):
                locks.append("保持已证据化的毛色、斑纹、耳朵、尾巴和体态比例")
            return _dedupe(locks)
        if character_subtype == "robot":
            locks.extend(["identity", "robot shell/material", "mechanical proportions"])
            return _dedupe(locks)
        locks.extend(["identity", "silhouette", "body proportions"])
        if _text(facts.get("hair")):
            locks.append(f"保持发型/发色：{facts['hair']}")
        if _text(facts.get("wardrobe")):
            locks.append(f"保持服装：{facts['wardrobe']}")
        return _dedupe(locks)
    if asset_type == "scene":
        location = _text(facts.get("location_type")) or label
        locks.extend([f"保持{location}场景身份", "保持空间结构和主体位置关系"])
        if structure := _text(facts.get("spatial_structure")):
            locks.append(f"保持空间结构：{structure}")
        if lighting := _text(facts.get("lighting_atmosphere")):
            locks.append(f"保持光影氛围：{lighting}")
        elements = _strings(facts.get("key_environment_elements"), limit=4)
        locks.extend(f"保留关键环境元素：{item}" for item in elements)
        return _dedupe(locks)
    if asset_type == "prop":
        locks.extend([f"保持{label}道具身份", "保持道具几何、材质和使用关系"])
        if appearance := _text(facts.get("appearance")):
            locks.append(f"保持外观：{appearance}")
        return _dedupe(locks)
    return [f"保持{label}已证据化视觉身份"]


def negative_locks_from_facts(
    asset_type: str,
    label: str,
    character_subtype: str,
    facts: dict[str, Any],
) -> list[str]:
    locks = ["不要添加文字、水印、UI 或边框"]
    if asset_type == "character":
        locks.extend([f"不要改变{label}身份", "不要新增未要求角色"])
        if character_subtype == "animal":
            species = _text(facts.get("species"))
            color = _text(facts.get("color_pattern"))
            age = _text(facts.get("size_or_age"))
            marks = _strings(facts.get("distinctive_marks"), limit=4)
            if species:
                locks.append(f"不要把{label}改成其他物种")
            if color:
                locks.append(f"不要改变{color}毛色/斑纹")
            if age:
                locks.append("不要改成成年体型，除非分镜明确要求")
            locks.extend(f"不要改变{mark}" for mark in marks)
            locks.append("不要新增项圈、衣物或拟人化装饰，除非分镜明确要求")
        elif character_subtype == "robot":
            locks.append("不要把机器人改成人类或动物主体")
        return _dedupe(locks)
    if asset_type == "scene":
        locks.extend(["不要移动到其他地点", "不要新增无关家具、飞檐、椅凳或场景道具，除非分镜明确要求"])
        return _dedupe(locks)
    if asset_type == "prop":
        locks.extend(["不要改变道具功能", "不要复制道具，除非分镜明确要求"])
        return _dedupe(locks)
    return _dedupe(locks)


def render_asset_prompt_line(
    asset: dict[str, Any],
    *,
    negative_locks: list[str] | None = None,
    max_facts: int = 8,
) -> str:
    label = _clean_label(asset.get("label") or asset.get("display_name") or asset.get("asset_id") or "asset")
    asset_type = _clean_token(asset.get("asset_type") or "asset")
    subtype = _clean_token(asset.get("character_subtype") or "")
    facts = asset.get("facts") if isinstance(asset.get("facts"), dict) else {}
    if not facts and isinstance(asset.get("asset_fact_profile"), dict):
        profile = asset["asset_fact_profile"]
        facts = profile.get("facts") if isinstance(profile.get("facts"), dict) else {}
        subtype = subtype or _clean_token(profile.get("character_subtype") or "")
    fact_text = _fact_summary(facts, max_items=max_facts)
    locks = _strings(asset.get("continuity_locks"), limit=6)
    avoids = _strings(negative_locks if negative_locks is not None else asset.get("negative_locks"), limit=4)
    type_label = _type_label(asset_type, subtype)
    parts = [f"{label}（{type_label}）"]
    if fact_text:
        parts.append(f"证据事实：{fact_text}")
    elif signature := _text(asset.get("signature") or asset.get("descriptive_signature")):
        parts.append(f"证据摘要：{signature}")
    if locks:
        parts.append(f"连续性：{'；'.join(locks)}")
    if avoids:
        parts.append(f"避免：{'；'.join(avoids)}")
    return "；".join(part for part in parts if part)


def animal_assets_only(assets: list[dict[str, Any]]) -> bool:
    character_assets = [asset for asset in assets if str(asset.get("asset_type") or "") == "character"]
    return bool(character_assets) and all(str(asset.get("character_subtype") or "") == "animal" for asset in character_assets)


def has_animal_asset(assets: list[dict[str, Any]]) -> bool:
    return any(str(asset.get("character_subtype") or "") == "animal" for asset in assets)


def has_human_asset(assets: list[dict[str, Any]]) -> bool:
    return any(str(asset.get("character_subtype") or "") == "human" for asset in assets)


def _facts_for_asset(asset_type: str, label: str, subtype: str, context_text: str) -> dict[str, Any]:
    if asset_type == "character":
        if subtype == "animal":
            return _animal_facts(label, context_text)
        if subtype == "human":
            return _human_facts(label, context_text)
        if subtype == "robot":
            return _robot_facts(label, context_text)
        return {"identity": label, "source_context": _trim_evidence(context_text)}
    if asset_type == "scene":
        return _scene_facts(label, context_text)
    if asset_type == "prop":
        return _prop_facts(label, context_text)
    return {"identity": label, "source_context": _trim_evidence(context_text)}


def _animal_facts(label: str, context_text: str) -> dict[str, Any]:
    facts: dict[str, Any] = {"identity": label}
    if species := _animal_species(label, context_text):
        facts["species"] = species
    if color := _color_pattern(context_text, label=label):
        facts["color_pattern"] = color
    if state := _surface_state(context_text, label=label):
        facts["surface_state"] = state
    if size := _size_or_age(label, context_text):
        facts["size_or_age"] = size
    marks = _distinctive_marks(context_text, label=label)
    if marks:
        facts["distinctive_marks"] = marks
    actions = _actions(context_text, label=label)
    if actions:
        facts["current_action"] = actions
    relationships = _relationships(context_text, label)
    if relationships:
        facts["relationship"] = relationships
    return facts


def _human_facts(label: str, context_text: str) -> dict[str, Any]:
    facts: dict[str, Any] = {"identity": label}
    if hair := _phrase_with_terms(context_text, ("头发", "发型", "发色", "湿发", "short hair", "hair"), fallback_terms=False):
        facts["hair"] = hair
    if wardrobe := _phrase_with_terms(context_text, ("衣", "裙", "校服", "外套", "coat", "wardrobe", "uniform"), fallback_terms=False):
        facts["wardrobe"] = wardrobe
    if appearance := _trim_evidence(context_text):
        facts["appearance_context"] = appearance
    return facts


def _robot_facts(label: str, context_text: str) -> dict[str, Any]:
    facts = {"identity": label}
    if body := _phrase_with_terms(context_text, ("机械", "机甲", "外壳", "关节", "shell", "mechanical", "robot"), fallback_terms=True):
        facts["body_material_or_shell"] = body
    return facts


def _scene_facts(label: str, context_text: str) -> dict[str, Any]:
    facts: dict[str, Any] = {"location_type": label}
    if structure := _phrase_with_terms(context_text, SCENE_STRUCTURE_TERMS, fallback_terms=True):
        facts["spatial_structure"] = structure
    if lighting := (_explicit_lighting_phrase(context_text) or _phrase_with_terms(context_text, LIGHTING_TERMS, fallback_terms=True)):
        facts["lighting_atmosphere"] = lighting
    elements = _scene_elements(context_text, label)
    if elements:
        facts["key_environment_elements"] = elements
    return facts


def _prop_facts(label: str, context_text: str) -> dict[str, Any]:
    facts = {"identity": label}
    if appearance := _trim_evidence(context_text):
        facts["appearance"] = appearance
    return facts


def _label_contexts(label: str, *sources: str) -> list[str]:
    combined = _clean_text(" ".join(str(source or "") for source in sources if str(source or "").strip()))
    if not combined:
        return []
    candidates: list[str] = []
    for sentence in re.split(r"(?<=[。！？.!?])\s*|\n+", combined):
        sentence = sentence.strip()
        if not sentence:
            continue
        clauses = [part.strip() for part in re.split(r"[；;]", sentence) if part.strip()]
        candidates.extend(clauses or [sentence])
    selected: list[str] = []
    for clause in candidates:
        if label and label in clause:
            selected.append(clause[:260])
    if label:
        for match in re.finditer(re.escape(label), combined):
            start = max(0, match.start() - 90)
            end = min(len(combined), match.end() + 120)
            selected.append(combined[start:end].strip()[:260])
    if not selected:
        selected = candidates[:3] or [combined[:260]]
    return _dedupe(selected)[:8]


def _animal_species(label: str, evidence_text: str = "") -> str:
    label_text = str(label or "").casefold()
    for species, terms in ANIMAL_TAXONOMY:
        if _contains_any(label_text, terms):
            return species
    text = f"{label} {evidence_text}".casefold()
    for species, terms in ANIMAL_TAXONOMY:
        if _contains_any(text, terms):
            return species
    return ""


def _animal_species_bound_to_label(label: str, evidence_text: str = "") -> str:
    if not label:
        return ""
    contexts = _label_contexts(label, evidence_text)
    for context in contexts:
        if _has_human_context(label, context):
            continue
        for species, terms in ANIMAL_TAXONOMY:
            if _animal_terms_bound_to_label(context, label, terms):
                return species
    return ""


def _animal_terms_bound_to_label(context: str, label: str, terms: tuple[str, ...]) -> bool:
    term_pattern = "|".join(re.escape(term) for term in sorted(terms, key=len, reverse=True))
    label_pattern = re.escape(label)
    return bool(
        re.search(rf"(?:{term_pattern})[^，。；,;\n]{{0,24}}(?:名叫|叫|名字|称作|取名为|{label_pattern}|[“\"']{label_pattern}[”\"'])", context, flags=re.I)
        or re.search(rf"(?:{label_pattern}|[“\"']{label_pattern}[”\"'])[^，。；,;\n]{{0,24}}(?:是一只|这只|那只|幼崽|幼犬|小猫|小狗|{term_pattern})", context, flags=re.I)
    )


def _has_human_context(label: str, evidence_text: str = "") -> bool:
    text = " ".join(_label_contexts(label, evidence_text)) or str(evidence_text or "")
    return _contains_any(text, HUMAN_CONTEXT_TERMS)


def _color_pattern(text: str, *, label: str) -> str:
    if label_color := _color_from_label(label):
        return label_color
    if match := re.search(r"(?:毛色|毛发|皮毛|羽毛|鳞片|主体毛色)[为是呈现]*([^\s，。；,;]{1,12})", text):
        candidate = _clean_fact_phrase(match.group(1))
        if _span_belongs_to_label(text, label, match.start(), match.end()) and any(term in candidate.casefold() for term in COLOR_DESCRIPTORS):
            return candidate[:16]
    for descriptor in COLOR_DESCRIPTORS:
        lowered = text.casefold()
        start = lowered.find(descriptor.casefold())
        if start >= 0 and _span_belongs_to_label(text, label, start, start + len(descriptor)):
            return descriptor
    return ""


def _surface_state(text: str, *, label: str) -> str:
    lowered = text.casefold()
    for term in SURFACE_STATE_TERMS:
        start = lowered.find(term.casefold())
        if start >= 0 and _span_belongs_to_label(text, label, start, start + len(term), radius=10):
            return term
    return ""


def _size_or_age(label: str, text: str) -> str:
    label_text = str(label or "").casefold()
    if any(term in label_text for term in ("小狗", "小猫", "幼犬", "幼猫", "puppy", "kitten")):
        return "幼小"
    lowered = text.casefold()
    for term, value in (
        ("幼小", "幼小"),
        ("幼犬", "幼小"),
        ("幼猫", "幼小"),
        ("puppy", "幼小"),
        ("kitten", "幼小"),
        ("成年", "成年"),
        ("adult", "成年"),
        ("瘦小", "小型"),
        ("小型", "小型"),
        ("tiny", "小型"),
        ("small", "小型"),
    ):
        start = lowered.find(term.casefold())
        if start >= 0 and _span_belongs_to_label(text, label, start, start + len(term), radius=12):
            return value
    return ""


def _distinctive_marks(text: str, *, label: str) -> list[str]:
    marks: list[str] = []
    for match in re.finditer(r"((?:左|右|双|单)?(?:耳|眼|爪|腿|尾巴|额头|鼻|脸|背)[^，。；,;]{0,18}(?:缺|断|疤|斑|纹|短|长|卷)[^，。；,;]{0,10})", text):
        if not _span_belongs_to_label(text, label, match.start(), match.end(), radius=10):
            continue
        mark = _clean_fact_phrase(match.group(1).replace(label, ""))
        if mark:
            marks.append(mark)
    return _dedupe(marks)[:4]


def _actions(text: str, *, label: str) -> list[str]:
    lowered = text.casefold()
    actions: list[str] = []
    for term in ANIMAL_ACTION_TERMS:
        for match in re.finditer(re.escape(term.casefold()), lowered):
            if _action_belongs_to_label(text, label, match.start(), match.end()):
                actions.append(term)
                break
    return _dedupe(actions)[:6]


def _relationships(text: str, label: str) -> list[str]:
    relationships: list[str] = []
    if label and re.search(r"(猫|橘猫|狸花猫|狗|犬|角色|人物)[^，。；,;]{0,12}(叼着|叼回)[^，。；,;]{0,24}" + re.escape(label), text):
        actor = re.search(r"(猫|橘猫|狸花猫|狗|犬|角色|人物)[^，。；,;]{0,12}(?:叼着|叼回)[^，。；,;]{0,24}" + re.escape(label), text)
        relationships.append(f"被{actor.group(1)}叼着或叼回" if actor else "被其他主体叼着或叼回")
    if label and "纸盒" in text:
        relationships.append("位于纸盒附近或纸盒内")
    if "保护" in text or "护住" in text or "主权" in text:
        relationships.append("处于保护/占有关系中")
    return _dedupe(relationships)[:4]


def _scene_elements(text: str, label: str) -> list[str]:
    elements: list[str] = []
    for term in (*SCENE_STRUCTURE_TERMS, *LIGHTING_TERMS):
        if term.casefold() in text.casefold():
            elements.append(term)
    if label:
        elements.insert(0, label)
    return _dedupe(elements)[:6]


def _explicit_lighting_phrase(text: str) -> str:
    match = re.search(r"(?:光影氛围|光线|灯光|lighting|light)\s*[:：]\s*([^。；;\n]{1,120})", text, flags=re.I)
    if not match:
        return ""
    value = re.split(r"\s*(?:运镜|对白|旁白|音效|资产|镜头)\s*[:：]", match.group(1), maxsplit=1)[0]
    return _clean_fact_phrase(value)[:100]


def _color_from_label(label: str) -> str:
    text = str(label or "")
    color_names = {
        "黑": "黑色",
        "白": "白色",
        "灰": "灰色",
        "棕": "棕色",
        "褐": "褐色",
        "橘": "橘色",
        "黄": "黄色",
        "金": "金色",
        "银": "银色",
    }
    if _animal_species(label, ""):
        for char, color in color_names.items():
            if char in text:
                return color
    return ""


def _span_belongs_to_label(text: str, label: str, start: int, end: int, *, radius: int = 18) -> bool:
    if not label:
        return False
    local = text[max(0, start - radius): min(len(text), end + radius)]
    if label in local:
        return True
    return False


def _action_belongs_to_label(text: str, label: str, start: int, end: int) -> bool:
    if not label:
        return False
    segment_start = max(text.rfind(mark, 0, start) for mark in ("。", "！", "？", ".", "!", "?", "；", ";", "\n", " "))
    before = text[segment_start + 1:start]
    mentions = list(re.finditer(r"[\u4e00-\u9fff]{0,4}(?:猫|狗|犬|兔|鸟|马|鹿|狐|狼|熊|虎|狮|蛇|龙|鱼)|\b(?:cat|dog|puppy|kitten|animal|pet)\b", before, flags=re.I))
    if mentions:
        nearest = mentions[-1].group(0)
        return label in nearest or nearest in label
    return label in before[-48:]


def _phrase_with_terms(text: str, terms: tuple[str, ...], *, fallback_terms: bool) -> str:
    clauses = [part.strip() for part in re.split(r"[，。；,;.!?\n]", text) if part.strip()]
    lowered_terms = tuple(term.casefold() for term in terms)
    for clause in clauses:
        lowered = clause.casefold()
        if any(term in lowered for term in lowered_terms):
            return _clean_fact_phrase(clause)[:80]
    return _trim_evidence(text, max_len=80) if fallback_terms else ""


def _fact_summary(facts: dict[str, Any], *, max_items: int) -> str:
    labels = {
        "identity": "身份",
        "species": "物种",
        "color_pattern": "毛色/纹理",
        "surface_state": "状态",
        "size_or_age": "体型/年龄",
        "distinctive_marks": "辨识点",
        "current_action": "当前动作",
        "relationship": "关系",
        "location_type": "地点",
        "spatial_structure": "空间结构",
        "lighting_atmosphere": "光影",
        "key_environment_elements": "环境元素",
        "appearance": "外观",
        "hair": "发型/发色",
        "wardrobe": "服装",
    }
    parts: list[str] = []
    for key, value in facts.items():
        if key == "source_context":
            continue
        rendered = "、".join(_strings(value, limit=5)) if isinstance(value, list) else _text(value)
        if rendered:
            parts.append(f"{labels.get(key, key)}={rendered}")
        if len(parts) >= max_items:
            break
    return "；".join(parts)


def _missing_fact_fields(asset_type: str, subtype: str, facts: dict[str, Any]) -> list[str]:
    if asset_type == "character" and subtype == "animal":
        required = ("species", "color_pattern", "distinctive_marks", "size_or_age")
    elif asset_type == "character" and subtype == "human":
        required = ("hair", "wardrobe")
    elif asset_type == "scene":
        required = ("spatial_structure", "lighting_atmosphere")
    else:
        required = ()
    return [field for field in required if not facts.get(field)]


def _type_label(asset_type: str, subtype: str) -> str:
    if asset_type == "character" and subtype == "animal":
        return "动物角色"
    if asset_type == "character" and subtype == "human":
        return "人物角色"
    if asset_type == "character" and subtype == "robot":
        return "机器人角色"
    if asset_type == "character":
        return "角色主体"
    if asset_type == "scene":
        return "场景"
    if asset_type == "prop":
        return "道具"
    return asset_type or "资产"


def _clean_fact_phrase(value: str) -> str:
    text = _clean_text(value)
    text = re.sub(r"^(一只|一个|一种|正|正在|被|的)+", "", text)
    text = re.sub(r"(。|；|，|,|;)$", "", text)
    return text.strip()


def _trim_evidence(text: str, *, max_len: int = 120) -> str:
    return _clean_text(text)[:max_len]


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    lowered = str(text or "").casefold()
    return any(term.casefold() in lowered for term in terms)


def _text(value: Any) -> str:
    return _clean_text(value) if not isinstance(value, list) else "、".join(_strings(value, limit=8))


def _strings(value: Any, *, limit: int) -> list[str]:
    result: list[str] = []
    if not isinstance(value, list):
        return [_clean_text(value)] if _clean_text(value) else []
    for item in value:
        text = _clean_text(item)
        if text and text not in result:
            result.append(text[:160])
        if len(result) >= limit:
            break
    return result


def _clean_label(value: Any) -> str:
    return re.sub(r"^[\s@]+|[\s，。；:：.!?！？]+$", "", str(value or "")).strip()[:80]


def _clean_token(value: Any) -> str:
    return re.sub(r"\s+", "_", str(value or "").strip().lower())[:80]


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = _clean_text(value)
        if text and text not in result:
            result.append(text)
    return result


__all__ = (
    "FACT_PROFILE_SCHEMA_VERSION",
    "animal_assets_only",
    "build_asset_fact_profile",
    "continuity_locks_from_facts",
    "has_animal_asset",
    "has_human_asset",
    "infer_character_subtype",
    "negative_locks_from_facts",
    "render_asset_prompt_line",
)
