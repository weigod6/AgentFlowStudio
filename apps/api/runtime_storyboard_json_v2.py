from __future__ import annotations

import json
from typing import Any


class StoryboardJsonError(ValueError):
    pass


def json_object_from_provider_text(text: str) -> dict[str, Any]:
    source = str(text or "").strip()
    if source.startswith("```"):
        lines = source.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        source = "\n".join(lines).strip()
    try:
        payload = json.loads(source)
    except json.JSONDecodeError:
        payload = _first_json_object(source)
    if not isinstance(payload, dict):
        raise StoryboardJsonError("provider response root must be an object")
    return payload


def _first_json_object(text: str) -> Any:
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            payload, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise StoryboardJsonError("provider response is not valid JSON") from None


__all__ = ("StoryboardJsonError", "json_object_from_provider_text")
