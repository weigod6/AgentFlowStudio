from __future__ import annotations

from typing import Any

from agentflow_studio.model_gateway.errors import ModelGatewayError
from agentflow_studio.model_gateway.provider_adapter import ProviderDispatchRequest
from apps.api.runtime_llm_enhancement_gate import provider_candidates, provider_name
from apps.api.runtime_models import PromptOptimizationRequest


def dispatch_llm_with_fallback(
    registry: Any,
    request: PromptOptimizationRequest,
    dispatch_request: ProviderDispatchRequest,
) -> dict[str, Any]:
    missing: list[str] = []
    for service_id in provider_candidates(request, registry):
        try:
            return registry.dispatch("llm", service_id, dispatch_request)
        except TimeoutError as exc:
            raise ModelGatewayError("Provider request timed out") from exc
        except ModelGatewayError as exc:
            message = str(exc)
            if "Provider service not found" in message or "OpenAI-compatible HTTP error 404" in message:
                missing.append(service_id)
                continue
            raise
    missing_text = ", ".join(missing) if missing else provider_name(request)
    raise ModelGatewayError(f"Provider service not found: {missing_text}")


__all__ = ("dispatch_llm_with_fallback",)
