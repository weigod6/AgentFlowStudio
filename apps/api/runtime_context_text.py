from __future__ import annotations

import re
from typing import Any

from agentflow.algorithms.asset_facts import render_asset_prompt_line
from apps.api.runtime_director_compiler import compile_director_setup
from apps.api.runtime_models import DirectorSetup2D, TemporaryLockOverride


def provider_prompt_from_bundle(bundle: dict[str, Any]) -> str:
    # Identity/locks lead the prompt: if the provider hard limit ever tail-cuts
    # the joined text, the loss order matches the priority order.
    text = bundle.get("text_channel") if isinstance(bundle.get("text_channel"), dict) else {}
    visible_prompt = str(text.get("visible_prompt") or "").strip()
    if _is_reference_localized_edit(bundle, visible_prompt):
        parts = [
            (
                "Requested change / preserve policy: "
                f"{visible_prompt}\n"
                "Reference/base descriptors are anchors, not instructions to undo the requested change."
            ),
            str(text.get("asset_identity_segment") or "").strip(),
            str(text.get("scene_director_segment") or "").strip(),
            str(text.get("upstream_summary_segment") or "").strip(),
            str(text.get("preference_segment") or "").strip(),
        ]
        return "\n".join(part for part in parts if part)
    parts = [
        str(text.get("asset_identity_segment") or "").strip(),
        visible_prompt,
        str(text.get("scene_director_segment") or "").strip(),
        str(text.get("upstream_summary_segment") or "").strip(),
        str(text.get("preference_segment") or "").strip(),
    ]
    return "\n".join(part for part in parts if part)


def text_channel(
    mode: str,
    visible_prompt: str,
    assets: list[tuple[dict[str, Any], str]],
    overrides: set[tuple[str, str]],
    *,
    upstream_lines: list[str] | None = None,
    style_preference: str | None = None,
    director_compile: dict[str, Any] | None = None,
) -> dict[str, str]:
    if mode == "optimize":
        signatures = [
            _sanitize_asset_line_for_visible_prompt(f"{asset['label']}: {asset.get('signature')}", visible_prompt)
            for asset, _detail in assets
        ]
        return {
            "visible_prompt": visible_prompt,
            "asset_signature_segment": "\n".join(signatures),
            "asset_identity_segment": "",
            "scene_director_segment": "",
            "upstream_summary_segment": "",
            "preference_segment": "",
        }
    identity_lines: list[str] = []
    scene_lines: list[str] = []
    for asset, detail_level in assets:
        if detail_level != "full_card":
            line = f"{asset.get('label')}: {asset.get('signature')}".strip()
            line = _sanitize_asset_line_for_visible_prompt(line, visible_prompt)
            if asset.get("asset_type") == "scene":
                scene_lines.append(line)
            else:
                identity_lines.append(line)
            continue
        card = asset.get("feature_card") if isinstance(asset.get("feature_card"), dict) else {}
        locks = [
            lock
            for lock in asset.get("negative_locks", [])
            if (str(asset.get("asset_id")), str(lock)) not in overrides
        ]
        render_asset = {**asset, "negative_locks": locks}
        line = render_asset_prompt_line(render_asset, negative_locks=locks)
        if "证据事实" not in line:
            card_text = _provider_safe_feature_card_text(card)
            fallback = f"{asset.get('label')}: {asset.get('signature')}".strip()
            line = f"{fallback}. {card_text}. Locks: {'; '.join(locks)}".strip()
        line = _sanitize_asset_line_for_visible_prompt(line, visible_prompt)
        if asset.get("asset_type") == "scene":
            scene_lines.append(line)
        else:
            identity_lines.append(line)
    preference = str(style_preference or "").strip()
    director_lines = _director_lines(director_compile)
    return {
        "visible_prompt": visible_prompt,
        "asset_signature_segment": "",
        "asset_identity_segment": "\n".join(identity_lines),
        "scene_director_segment": "\n".join([*director_lines, *scene_lines]),
        "upstream_summary_segment": "\n".join(upstream_lines or []),
        "preference_segment": f"style preference: {preference}" if preference else "",
    }


def director_compile_result(director_setup: DirectorSetup2D | None, assets: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    if director_setup is None:
        return None
    signatures = {
        str(asset.get("asset_id")): str(asset.get("signature") or "")
        for asset in assets.values()
        if str(asset.get("signature") or "")
    }
    return compile_director_setup(director_setup, visual_asset_signatures=signatures)


def override_pairs(overrides: list[TemporaryLockOverride]) -> set[tuple[str, str]]:
    return {(item.asset_id, item.lock_text) for item in overrides}


def _director_lines(director_compile: dict[str, Any] | None) -> list[str]:
    if not director_compile:
        return []
    lines = []
    for section in director_compile.get("sections", []):
        if not isinstance(section, dict):
            continue
        title = str(section.get("title") or "").strip()
        text = str(section.get("text") or "").strip()
        if title and text:
            lines.append(f"{title}: {text}")
    return lines


def _is_reference_localized_edit(bundle: dict[str, Any], visible_prompt: str) -> bool:
    if not bundle.get("reference_image_channel") or not visible_prompt:
        return False
    prompt = visible_prompt.casefold()
    edit_terms = (
        "add ",
        "adjust ",
        "change ",
        "edit ",
        "replace ",
        "only ",
        "subtle ",
        "局部",
        "调整",
        "改",
        "增加",
        "只",
    )
    preserve_terms = ("preserve", "keep", "保持", "不变")
    return any(term in prompt for term in edit_terms) and any(term in prompt for term in preserve_terms)


def _provider_safe_feature_card_text(card: dict[str, Any]) -> str:
    parts: list[str] = []
    placeholder_terms = ("待确认", "后续可人工补充", "根据分镜", "pending human confirmation", "pending confirmation")
    for key, value in card.items():
        text = str(value or "").strip()
        if not text:
            continue
        if any(term in text for term in placeholder_terms):
            continue
        parts.append(f"{key}: {text}")
    return "; ".join(parts)


def _sanitize_asset_line_for_visible_prompt(line: str, visible_prompt: str) -> str:
    if not _prompt_is_animal_subject(visible_prompt):
        return line
    text = str(line or "")
    replacements = {
        "reference character": "reference animal subject",
        "pending human confirmation": "pending confirmation",
        "keep character identity": "keep animal identity",
        "keep signature wardrobe": "do not add human wardrobe",
        "参考图角色": "参考图动物主体",
        "参考图人物": "参考图动物主体",
        "角色身份": "动物主体身份",
        "人物身份": "动物主体身份",
        "角色主体": "动物主体",
        "人物角色": "动物主体",
        "角色资产": "主体资产",
        "人物资产": "主体资产",
        "标志性服装": "不要添加人类服装",
        "服装": "不要添加人类服装",
        "发型、发色": "毛色和毛发纹理",
        "发型发色": "毛色和毛发纹理",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    text = re.sub(r";\s*wardrobe:\s*[^.;\n]+", "; no_human_clothing: do not add human clothing", text)
    text = re.sub(r";\s*hair:\s*[^.;\n]+", "; fur: preserve animal fur color and markings", text)
    return text


def _prompt_is_animal_subject(value: str) -> bool:
    text = str(value or "").casefold()
    text = text.replace("角色/主体", "").replace("人物/主体", "").replace("角色：", "").replace("角色:", "").replace("人物：", "").replace("人物:", "")
    if not text:
        return False
    animal_terms = ("猫", "狸花猫", "黑猫", "白猫", "橘猫", "宠物", "动物", "狗", "犬", "cat", "tabby", "kitten", "feline", "dog", "puppy", "animal", "pet")
    human_terms = ("人像", "真人", "人类", "女孩", "男孩", "女人", "男人", "女性", "男性", "头发", "发型", "校服", "服装", "person", "human", "girl", "boy", "woman", "man", "hair", "wardrobe", "uniform")
    return any(term.casefold() in text for term in animal_terms) and not any(term.casefold() in text for term in human_terms)


__all__ = (
    "director_compile_result",
    "override_pairs",
    "provider_prompt_from_bundle",
    "text_channel",
)
