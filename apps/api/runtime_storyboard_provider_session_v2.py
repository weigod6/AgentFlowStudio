from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from agentflow_studio.model_gateway.errors import ModelGatewayError
from agentflow_studio.model_gateway.provider_adapter import ProviderDispatchRequest
from apps.api.runtime_errors import RuntimeApiError
from apps.api.runtime_llm_enhancement_dispatch import dispatch_llm_with_fallback
from apps.api.runtime_models import PromptOptimizationRequest

PIPELINE_ID = "provider_verified_v2"


class ProviderCallSession:
    def __init__(
        self,
        registry: Any,
        llm_request: PromptOptimizationRequest,
        output_dir: Path,
        *,
        max_calls: int,
    ) -> None:
        self.registry = registry
        self.llm_request = llm_request
        self.output_dir = output_dir
        self.max_calls = max_calls
        self.call_count = 0
        self.cache_hits = 0
        self._cache: dict[str, str] = {}

    def call(self, prompt: str, *, stage: str, timeout_sec: float) -> str:
        cache_key = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        if cache_key in self._cache:
            self.cache_hits += 1
            return self._cache[cache_key]
        if self.call_count >= self.max_calls:
            raise pipeline_error(
                "provider_output_invalid",
                stage="call_budget",
                message="分镜验证所需调用超过本次安全预算，请减少镜头数量后重试。",
                details={"max_calls": self.max_calls},
            )
        self.call_count += 1
        request = ProviderDispatchRequest(
            prompt=prompt,
            output_dir=self.output_dir / stage,
            task_type=f"storyboard_{stage}",
            timeout_sec=timeout_sec,
        )
        try:
            result = dispatch_llm_with_fallback(self.registry, self.llm_request, request)
        except ModelGatewayError as exc:
            raise pipeline_error(
                "provider_unavailable",
                stage=stage,
                message="LLM 服务暂时不可用，未生成本地替代分镜。请检查服务后重试。",
                status_code=503,
                retryable=True,
                details={"attempted_calls": self.call_count},
            ) from exc
        text = str(result.get("text") or "").strip() if isinstance(result, dict) else ""
        if not text:
            raise pipeline_error(
                "provider_output_invalid",
                stage=stage,
                message="LLM 返回了空结果，未生成本地替代分镜。",
                details={"attempted_calls": self.call_count},
            )
        self._cache[cache_key] = text
        return text

    def summary(self) -> dict[str, Any]:
        return {
            "call_count": self.call_count,
            "cache_hits": self.cache_hits,
            "max_calls": self.max_calls,
            "max_concurrency": 1,
            "raw_provider_response_stored": False,
        }


def contract_pipeline_error(state: str, stage: str, exc: ValueError) -> RuntimeApiError:
    return pipeline_error(
        state,
        stage=stage,
        message="LLM 返回结果未通过结构与证据合同，未生成本地替代分镜。",
        details={
            "reason": str(getattr(exc, "reason", str(exc))),
            **dict(getattr(exc, "details", {}) or {}),
        },
    )


def pipeline_error(
    state: str,
    *,
    stage: str,
    message: str,
    status_code: int = 422,
    user_action: str = "修正输入或服务状态后重新拆分分镜。",
    retryable: bool = False,
    details: dict[str, Any] | None = None,
) -> RuntimeApiError:
    return RuntimeApiError(
        state,
        message,
        stage=stage,
        status_code=status_code,
        user_action=user_action,
        retryable=retryable,
        details={"pipeline": PIPELINE_ID, "pipeline_state": state, **(details or {})},
    )


__all__ = (
    "PIPELINE_ID",
    "ProviderCallSession",
    "contract_pipeline_error",
    "pipeline_error",
)
