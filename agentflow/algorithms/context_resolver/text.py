from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agentflow.algorithms.asset_facts import render_asset_prompt_line


def provider_prompt_from_bundle(bundle: dict[str, Any]) -> str:
    text = bundle.get("text_channel") if isinstance(bundle.get("text_channel"), dict) else {}
    parts = [
        str(text.get("asset_identity_segment") or "").strip(),
        str(text.get("visible_prompt") or "").strip(),
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
        signatures = [f"{asset['label']}: {asset.get('signature')}" for asset, _detail in assets]
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
        line = render_asset_prompt_line({**asset, "negative_locks": locks}, negative_locks=locks)
        if "证据事实" not in line:
            card_text = _provider_safe_feature_card_text(card)
            line = f"{asset.get('label')}: {asset.get('signature')}. {card_text}. Locks: {'; '.join(locks)}".strip()
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


def director_compile_result(
    director_setup: Any | None,
    assets: dict[str, dict[str, Any]],
    *,
    compile_director_setup: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    if director_setup is None or compile_director_setup is None:
        return None
    signatures = {
        str(asset.get("asset_id")): str(asset.get("signature") or "")
        for asset in assets.values()
        if str(asset.get("signature") or "")
    }
    return compile_director_setup(director_setup, visual_asset_signatures=signatures)


def override_pairs(overrides: list[Any]) -> set[tuple[str, str]]:
    return {(str(item.asset_id), str(item.lock_text)) for item in overrides}


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


__all__ = (
    "director_compile_result",
    "override_pairs",
    "provider_prompt_from_bundle",
    "text_channel",
)
