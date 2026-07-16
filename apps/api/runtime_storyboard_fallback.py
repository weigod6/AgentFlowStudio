from __future__ import annotations


def storyboard_fallback_message(fallback_reason: str | None, discard_reason: str | None) -> str:
    if fallback_reason == "llm_gate_blocked":
        return "LLM gate 未开启，已使用本地保守分镜；结果需要人工复核后再继续资产识别。"
    if fallback_reason == "provider_call_failed":
        return "LLM provider 调用失败，已使用本地保守分镜；请检查服务配置或稍后重试。"
    if fallback_reason == "provider_output_discarded":
        detail = f"原因：{discard_reason}" if discard_reason else "原因：provider 输出未通过结构化校验"
        return f"LLM 输出未被采用，已回退到本地保守分镜；{detail}。"
    if fallback_reason == "provider_output_unavailable":
        return "LLM 未返回可用分镜，已使用本地保守分镜；请检查 provider 状态。"
    return ""


__all__ = ("storyboard_fallback_message",)
