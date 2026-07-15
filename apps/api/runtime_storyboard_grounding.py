from __future__ import annotations

import re
from typing import Any


UNREQUESTED_SET_PIECES = (
    "木椅",
    "椅子",
    "凳子",
    "屋檐",
    "飞檐",
    "篮子",
    "chair",
    "stool",
    "eaves",
)
UNSUPPORTED_COUNT_RE = re.compile(r"[二三四五六七八九十两\d]+人[一二三四五六七八九十两\d]+(?:猫|狗|犬|鸟|兽)")
ACTION_OBJECT_RE = re.compile(
    r"(?:晃动|摇动|拿起|拿着|握着|攥着|叼着|捡起|捡到|抱着|举起|推开|打开|触到|按住|抓住|拖着|背着|放下|递出|沾着)"
    r"([^，。；;、\n]{2,14})"
)


def storyboard_source_span(source: str, full_source: str, index: int) -> dict[str, Any]:
    clean_source = clean_text(source)
    clean_full = clean_text(full_source) or clean_source
    start = clean_full.find(clean_source) if clean_source else -1
    end = start + len(clean_source) if start >= 0 else -1
    grounded = bool(clean_source and (start >= 0 or clean_full == clean_source))
    return {
        "span_id": f"script_span_{index:02d}",
        "text": clean_source[:500],
        "start": start,
        "end": end,
        "grounding_status": "source_grounded" if grounded else "missing_source_text" if not clean_source else "source_span_not_found",
    }


def unsupported_additions_for_description(description: str, source_text: str) -> list[str]:
    desc = str(description or "")
    source = str(source_text or "")
    additions: list[str] = []
    for term in UNREQUESTED_SET_PIECES:
        if term in desc and term not in source:
            additions.append(term)
    for match in UNSUPPORTED_COUNT_RE.findall(desc):
        if match not in source:
            additions.append(match)
    for match in ACTION_OBJECT_RE.findall(desc):
        candidate = _clean_candidate_addition(match)
        if candidate and candidate not in source:
            additions.append(candidate)
    return _dedupe(additions)


def _clean_candidate_addition(value: str) -> str:
    clean = re.sub(r"^(?:一|一个|一只|一条|一位|一名|那只|这只|旧|破旧|湿漉漉的|细微的)+", "", clean_text(value))
    clean = re.sub(r"(?:的)?(?:节奏|动作|姿态|方向|边缘|末端)$", "", clean).strip()
    if len(clean) < 2:
        return ""
    if any(term in clean for term in ("镜头", "画面", "光影", "阳光", "呼吸", "影子")):
        return ""
    return clean[:24]


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def grounding_status_for_unsupported(unsupported: list[str]) -> str:
    return "needs_review_unsupported_addition" if unsupported else "source_grounded"


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


__all__ = (
    "clean_text",
    "grounding_status_for_unsupported",
    "storyboard_source_span",
    "unsupported_additions_for_description",
)
