from __future__ import annotations

import re
from typing import Any


MOJIBAKE_FRAGMENTS = (
    "璧勴",
    "璧勪骇",
    "绛惧悕",
    "锛歕",
    "瑙掕壊",
    "鍦烘櫙",
    "寰呬汉宸",
    "閬撳叿",
)
SUSPICIOUS_MOJIBAKE_CHARS = frozenset("璧绛锛歕閬瑙鍦櫙寰呬宸")


def prompt_integrity_issues(value: Any) -> list[dict[str, str]]:
    text = str(value or "")
    issues: list[dict[str, str]] = []
    if "\ufffd" in text:
        issues.append({"code": "unicode_replacement_character", "evidence": "�"})
    for fragment in MOJIBAKE_FRAGMENTS:
        if fragment in text:
            issues.append({"code": "mojibake_fragment", "evidence": fragment})
    if re.search(r"[锛歕]\\?n", text):
        issues.append({"code": "corrupt_newline_escape", "evidence": "mojibake_newline"})
    suspicious_count = sum(1 for char in text if char in SUSPICIOUS_MOJIBAKE_CHARS)
    cjk_count = len(re.findall(r"[\u4e00-\u9fff]", text))
    if suspicious_count >= 6 and cjk_count and suspicious_count / max(cjk_count, 1) >= 0.08:
        issues.append({"code": "mojibake_density", "evidence": f"suspicious_cjk={suspicious_count}"})
    return _dedupe_issues(issues)


def validate_prompt_integrity(value: Any, *, field_name: str = "provider_prompt") -> str:
    text = str(value or "")
    issues = prompt_integrity_issues(text)
    if issues:
        codes = ", ".join(issue["code"] for issue in issues)
        raise ValueError(f"{field_name} failed integrity guard: {codes}")
    return text


def _dedupe_issues(issues: list[dict[str, str]]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for issue in issues:
        key = (str(issue.get("code") or ""), str(issue.get("evidence") or ""))
        if key not in seen:
            seen.add(key)
            result.append(issue)
    return result


__all__ = ("prompt_integrity_issues", "validate_prompt_integrity")
