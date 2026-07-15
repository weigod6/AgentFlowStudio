from __future__ import annotations

import json
from typing import Any

from apps.api.runtime_models import PromptOptimizationRequest, ShotAssetPlanRequest


ASSET_RECOGNITION_CONTRACT = "shot_asset_recognition_v1"


def asset_plan_llm_request(request: ShotAssetPlanRequest, text: str) -> PromptOptimizationRequest:
    return PromptOptimizationRequest(
        node_id=request.node_id,
        node_type="script",
        prompt_text=text,
        generation_target="prompt",
        target_platform=request.target_platform,
        style=request.style,
        node_parameters={
            "llm_provider": "prompt_optimizer",
            "asset_recognition_contract": ASSET_RECOGNITION_CONTRACT,
        },
        generated_at=request.generated_at,
    )


def asset_plan_instruction(request: ShotAssetPlanRequest, text: str) -> str:
    return "\n".join(
        [
            "你是影视分镜资产识别器。请只根据输入分镜和剧本文本识别可复用视觉资产，输出严格 JSON，不要 Markdown。",
            f"契约版本：{ASSET_RECOGNITION_CONTRACT}",
            "核心任务：识别真实出现的角色、动物角色、场景和必要道具；不要根据关键词联想、不要补写新地点、不要把局部物件误判成宏大场景。",
            "输出根对象必须是：{\"assets\": [...], \"dropped_candidates\": [...]}。",
            "assets 每项字段：label, asset_type, character_subtype, evidence_text, facts, continuity_locks, negative_locks, role_in_shot, confidence。",
            "asset_type 只能是 character、scene、prop。",
            "当 asset_type=character 时，character_subtype 只能是 human、animal、robot、subject；猫、狗、鸟、龙等动物必须标为 animal，人类角色必须标为 human。",
            "场景必须是分镜里能稳定复用的地点/空间，例如巷口、厨房、战场、屋顶；不要把“青石台阶”里的“石台”联想成山巅石台战场。",
            "label 必须使用中文或原文已有名称；不要用“主角/角色/人物/主要场景/场景”替代已有真实名称。",
            "evidence_text 必须逐字来自输入文本，并能直接支撑该资产；没有原文证据就放入 dropped_candidates，不要放入 assets。",
            "facts 必须从 evidence_text 和输入上下文抽取，不要套固定模板；能确定多少写多少，不能确定就留空对象。",
            "动物 facts 建议包含 species、color_pattern、size_or_age、surface_state、distinctive_marks、current_action、relationship 等实际出现的信息。",
            "人物 facts 建议包含 identity、appearance_context、wardrobe、hair 等实际出现的信息。",
            "场景 facts 建议包含 location_type、spatial_structure、lighting_atmosphere、key_environment_elements 等实际出现的信息。",
            "continuity_locks/negative_locks 必须基于 facts 生成，用于后续关键帧和视频一致性；不要写与文本无关的限制。",
            "默认只把主要角色与主要场景放入 assets；道具只有在后续画面连续性必须固定时才放入 prop，否则放入 dropped_candidates。",
            "如果你认为某候选不应自动建资产，在 dropped_candidates 中说明 label、asset_type、reason、evidence_text。",
            f"平台：{request.target_platform}；风格：{request.style}",
            "分镜对象：",
            _safe_json(request.shot),
            "已有候选资产：",
            _safe_json(request.existing_assets),
            "输入文本：",
            text,
        ]
    )


def _safe_json(value: Any) -> str:
    return json.dumps(value if isinstance(value, (dict, list)) else {}, ensure_ascii=False, sort_keys=True)[:5000]


__all__ = (
    "ASSET_RECOGNITION_CONTRACT",
    "asset_plan_instruction",
    "asset_plan_llm_request",
)
