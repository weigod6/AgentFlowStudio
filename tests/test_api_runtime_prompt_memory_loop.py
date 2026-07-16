from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from agentflow_studio.model_gateway.errors import ModelGatewayError
from apps.api.runtime_llm_enhancement import maybe_enhance_prompt_with_llm
from apps.api.runtime_llm_enhancement_instructions import enhancement_instruction
from apps.api.runtime_models import PromptOptimizationRequest
from apps.api.openapi_export import export_openapi_schema
from apps.api.runtime_service import create_runtime_app
from apps.api.runtime_script_generation_body import is_script_surface_request


def _runtime_error_raw_detail(result) -> str:
    detail = result.json()["detail"]
    if isinstance(detail, dict):
        details = detail.get("details") if isinstance(detail.get("details"), dict) else {}
        return str(details.get("raw_detail") or detail.get("message") or detail.get("error") or "")
    return str(detail)


def test_node_prompt_optimization_returns_only_optimized_prompt_for_canvas_ui(tmp_path) -> None:
    client = TestClient(create_runtime_app(runtime_root=tmp_path))
    client.post(
        "/projects",
        json={
            "project_id": "proj_libtv_prompt",
            "project_type": "short_video_campaign",
            "goal": "Create node canvas prompts.",
        },
    )

    result = client.post(
        "/projects/proj_libtv_prompt/prompt-optimizations",
        json={
            "node_id": "script-node-001",
            "node_type": "script",
            "prompt_text": "A calm founder walks through a rainy neon street and introduces an AI video tool.",
            "generation_target": "script",
            "target_platform": "short_video",
            "style": "cinematic noir",
            "generated_at": "2026-06-11T10:00:00+08:00",
        },
    )

    assert result.status_code == 200
    payload = result.json()
    brief = client.get(f"/artifacts/{payload['artifacts']['creative_brief']['artifact_id']}").json()["payload"]
    trace = client.get(f"/artifacts/{payload['artifacts']['prompt_assembly_trace']['artifact_id']}").json()["payload"]
    manifest = client.get(f"/artifacts/{payload['artifacts']['prompt_optimization_safe_manifest']['artifact_id']}").json()["payload"]
    contract = client.get(f"/artifacts/{payload['artifacts']['creative_runtime_contract']['artifact_id']}").json()["payload"]
    serialized = json.dumps(
        {"payload": payload, "brief": brief, "trace": trace, "manifest": manifest, "contract": contract},
        ensure_ascii=False,
    ).lower()

    assert payload["job"]["action"] == "prompt_optimization"
    assert payload["job"]["status"] == "succeeded"
    assert payload["ui_surface"] == "node_prompt_optimizer"
    assert payload["original_prompt"].startswith("A calm founder")
    assert payload["optimized_prompt"] == brief["optimized_prompt"]
    assert "lighting" in payload["optimized_prompt"].lower()
    assert trace["context_priority"] == [
        "professional_knowledge_base",
        "script_character_scene_assets",
        "user_preferences",
    ]
    assert {rule["rule_id"] for rule in trace["knowledge_rules"]} >= {
        "cinematography_shot_intent_v1",
        "lighting_mood_v1",
        "character_consistency_v1",
    }
    assert trace["background_context_refs"] == []
    assert trace["extracted_context_refs"]
    assert manifest["provider_calls_started"] is False
    assert manifest["raw_provider_response_stored"] is False
    assert manifest["generated_media_bytes_stored"] is False
    assert manifest["creative_runtime_contract_id"] == contract["contract_id"]
    assert manifest["creative_runtime_contract_ref"] == "creative_runtime_contract.json"
    assert payload["creative_runtime_contract_id"] == contract["contract_id"]
    assert payload["creative_runtime_contract_summary"]["artifact"]["filename"] == "creative_runtime_contract.json"
    assert payload["creative_runtime_contract_summary"]["operation"] == "prompt_optimization"
    assert payload["creative_runtime_contract_summary"]["provider_context"]["required_gate"] == "AFS_ALLOW_REMOTE_LLM"
    assert payload["creative_runtime_contract_summary"]["provider_context"]["provider_calls_started"] is False
    assert contract["model_call_context"]["context_id"] == payload["model_call_context_id"]
    assert contract["runtime_policy"]["writes_long_term_memory"] is False
    assert contract["runtime_policy"]["requires_evaluator_before_quality_claim"] is True
    assert "not_durable_memory_promotion" in contract["non_claims"]
    assert payload["provider_calls_started"] is False
    assert payload["writes_long_term_memory"] is False
    assert payload["writes_company_kb"] is False
    assert "not durable memory" in payload["non_claims"]
    assert "api_key" not in serialized
    assert "bearer " not in serialized
    assert "signed_url" not in serialized
    assert "c:\\" not in serialized
    assert "d:\\" not in serialized
    assert "data/processed/runs" not in serialized


def test_text_node_script_target_uses_script_surface_instruction() -> None:
    request = PromptOptimizationRequest(
        node_id="text-script-surface",
        node_type="text",
        prompt_text="片名：《白骨灯》\n\n唐僧娶了白骨精。孙悟空和猪八戒在远处旁观。结尾，红盖头下露出白骨影子。",
        generation_target="script",
        target_platform="short_video",
        style="cinematic",
        node_parameters={
            "scriptInputMode": "idea_expanded_script",
            "remote_optimizer_required": True,
        },
        generated_at="2026-07-09T10:00:00+08:00",
    )

    instruction = enhancement_instruction(request, {"knowledge_rules": []})

    assert is_script_surface_request(request) is True
    assert "这不是生图提示词优化" in instruction
    assert "输出仍必须像剧本正文" in instruction
    assert "意图、角色/主体" in instruction


def test_prompt_memory_mvp_openapi_exposes_optimizer_but_not_memory_review_surfaces(tmp_path) -> None:
    output_path = tmp_path / "frontend" / "afs-runtime-service.openapi.json"
    exported_path = export_openapi_schema(output_path, runtime_root=tmp_path / "openapi_runtime")
    schema = json.loads(exported_path.read_text(encoding="utf-8"))
    serialized = json.dumps(schema, ensure_ascii=False).lower()

    assert "/projects/{project_id}/prompt-optimizations" in schema["paths"]
    assert "/projects/{project_id}/creative-memory" not in schema["paths"]
    assert "/projects/{project_id}/memory-decisions" not in schema["paths"]
    assert "promptoptimizationrequest" in serialized
    assert "memorydecisionrequest" not in serialized
    assert "api_key" not in serialized
    assert "signed_url" not in serialized


def test_prompt_optimizer_uses_professional_knowledgebase_trace_and_chinese_slots(tmp_path) -> None:
    client = TestClient(create_runtime_app(runtime_root=tmp_path))
    client.post(
        "/projects",
        json={
            "project_id": "proj_zh_prompt_knowledge",
            "project_type": "short_video_campaign",
            "goal": "中文节点提示词优化。",
        },
    )

    result = client.post(
        "/projects/proj_zh_prompt_knowledge/prompt-optimizations",
        json={
            "node_id": "video-node-zh-001",
            "node_type": "video",
            "prompt_text": "一个男孩坐在昏暗房间里，墙上有海报，情绪低落，镜头缓慢推进。",
            "generation_target": "video",
            "target_platform": "short_video",
            "style": "用户偏好：高饱和、夸张炫光、快速甩镜；项目风格：克制、真实、电影感、低照度室内光线",
            "generated_at": "2026-06-11T11:00:00+08:00",
        },
    )

    assert result.status_code == 200
    payload = result.json()
    trace = client.get(f"/artifacts/{payload['artifacts']['prompt_assembly_trace']['artifact_id']}").json()["payload"]
    manifest = client.get(f"/artifacts/{payload['artifacts']['prompt_optimization_safe_manifest']['artifact_id']}").json()["payload"]
    brief = client.get(f"/artifacts/{payload['artifacts']['creative_brief']['artifact_id']}").json()["payload"]

    assert manifest["knowledgebase_version"] == "creative_prompt_knowledgebase_v1"
    assert manifest["knowledgebase_rules_count"] >= 40
    assert manifest["knowledgebase_registry_hash"]
    assert all("match_reason" in rule for rule in trace["knowledge_rules"])
    assert all("weight" in rule for rule in trace["knowledge_rules"])
    assert {rule["rule_id"] for rule in trace["knowledge_rules"]} >= {
        "video_motion_temporal_progression_v1",
        "lighting_motivated_low_key_v1",
        "negative_no_provider_claim_v1",
    }
    assert trace["selected_slots"]["language"] == "zh"
    assert trace["selected_slots"].get("subject")
    assert trace["selected_slots"].get("scene")
    assert trace["conflict_resolution"]["policy"] == "professional_knowledge_over_user_preference"
    assert trace["suppressed_context"]
    assert "Intent:" in brief["optimized_prompt"]
    assert "Subject/Character:" in brief["optimized_prompt"]
    assert "Lighting:" in brief["optimized_prompt"]
    assert "Negative Constraints:" in brief["optimized_prompt"]
    assert "provider calls remain off" in brief["optimized_prompt"].lower()
    assert payload["provider_calls_started"] is False


def test_prompt_optimizer_can_apply_gated_prompt_optimizer_enhancement(tmp_path, monkeypatch) -> None:
    class FakeRegistry:
        def dispatch(self, capability, service_id, request):
            assert capability == "llm"
            assert service_id == "prompt_optimizer"
            assert request.task_type == "prompt_enhancement"
            prompt = request.prompt
            assert "原始提示词：" in prompt
            assert "输出必须只有以下九行" in prompt
            assert "硬性要求：" in prompt
            return {"text": "\n".join(
                [
                    "意图：为安静的创始人场景生成一张可控关键帧。",
                    "人物/主体：保持创始人的身份、服装和神态稳定清晰。",
                    "场景/美术：夜间工作室、玻璃墙、克制道具，避免杂乱背景。",
                    "动作/情节：创始人在发言前短暂停顿，情绪内敛但有压力。",
                    "镜头/构图：竖构图中景，主体位置明确，背景信息服务叙事。",
                    "灯光：低调实景窗光，柔和反差，保留面部可读性。",
                    "运动/时间推进：静态关键帧，暗示缓慢推进但不制造运动模糊。",
                    "连续性：延续服装、空间方位和主光方向。",
                    "负面约束：不要水印，不要手部畸形，不要身份漂移。",
                ]
            )}

    monkeypatch.setenv("AFS_ALLOW_REMOTE_LLM", "true")
    monkeypatch.setattr("apps.api.runtime_llm_enhancement.load_provider_registry", lambda: FakeRegistry())
    client = TestClient(create_runtime_app(runtime_root=tmp_path))

    result = client.post(
        "/projects/proj_relay_llm/prompt-optimizations",
        json={
            "node_id": "image-node-prompt-text",
            "node_type": "image",
            "prompt_text": "A founder stands in a night studio before a product launch.",
            "generation_target": "keyframe",
            "target_platform": "short_video",
            "style": "cinematic",
            "node_parameters": {
                "model": "image2-keyframe",
                "llm_provider": "prompt_optimizer",
                "llm_model": "prompt-optimizer",
            },
            "generated_at": "2026-06-12T13:00:00+08:00",
        },
    )

    assert result.status_code == 200
    payload = result.json()
    trace = client.get(f"/artifacts/{payload['artifacts']['prompt_assembly_trace']['artifact_id']}").json()["payload"]
    brief = client.get(f"/artifacts/{payload['artifacts']['creative_brief']['artifact_id']}").json()["payload"]
    manifest = client.get(f"/artifacts/{payload['artifacts']['prompt_optimization_safe_manifest']['artifact_id']}").json()["payload"]
    serialized = json.dumps({"payload": payload, "trace": trace, "brief": brief, "manifest": manifest}, ensure_ascii=False).lower()

    assert payload["provider_calls_started"] is True
    assert payload["optimized_prompt"].startswith("意图：为安静的创始人场景生成")
    assert "Intent:" not in payload["optimized_prompt"]
    assert payload["user_prompt"] == payload["optimized_prompt"]
    assert trace["llm_enhancement"]["status"] == "applied"
    assert trace["llm_enhancement"]["model"] == "provider_configured"
    assert manifest["llm_enhancement"]["raw_response_stored"] is False
    assert brief["provider_calls_started"] is True
    assert "api_key" not in serialized
    assert "bearer " not in serialized
    assert "reasoning_content" not in serialized
    assert "c:\\" not in serialized
    assert "d:\\" not in serialized


def test_studio_prompt_optimizer_uses_llm_fields_even_when_image_model_is_selected() -> None:
    from apps.api.runtime_llm_enhancement import provider_text_requested
    from apps.api.runtime_models import PromptOptimizationRequest

    request = PromptOptimizationRequest(
        node_id="image-node-studio-llm-fields",
        node_type="image",
        prompt_text="A young woman turns back on a rainy neon street.",
        generation_target="image",
        target_platform="short_video",
        style="cinematic",
        node_parameters={
            "model": "image2-keyframe",
            "llm_provider": "prompt_optimizer",
            "llm_model": "prompt-optimizer",
            "remote_optimizer_required": True,
        },
        generated_at="2026-06-14T05:20:00+08:00",
    )

    assert provider_text_requested(request) is True


def test_visual_prompt_instruction_distinguishes_subject_and_style_references() -> None:
    from apps.api.runtime_llm_enhancement_instructions import enhancement_instruction
    from apps.api.runtime_models import PromptOptimizationRequest

    request = PromptOptimizationRequest(
        node_id="image-node-reference-role",
        node_type="image",
        prompt_text="生成一只清晰自然的狸花猫单帧画面，主体明确、质感真实。",
        generation_target="image",
        target_platform="short_video",
        style="cinematic",
        node_parameters={
            "model": "image2-keyframe",
            "llm_provider": "prompt_optimizer",
            "uploaded_images": [
                {
                    "asset_id": "img_ref_cat_lab",
                    "filename": "ChatGPT Image Dec 28, 2025, 06_57_23 PM.png",
                    "role": "reference_image",
                }
            ],
        },
        generated_at="2026-06-21T06:40:00+08:00",
    )

    instruction = enhancement_instruction(request, {})

    assert "参考图用途" in instruction
    assert "主体参考" in instruction
    assert "风格参考" in instruction
    assert "如果原始提示词明确指定新主体" in instruction
    assert "不要把参考图主体替换用户指定主体" in instruction
    assert "狸花猫" in instruction


def test_i2i_guardrail_allows_new_subject_with_style_reference(tmp_path, monkeypatch) -> None:
    class FakeRegistry:
        def dispatch(self, capability, service_id, request):
            assert capability == "llm"
            return {"text": "\n".join(
                [
                    "意图：生成一只清晰自然的狸花猫单帧画面。",
                    "人物/主体：狸花猫主体明确，棕灰黑相间虎斑纹、额头 M 字纹、短毛和明亮眼睛清楚。",
                    "场景/美术：背景保持干净自然，参考图只提供画面质感和可读性，不复制参考图主体。",
                    "动作/情节：只呈现一只狸花猫，不新增人物、服装或额外角色。",
                    "镜头/构图：主体居中，完整可辨识，关键特征清晰呈现。",
                    "灯光：自然柔和，毛色、眼神和轮廓清楚。",
                    "运动/时间推进：单帧关键画面，不制造多阶段动作。",
                    "连续性：用户指定主体优先，参考图仅作风格线索，不替换狸花猫主体。",
                    "负面约束：不要水印、文字乱码、五官畸形、身体比例异常、毛色错乱或身份漂移。",
                ]
            )}

    monkeypatch.setenv("AFS_ALLOW_REMOTE_LLM", "true")
    monkeypatch.setattr("apps.api.runtime_llm_enhancement.load_provider_registry", lambda: FakeRegistry())
    client = TestClient(create_runtime_app(runtime_root=tmp_path))

    result = client.post(
        "/projects/proj_i2i_style_reference/prompt-optimizations",
        json={
            "node_id": "image-node-style-reference",
            "node_type": "image",
            "prompt_text": "生成一只清晰自然的狸花猫单帧画面，主体明确、质感真实。",
            "generation_target": "image",
            "target_platform": "short_video",
            "style": "cinematic",
            "node_parameters": {
                "model": "image2-keyframe",
                "llm_provider": "prompt_optimizer",
                "remote_optimizer_required": True,
                "uploaded_images": [
                    {
                        "asset_id": "img_ref_cat_lab",
                        "filename": "ChatGPT Image Dec 28, 2025, 06_57_23 PM.png",
                        "role": "reference_image",
                    }
                ],
            },
            "generated_at": "2026-06-21T06:50:00+08:00",
        },
    )

    payload = result.json()
    assert result.status_code == 200
    assert payload["provider_calls_started"] is True
    assert payload["safe_manifest"]["llm_enhancement"]["guardrail_fallback_used"] is False
    assert "狸花猫" in payload["optimized_prompt"]
    assert "ChatGPT Image Dec" not in payload["optimized_prompt"]
    assert "参考图仅作风格线索" in payload["optimized_prompt"]


def test_i2i_style_reference_fallback_does_not_reuse_reference_subject() -> None:
    from apps.api.runtime_llm_enhancement_fallback import deterministic_chinese_fallback_prompt

    request = PromptOptimizationRequest(
        node_id="image-node-style-reference-fallback",
        node_type="image",
        prompt_text="生成一只清晰自然的狸花猫单帧画面，主体明确、质感真实。",
        generation_target="image",
        target_platform="short_video",
        style="cinematic",
        node_parameters={
            "model": "image2-keyframe",
            "uploaded_images": [
                {
                    "asset_id": "img_ref_cat_lab",
                    "filename": "ChatGPT Image Dec 28, 2025, 06_57_23 PM.png",
                    "role": "reference_image",
                }
            ],
        },
        generated_at="2026-06-21T06:55:00+08:00",
    )

    fallback = deterministic_chinese_fallback_prompt(request, {"selected_slots": {}})

    assert "参考图只作为风格" in fallback
    assert "不替换用户指定的新主体" in fallback
    assert "狸花猫" in fallback
    assert "对ChatGPT Image" not in fallback


def test_i2i_originalize_reference_fallback_redesigns_instead_of_preserving_identity() -> None:
    from apps.api.runtime_llm_enhancement_fallback import deterministic_chinese_fallback_prompt

    request = PromptOptimizationRequest(
        node_id="image-node-originalize-reference-fallback",
        node_type="image",
        prompt_text="参考这张图的气质，重新设计成原创角色",
        generation_target="keyframe",
        target_platform="short_video",
        style="cinematic",
        asset_refs=["img_reference_ip_risk"],
        node_parameters={
            "model": "image2-keyframe",
            "reference_transform_mode": "originalize_ip_safe",
            "uploaded_images": [
                {
                    "asset_id": "img_reference_ip_risk",
                    "filename": "reference-character.png",
                    "role": "reference_image",
                    "reference_target": "asset_card_draft",
                }
            ],
        },
        generated_at="2026-07-12T10:00:00+08:00",
    )

    fallback = deterministic_chinese_fallback_prompt(request, {"selected_slots": {}})

    assert "降低可识别 IP 相似度" in fallback
    assert "重新设计身份" in fallback
    assert "不要复制已知角色" in fallback
    assert "保持参考图脸部辨识度" not in fallback
    assert "primary visual source of truth" not in fallback


def test_i2i_animal_subject_reference_fallback_avoids_human_template() -> None:
    from apps.api.runtime_llm_enhancement_fallback import deterministic_chinese_fallback_prompt

    request = PromptOptimizationRequest(
        node_id="image-node-animal-reference-fallback",
        node_type="image",
        prompt_text="根据上游节点的狸花猫,生成这只猫在房间里跳舞的图片",
        generation_target="keyframe",
        target_platform="short_video",
        style="cinematic",
        asset_refs=["img_upstream_cat"],
        node_parameters={
            "model": "image2-keyframe",
            "connected_reference_nodes": [
                {
                    "title": "图片 / 关键帧",
                    "prompt": "意图：生成一只清晰自然的狸花猫单帧画面，棕灰黑虎斑纹和额头 M 字纹清楚。",
                }
            ],
        },
        generated_at="2026-06-21T03:45:00+08:00",
    )

    fallback = deterministic_chinese_fallback_prompt(request, {"selected_slots": {}})

    assert "角色/主体" in fallback
    assert "狸花猫" in fallback
    assert "房间" in fallback
    assert "跳舞" in fallback
    assert "除非用户明确要求" in fallback
    assert "必须重绘为统一、连贯的完整主体" in fallback
    assert "短发" not in fallback
    assert "校服" not in fallback


def test_i2i_animal_subject_reference_allows_explicit_stylization() -> None:
    from apps.api.runtime_llm_enhancement_fallback import deterministic_chinese_fallback_prompt

    request = PromptOptimizationRequest(
        node_id="image-node-animal-style-fallback",
        node_type="image",
        prompt_text="根据上游节点的狸花猫,生成这只猫穿红色外套在房间里跳舞的图片",
        generation_target="keyframe",
        target_platform="short_video",
        style="cinematic",
        asset_refs=["img_upstream_cat"],
        node_parameters={
            "model": "image2-keyframe",
            "connected_reference_nodes": [
                {"title": "狸花猫参考", "prompt": "参考图中的狸花猫，棕灰黑虎斑纹。"}
            ],
        },
        generated_at="2026-06-21T03:46:00+08:00",
    )

    fallback = deterministic_chinese_fallback_prompt(request, {"selected_slots": {}})

    assert "角色/主体" in fallback
    assert "狸花猫" in fallback
    assert "红色外套" in fallback
    assert "按用户明确要求处理拟人化、服装、饰品或卡通化" in fallback
    assert "短发" not in fallback


def test_i2i_animal_subject_reference_allows_english_clothing_request() -> None:
    from apps.api.runtime_llm_enhancement_fallback import deterministic_chinese_fallback_prompt

    request = PromptOptimizationRequest(
        node_id="image-node-animal-english-style-fallback",
        node_type="image",
        prompt_text="use upstream tabby cat reference, make this cat wear a red little coat and dance",
        generation_target="keyframe",
        target_platform="short_video",
        style="cinematic",
        asset_refs=["img_upstream_cat"],
        node_parameters={
            "model": "image2-keyframe",
            "connected_reference_nodes": [
                {"title": "tabby cat reference", "prompt": "reference animal subject"}
            ],
        },
        generated_at="2026-06-21T03:47:00+08:00",
    )

    fallback = deterministic_chinese_fallback_prompt(request, {"selected_slots": {}})

    assert "角色/主体" in fallback
    assert "狸花猫" in fallback
    assert "red little coat" in fallback
    assert "按用户明确要求处理拟人化、服装、饰品或卡通化" in fallback
    assert "短发" not in fallback


def test_prompt_optimizer_falls_back_to_available_relay_llm_service(tmp_path, monkeypatch) -> None:
    class Descriptor:
        modality = "llm"

    class FakeRegistry:
        _descriptors = {"relay_llm": Descriptor()}

        def __init__(self) -> None:
            self.calls: list[str] = []

        def dispatch(self, capability, service_id, request):
            assert capability == "llm"
            self.calls.append(service_id)
            if service_id == "prompt_optimizer":
                raise ModelGatewayError("Provider service not found: prompt_optimizer")
            assert service_id == "relay_llm"
            return {"text": "\n".join(
                [
                    "意图：为安静的创始人场景生成一张可控关键帧。",
                    "人物/主体：保持创始人的身份、服装和神态稳定清晰。",
                    "场景/美术：夜间工作室、玻璃墙、克制道具，避免杂乱背景。",
                    "动作/情节：创始人在发言前短暂停顿，情绪内敛但有压力。",
                    "镜头/构图：竖构图中景，主体位置明确，背景信息服务叙事。",
                    "灯光：低调实景窗光，柔和反差，保留面部可读性。",
                    "运动/时间推进：静态关键帧，暗示缓慢推进但不制造运动模糊。",
                    "连续性：延续服装、空间方位和主光方向。",
                    "负面约束：不要水印，不要手部畸形，不要身份漂移。",
                ]
            )}

    registry = FakeRegistry()
    monkeypatch.setenv("AFS_ALLOW_REMOTE_LLM", "true")
    monkeypatch.setattr("apps.api.runtime_llm_enhancement.load_provider_registry", lambda: registry)
    client = TestClient(create_runtime_app(runtime_root=tmp_path))

    result = client.post(
        "/projects/proj_relay_llm_alias/prompt-optimizations",
        json={
            "node_id": "image-node-prompt-alias",
            "node_type": "image",
            "prompt_text": "A founder stands in a night studio before a product launch.",
            "generation_target": "keyframe",
            "target_platform": "short_video",
            "style": "cinematic",
            "node_parameters": {
                "model": "image2-keyframe",
                "llm_provider": "prompt_optimizer",
            },
            "generated_at": "2026-06-13T13:00:00+08:00",
        },
    )

    assert result.status_code == 200
    assert registry.calls[:2] == ["prompt_optimizer", "relay_llm"]
    assert result.json()["provider_calls_started"] is True


def test_prompt_optimizer_skips_incompatible_llm_endpoint_404(tmp_path, monkeypatch) -> None:
    class Descriptor:
        modality = "llm"

    class FakeRegistry:
        _descriptors = {"deepseek_llm": Descriptor()}

        def __init__(self) -> None:
            self.calls: list[str] = []

        def dispatch(self, capability, service_id, request):
            assert capability == "llm"
            self.calls.append(service_id)
            if service_id == "prompt_optimizer":
                raise ModelGatewayError(f"OpenAI-compatible HTTP error 404: unavailable {service_id}")
            assert service_id == "deepseek_llm"
            return {"text": "\n".join(
                [
                    "意图：为安静的创始人场景生成一张可控关键帧。",
                    "人物/主体：保持创始人的身份、服装和神态稳定清晰。",
                    "场景/美术：夜间工作室、玻璃墙、克制道具，避免杂乱背景。",
                    "动作/情节：创始人在发言前短暂停顿，情绪内敛但有压力。",
                    "镜头/构图：竖构图中景，主体位置明确，背景信息服务叙事。",
                    "灯光：低调实景窗光，柔和反差，保留面部可读性。",
                    "运动/时间推进：静态关键帧，暗示缓慢推进但不制造运动模糊。",
                    "连续性：延续服装、空间方位和主光方向。",
                    "负面约束：不要水印，不要手部畸形，不要身份漂移。",
                ]
            )}

    registry = FakeRegistry()
    monkeypatch.setenv("AFS_ALLOW_REMOTE_LLM", "true")
    monkeypatch.setattr("apps.api.runtime_llm_enhancement.load_provider_registry", lambda: registry)
    client = TestClient(create_runtime_app(runtime_root=tmp_path))

    result = client.post(
        "/projects/proj_relay_llm_404_fallback/prompt-optimizations",
        json={
            "node_id": "image-node-prompt-404-fallback",
            "node_type": "image",
            "prompt_text": "A founder stands in a night studio before a product launch.",
            "generation_target": "keyframe",
            "target_platform": "short_video",
            "style": "cinematic",
            "node_parameters": {"model": "image2-keyframe", "llm_provider": "prompt_optimizer"},
            "generated_at": "2026-06-13T13:10:00+08:00",
        },
    )

    assert result.status_code == 200
    assert registry.calls == ["prompt_optimizer", "deepseek_llm"]
    assert result.json()["provider_calls_started"] is True


def test_prompt_optimizer_uses_chinese_fallback_when_provider_output_is_templated(tmp_path, monkeypatch) -> None:
    class FakeRegistry:
        def dispatch(self, capability, service_id, request):
            assert capability == "llm"
            assert service_id == "prompt_optimizer"
            assert request.task_type in {"prompt_enhancement", "prompt_enhancement_retry"}
            return {"text": "\n".join(
                [
                    "Intent: generic scene.",
                    "Subject/Character: Primary character with stable identity.",
                    "Scene/Production Design: primary scene.",
                    "Camera/Framing: medium shot.",
                    "Lighting: cinematic light.",
                    "Negative Constraints: no watermark.",
                ]
            )}

    monkeypatch.setenv("AFS_ALLOW_REMOTE_LLM", "true")
    monkeypatch.setattr("apps.api.runtime_llm_enhancement.load_provider_registry", lambda: FakeRegistry())
    client = TestClient(create_runtime_app(runtime_root=tmp_path))

    result = client.post(
        "/projects/proj_relay_llm_fallback/prompt-optimizations",
        json={
            "node_id": "image-node-prompt-text-fallback",
            "node_type": "image",
            "prompt_text": "一个穿黑色风衣的年轻女导演，短发，雨夜天台，城市霓虹在湿地上反光。",
            "generation_target": "keyframe",
            "target_platform": "short_video",
            "style": "cinematic",
            "node_parameters": {
                "model": "image2-keyframe",
                "aspect_ratio": "9:16",
                "llm_provider": "prompt_optimizer",
                "llm_model": "prompt-optimizer",
            },
            "generated_at": "2026-06-12T13:10:00+08:00",
        },
    )

    assert result.status_code == 200
    payload = result.json()
    trace = client.get(f"/artifacts/{payload['artifacts']['prompt_assembly_trace']['artifact_id']}").json()["payload"]

    assert payload["provider_calls_started"] is True
    assert payload["optimized_prompt"].startswith("意图：")
    assert "角色/主体：" in payload["optimized_prompt"]
    assert "Primary character" not in payload["optimized_prompt"]
    assert trace["llm_enhancement"]["status"] == "discarded"
    assert trace["llm_enhancement"]["discard_reason"] == "enhancement missing required sections"
    assert trace["llm_enhancement"]["format_retry_count"] == 1


def test_prompt_optimizer_llm_enhancement_uses_provider_registry_not_legacy_gateway() -> None:
    source = Path("apps/api/runtime_llm_enhancement.py").read_text(encoding="utf-8")

    assert "ModelGateway.from_config_path" not in source
    assert "MODEL_GATEWAY_CONFIG_ENV" not in source
    assert "load_provider_registry" in source


def test_studio_prompt_optimizer_requires_remote_llm_when_requested(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("AFS_ALLOW_REMOTE_LLM", raising=False)
    client = TestClient(create_runtime_app(runtime_root=tmp_path))

    result = client.post(
        "/projects/proj_studio_remote_optimizer/prompt-optimizations",
        json={
            "node_id": "image-node-studio",
            "node_type": "image",
            "prompt_text": "A school-uniform character sheet for a quiet young woman.",
            "generation_target": "keyframe",
            "target_platform": "short_video",
            "style": "cinematic",
            "node_parameters": {
                "model": "image2-keyframe",
                "llm_provider": "prompt_optimizer",
                "remote_optimizer_required": True,
            },
            "generated_at": "2026-06-13T01:30:00+08:00",
        },
    )

    assert result.status_code == 422
    assert "remote LLM prompt optimization unavailable" in _runtime_error_raw_detail(result)


def test_studio_prompt_optimizer_does_not_fallback_when_remote_llm_output_is_rejected(tmp_path, monkeypatch) -> None:
    class FakeRegistry:
        def dispatch(self, capability, service_id, request):
            assert capability == "llm"
            assert service_id == "prompt_optimizer"
            return {"text": "Subject/Character: Primary character with stable identity."}

    monkeypatch.setenv("AFS_ALLOW_REMOTE_LLM", "true")
    monkeypatch.setattr("apps.api.runtime_llm_enhancement.load_provider_registry", lambda: FakeRegistry())
    client = TestClient(create_runtime_app(runtime_root=tmp_path))

    result = client.post(
        "/projects/proj_studio_remote_optimizer_rejected/prompt-optimizations",
        json={
            "node_id": "image-node-studio-rejected",
            "node_type": "image",
            "prompt_text": "A desert walking keyframe with a fixed character.",
            "generation_target": "keyframe",
            "target_platform": "short_video",
            "style": "cinematic",
            "node_parameters": {
                "model": "image2-keyframe",
                "llm_provider": "prompt_optimizer",
                "remote_optimizer_required": True,
            },
            "generated_at": "2026-06-13T01:35:00+08:00",
        },
    )

    assert result.status_code == 422
    assert "enhancement missing required sections" in _runtime_error_raw_detail(result)


def test_studio_prompt_optimizer_can_disable_format_retry_for_stop_loss(tmp_path, monkeypatch) -> None:
    calls: list[str] = []

    class FakeRegistry:
        def dispatch(self, capability, service_id, request):
            assert capability == "llm"
            assert service_id == "prompt_optimizer"
            calls.append(request.task_type)
            return {"text": "只有一句不合格的返回。"}

    monkeypatch.setenv("AFS_ALLOW_REMOTE_LLM", "true")
    monkeypatch.setattr("apps.api.runtime_llm_enhancement.load_provider_registry", lambda: FakeRegistry())
    client = TestClient(create_runtime_app(runtime_root=tmp_path))

    result = client.post(
        "/projects/proj_studio_remote_optimizer_retry_disabled/prompt-optimizations",
        json={
            "node_id": "image-node-studio-retry-disabled",
            "node_type": "image",
            "prompt_text": "A desert walking keyframe with a fixed character.",
            "generation_target": "keyframe",
            "target_platform": "short_video",
            "style": "cinematic",
            "node_parameters": {
                "model": "image2-keyframe",
                "llm_provider": "prompt_optimizer",
                "remote_optimizer_required": True,
                "disable_provider_retry": True,
            },
            "generated_at": "2026-06-13T01:36:00+08:00",
        },
    )

    assert result.status_code == 422
    assert calls == ["prompt_enhancement"]
    assert "enhancement_missing_required_sections_retry_disabled" in _runtime_error_raw_detail(result)


def test_studio_prompt_optimizer_rejects_provider_infrastructure_error_text(tmp_path, monkeypatch) -> None:
    provider_error = (
        "Unable to read `request.json` or `prompt.md`: the local command sandbox fails "
        "with `bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted`."
    )

    class FakeRegistry:
        def dispatch(self, capability, service_id, request):
            assert capability == "llm"
            assert service_id == "prompt_optimizer"
            return {"text": provider_error}

    monkeypatch.setenv("AFS_ALLOW_REMOTE_LLM", "true")
    monkeypatch.setattr("apps.api.runtime_llm_enhancement.load_provider_registry", lambda: FakeRegistry())
    client = TestClient(create_runtime_app(runtime_root=tmp_path))

    result = client.post(
        "/projects/proj_studio_remote_optimizer_provider_error/prompt-optimizations",
        json={
            "node_id": "image-node-studio-provider-error",
            "node_type": "image",
            "prompt_text": "老师，在办公室，批评玩手机的学生",
            "generation_target": "keyframe",
            "target_platform": "short_video",
            "style": "cinematic",
            "node_parameters": {
                "model": "image2-keyframe",
                "llm_provider": "prompt_optimizer",
                "remote_optimizer_required": True,
            },
            "generated_at": "2026-06-28T12:30:00+08:00",
        },
    )

    assert result.status_code == 422
    detail = _runtime_error_raw_detail(result)
    assert "provider returned infrastructure error" in detail
    assert "Unable to read" not in detail
    assert "request.json" not in detail
    assert "bwrap" not in detail


def test_studio_prompt_optimizer_rejects_local_file_access_error_inside_sections(tmp_path, monkeypatch) -> None:
    provider_error = "I can't access the requested local files in this session."
    provider_text = "\n".join(
        [
            "Intent: create a usable visual prompt for a teacher scolding a student in an office.",
            f"Subject/Character: {provider_error}",
            f"Scene/Art Direction: {provider_error}",
            "Action/Story: a teacher scolds a student for playing with a phone in an office.",
            f"Camera/Composition: {provider_error}",
            f"Lighting: {provider_error}",
            "Motion/Time Progression: keep the single keyframe readable and stable.",
            "Continuity: keep the teacher, student, clothing, and office consistent.",
            "Negative Constraints: no watermark, no gibberish text, no identity drift.",
        ]
    )

    class FakeRegistry:
        def dispatch(self, capability, service_id, request):
            assert capability == "llm"
            assert service_id == "prompt_optimizer"
            return {"text": provider_text}

    monkeypatch.setenv("AFS_ALLOW_REMOTE_LLM", "true")
    monkeypatch.setattr("apps.api.runtime_llm_enhancement.load_provider_registry", lambda: FakeRegistry())
    client = TestClient(create_runtime_app(runtime_root=tmp_path))

    result = client.post(
        "/projects/proj_studio_remote_optimizer_local_file_error/prompt-optimizations",
        json={
            "node_id": "script-node-local-file-error",
            "node_type": "script",
            "prompt_text": "A teacher scolds a student for playing with a phone in an office.",
            "generation_target": "script",
            "target_platform": "short_video",
            "style": "cinematic",
            "node_parameters": {
                "model": "text",
                "llm_provider": "prompt_optimizer",
                "remote_optimizer_required": True,
            },
            "generated_at": "2026-06-28T15:10:00+08:00",
        },
    )

    assert result.status_code == 422
    detail = _runtime_error_raw_detail(result)
    assert "provider returned infrastructure error" in detail
    assert "local files" not in detail.lower()
    assert "requested local files" not in detail.lower()


def test_studio_prompt_optimizer_does_not_retry_or_salvage_infrastructure_error(tmp_path, monkeypatch) -> None:
    first_provider_text = (
        "I'm unable to read the files because the local command sandbox is failing "
        "before any command runs."
    )
    retry_provider_text = "Unable to read `request.json`: bwrap: Failed RTM_NEWADDR: Operation not permitted"

    class FakeRegistry:
        def __init__(self):
            self.calls = 0

        def dispatch(self, capability, service_id, request):
            assert capability == "llm"
            assert service_id == "prompt_optimizer"
            self.calls += 1
            return {"text": first_provider_text if self.calls == 1 else retry_provider_text}

    fake_registry = FakeRegistry()
    monkeypatch.setenv("AFS_ALLOW_REMOTE_LLM", "true")
    monkeypatch.setattr("apps.api.runtime_llm_enhancement.load_provider_registry", lambda: fake_registry)
    client = TestClient(create_runtime_app(runtime_root=tmp_path))

    result = client.post(
        "/projects/proj_studio_remote_optimizer_retry_infra_error/prompt-optimizations",
        json={
            "node_id": "script-node-retry-infra-error",
            "node_type": "script",
            "prompt_text": "A teacher scolds a student for playing with a phone in an office.",
            "generation_target": "script",
            "target_platform": "short_video",
            "style": "cinematic",
            "node_parameters": {
                "model": "text",
                "llm_provider": "prompt_optimizer",
                "remote_optimizer_required": True,
            },
            "generated_at": "2026-06-28T15:30:00+08:00",
        },
    )

    assert result.status_code == 422
    assert fake_registry.calls == 1
    detail = _runtime_error_raw_detail(result)
    assert "provider returned infrastructure error" in detail
    assert "sandbox" not in detail.lower()
    assert "request.json" not in detail


def test_prompt_optimizer_failure_file_log_includes_timings(tmp_path, monkeypatch) -> None:
    provider_error = "Unable to read `request.json`: bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted"

    class FakeRegistry:
        def dispatch(self, capability, service_id, request):
            assert capability == "llm"
            assert service_id == "prompt_optimizer"
            return {"text": provider_error}

    log_dir = tmp_path / "logs"
    monkeypatch.setenv("AFS_ALLOW_REMOTE_LLM", "true")
    monkeypatch.setenv("AFS_FILE_LOG_ENABLED", "true")
    monkeypatch.setenv("AFS_FILE_LOG_DIR", str(log_dir))
    monkeypatch.setenv("AFS_FILE_LOG_NAME", "afs-test")
    monkeypatch.setattr("apps.api.runtime_llm_enhancement.load_provider_registry", lambda: FakeRegistry())
    client = TestClient(create_runtime_app(runtime_root=tmp_path / "runtime"))

    result = client.post(
        "/projects/proj_studio_prompt_log/prompt-optimizations",
        headers={
            "x-client-request-id": "cli_prompt_log",
            "x-user-action": "click_optimize_prompt",
            "x-studio-node-id": "node_prompt_log",
            "x-studio-node-type": "script",
        },
        json={
            "node_id": "node_prompt_log",
            "node_type": "script",
            "prompt_text": "老师，在办公室，批评玩手机的学生",
            "generation_target": "script",
            "target_platform": "short_video",
            "style": "cinematic",
            "node_parameters": {
                "model": "text",
                "llm_provider": "prompt_optimizer",
                "remote_optimizer_required": True,
            },
            "generated_at": "2026-06-28T14:20:00+08:00",
        },
    )

    assert result.status_code == 422
    lines = "\n".join(path.read_text(encoding="utf-8") for path in log_dir.glob("afs-test-*.log"))
    assert "prompt optimize_start" in lines
    assert "prompt optimize_failed" in lines
    assert "provider=prompt_optimizer" in lines
    assert "llm_status=discarded" in lines
    assert "discard_reason=\"provider returned infrastructure error\"" in lines
    assert "elapsed_ms=" in lines
    assert "llm_elapsed_ms=" in lines
    assert "provider_elapsed_ms=" in lines
    assert "provider_output_length=" in lines
    assert "provider_error_markers=" in lines
    assert "missing_sections=" in lines
    assert "provider_output_preview=" in lines
    assert "request.json" in lines
    assert "bwrap" in lines


def test_studio_prompt_optimizer_accepts_common_llm_section_format_variants(tmp_path, monkeypatch) -> None:
    class FakeRegistry:
        def dispatch(self, capability, service_id, request):
            assert capability == "llm"
            assert service_id == "prompt_optimizer"
            return {"text": "\n".join(
                [
                    "1. 【意图】生成一张未来机器人屋顶观星的完整概念图。",
                    "2. [人物] 未来机器人主体清晰，金属结构和发光部件具备辨识度。",
                    "3. 场景：星际屋顶场景宽阔，远处星空和城市轮廓补足空间层次。",
                    "- 动作：机器人安静站立，像是在观察远处星空。",
                    "- 镜头：低机位中景，主体居中偏右，保留环境纵深。",
                    "- 灯光：冷色星光与柔和边缘光突出金属轮廓。",
                    "- 运动：单帧概念图，强调静态瞬间和氛围。",
                    "- 连续性：保持未来科幻主题，不漂移到其他题材。",
                    "- 负面：不要水印、乱码、畸形肢体或无关角色。",
                ]
            )}

    monkeypatch.setenv("AFS_ALLOW_REMOTE_LLM", "true")
    monkeypatch.setattr("apps.api.runtime_llm_enhancement.load_provider_registry", lambda: FakeRegistry())
    client = TestClient(create_runtime_app(runtime_root=tmp_path))

    result = client.post(
        "/projects/proj_studio_remote_optimizer_section_variants/prompt-optimizations",
        json={
            "node_id": "image-node-studio-section-variants",
            "node_type": "image",
            "prompt_text": "一个来自未来的机器人，在屋顶看星星",
            "generation_target": "keyframe",
            "target_platform": "short_video",
            "style": "cinematic",
            "node_parameters": {
                "model": "image2-keyframe",
                "llm_provider": "prompt_optimizer",
                "remote_optimizer_required": True,
            },
            "generated_at": "2026-06-14T03:30:00+08:00",
        },
    )

    assert result.status_code == 200
    payload = result.json()
    titles = [section["title"] for section in payload["user_prompt_sections"]]
    assert "角色/主体" in titles
    assert "场景/美术" in titles
    assert "镜头/构图" in titles
    assert "负面约束" in titles
    assert "【意图】" not in payload["optimized_prompt"]


def test_studio_prompt_optimizer_retries_once_when_llm_returns_chatty_article(tmp_path, monkeypatch) -> None:
    calls: list[str] = []

    class FakeRegistry:
        def dispatch(self, capability, service_id, request):
            assert capability == "llm"
            assert service_id == "prompt_optimizer"
            calls.append(request.task_type)
            if len(calls) == 1:
                return {
                    "text": "\n".join(
                        [
                            "下面为您把原始提示词扩展成适合图像生成模型的完整提示词。",
                            "## 强化版 Prompt",
                            "A cinematic rooftop scene with dramatic rain and city lights.",
                            "## Negative Prompt",
                            "low quality, watermark, bad anatomy",
                        ]
                    )
                }
            return {"text": "\n".join(
                [
                    "意图：生成一张雨夜屋顶红发人物的电影关键帧。",
                    "人物/主体：Lin Wan 站在屋顶边缘，红色长发被风吹动，神情坚定。",
                    "场景/美术：雨夜城市屋顶，远处霓虹和湿润地面形成反光。",
                    "动作/情节：人物静立在雨中，像是在等待关键时刻。",
                    "镜头/构图：低角度中景，主体位于画面中央偏右，背景保留城市纵深。",
                    "灯光：冷色雨夜环境光与暖色轮廓光共同塑造电影感。",
                    "运动/时间推进：单帧关键画面，保留雨丝和发丝的短时间动势。",
                    "连续性：保持红发人物、雨夜屋顶和电影化风格一致。",
                    "负面约束：不要水印、文字乱码、畸形肢体、身份漂移或无关角色。",
                ]
            )}

    monkeypatch.setenv("AFS_ALLOW_REMOTE_LLM", "true")
    monkeypatch.setattr("apps.api.runtime_llm_enhancement.load_provider_registry", lambda: FakeRegistry())
    client = TestClient(create_runtime_app(runtime_root=tmp_path))

    result = client.post(
        "/projects/proj_studio_remote_optimizer_retry/prompt-optimizations",
        json={
            "node_id": "image-node-studio-retry",
            "node_type": "image",
            "prompt_text": "Lin Wan stands on a rain rooftop with red long hair, cinematic keyframe.",
            "generation_target": "keyframe",
            "target_platform": "short_video",
            "style": "cinematic",
            "node_parameters": {
                "model": "image2-keyframe",
                "llm_provider": "prompt_optimizer",
                "remote_optimizer_required": True,
            },
            "generated_at": "2026-06-14T03:40:00+08:00",
        },
    )

    assert result.status_code == 200
    payload = result.json()
    assert calls == ["prompt_enhancement", "prompt_enhancement_retry"]
    assert payload["optimized_prompt"].startswith("意图：")
    trace = client.get(f"/artifacts/{payload['artifacts']['prompt_assembly_trace']['artifact_id']}").json()["payload"]
    assert trace["llm_enhancement"]["format_retry_count"] == 1


def test_prompt_optimizer_retry_instruction_is_readable_chinese() -> None:
    from apps.api.runtime_llm_enhancement_instructions import strict_format_retry_instruction

    request = PromptOptimizationRequest(
        node_id="image-node-retry-instruction",
        node_type="image",
        prompt_text="根据参考图生成电影感关键帧",
        generation_target="keyframe",
        target_platform="short_video",
        style="cinematic",
        node_parameters={"model": "image2-keyframe"},
        generated_at="2026-06-21T00:00:00+08:00",
    )

    instruction = strict_format_retry_instruction(request)

    assert "只输出九行" in instruction
    assert "角色/主体" in instruction
    assert "负面约束" in instruction
    assert "????" not in instruction


def test_studio_prompt_optimizer_salvages_repeated_chatty_llm_article(tmp_path, monkeypatch) -> None:
    chatty = "\n".join(
        [
            "下面为您把原始提示词扩展成适合图像生成模型的完整提示词。",
            "## 中文版 Prompt",
            "> **电影感关键帧，逼真的雨夜屋顶场景，林晚站在屋顶边缘，长长的红色长发随风飘动，雨滴在发丝和肩头闪烁，远处城市灯光被雾气模糊，湿润反光表面，低角度拍摄，冷暖对比灯光，体积雾和镜头雨点散射。**",
            "## Negative Prompt",
            "```",
            "low quality, blurry, watermark, deformed hands, extra limbs, bad anatomy",
            "```",
        ]
    )

    class FakeRegistry:
        def dispatch(self, capability, service_id, request):
            assert capability == "llm"
            assert service_id == "prompt_optimizer"
            return {"text": chatty}

    monkeypatch.setenv("AFS_ALLOW_REMOTE_LLM", "true")
    monkeypatch.setattr("apps.api.runtime_llm_enhancement.load_provider_registry", lambda: FakeRegistry())
    client = TestClient(create_runtime_app(runtime_root=tmp_path))

    result = client.post(
        "/projects/proj_studio_remote_optimizer_salvage/prompt-optimizations",
        json={
            "node_id": "image-node-studio-salvage",
            "node_type": "image",
            "prompt_text": "Lin Wan stands on a rain rooftop with red long hair, cinematic keyframe.",
            "generation_target": "keyframe",
            "target_platform": "short_video",
            "style": "cinematic",
            "node_parameters": {
                "model": "image2-keyframe",
                "llm_provider": "prompt_optimizer",
                "remote_optimizer_required": True,
            },
            "generated_at": "2026-06-14T03:50:00+08:00",
        },
    )

    assert result.status_code == 200
    payload = result.json()
    assert payload["optimized_prompt"].startswith("意图：")
    assert "中文版 Prompt" not in payload["optimized_prompt"]
    assert "林晚站在屋顶边缘" in payload["optimized_prompt"]
    trace = client.get(f"/artifacts/{payload['artifacts']['prompt_assembly_trace']['artifact_id']}").json()["payload"]
    assert trace["llm_enhancement"]["format_retry_count"] == 1
    assert trace["llm_enhancement"]["format_salvage_used"] is True


def test_visual_prompt_optimizer_uses_t2i_instruction_without_references(tmp_path, monkeypatch) -> None:
    captured: dict[str, str] = {}

    class FakeRegistry:
        def dispatch(self, capability, service_id, request):
            assert capability == "llm"
            captured["prompt"] = request.prompt
            return {"text": "\n".join(
                [
                    "意图：根据一个来自未来的机器人生成一张完整概念图。",
                    "人物/主体：未来机器人主体清晰，金属结构与发光部件具有辨识度。",
                    "场景/美术：星际屋顶场景宽阔，远处星空和城市轮廓补足空间层次。",
                    "动作/情节：机器人安静站立，像是在观察远处星空。",
                    "镜头/构图：低机位中景，主体居中偏右，保留环境纵深。",
                    "灯光：冷色星光与柔和边缘光突出金属轮廓。",
                    "运动/时间推进：单帧概念图，强调静态瞬间和氛围。",
                    "连续性：保持未来科幻主题，不漂移到其他题材。",
                    "负面约束：不要水印、乱码、畸形肢体或无关角色。",
                ]
            )}

    monkeypatch.setenv("AFS_ALLOW_REMOTE_LLM", "true")
    monkeypatch.setattr("apps.api.runtime_llm_enhancement.load_provider_registry", lambda: FakeRegistry())
    client = TestClient(create_runtime_app(runtime_root=tmp_path))

    result = client.post(
        "/projects/proj_t2i_prompt_mode/prompt-optimizations",
        json={
            "node_id": "image-node-t2i",
            "node_type": "image",
            "prompt_text": "一个来自未来的机器人，在屋顶看星星",
            "generation_target": "keyframe",
            "target_platform": "short_video",
            "style": "cinematic",
            "node_parameters": {
                "model": "image2-keyframe",
                "llm_provider": "prompt_optimizer",
                "remote_optimizer_required": True,
            },
            "generated_at": "2026-06-13T15:00:00+08:00",
        },
    )

    assert result.status_code == 200
    payload = result.json()
    assert payload["optimization_mode"] == "t2i"
    assert "参考图中的同一个人物" not in captured["prompt"]
    assert "不改变参考图" not in captured["prompt"]
    assert "扩写" in captured["prompt"] or "补足" in captured["prompt"]


def test_visual_prompt_optimizer_uses_i2i_instruction_with_references(tmp_path, monkeypatch) -> None:
    captured: dict[str, str] = {}

    class FakeRegistry:
        def dispatch(self, capability, service_id, request):
            assert capability == "llm"
            captured["prompt"] = request.prompt
            return {"text": "\n".join(
                [
                    "意图：只根据用户要求调整参考人物发型。",
                    "人物/主体：保持参考图中的同一人物身份，只将头发改为短发。",
                    "场景/美术：保留参考图原有背景和整体氛围，不新增地点。",
                    "动作/情节：人物保持原有静态姿态，仅呈现发型变化。",
                    "镜头/构图：保持参考图主体关系和构图稳定。",
                    "灯光：不改变参考图的主要光感和明暗关系。",
                    "运动/时间推进：单帧编辑画面，不制造多阶段动作。",
                    "连续性：保持脸部辨识度、服装、体型比例和整体风格。",
                    "负面约束：不要水印、乱码、身份漂移或背景大幅变化。",
                ]
            )}

    monkeypatch.setenv("AFS_ALLOW_REMOTE_LLM", "true")
    monkeypatch.setattr("apps.api.runtime_llm_enhancement.load_provider_registry", lambda: FakeRegistry())
    client = TestClient(create_runtime_app(runtime_root=tmp_path))

    result = client.post(
        "/projects/proj_i2i_prompt_mode/prompt-optimizations",
        json={
            "node_id": "image-node-i2i",
            "node_type": "image",
            "prompt_text": "将这个人物的头发改为短发",
            "generation_target": "keyframe",
            "target_platform": "short_video",
            "style": "cinematic",
            "asset_refs": ["img_reference_001"],
            "node_parameters": {
                "model": "image2-keyframe",
                "llm_provider": "prompt_optimizer",
                "remote_optimizer_required": True,
            },
            "generated_at": "2026-06-13T15:10:00+08:00",
        },
    )

    assert result.status_code == 200
    assert result.json()["optimization_mode"] == "i2i"
    assert "参考图中的同一个人物" in captured["prompt"]
    assert "只改变用户明确要求改变的部分" in captured["prompt"]


def test_video_prompt_optimizer_uses_i2v_instruction_with_first_frame(tmp_path, monkeypatch) -> None:
    captured: dict[str, str] = {}

    class FakeRegistry:
        def dispatch(self, capability, service_id, request):
            assert capability == "llm"
            captured["prompt"] = request.prompt
            return {"text": "\n".join(
                [
                    "意图：基于首帧生成角色在沙漠中行走的视频，保持连续运动。",
                    "人物/主体：保持首帧中的同一人物身份、蓝白校服、发型轮廓和体态比例。",
                    "场景/美术：延续首帧画风与沙漠空间，背景自然变化。",
                    "动作/情节：人物从首帧姿态开始向前行走，步伐自然克制。",
                    "镜头/构图：以首帧构图为起点，轻微跟随主体运动。",
                    "灯光：保持首帧主要光源方向、曝光和色温。",
                    "运动/时间推进：5秒连续视频，动作方向明确，节奏稳定。",
                    "连续性：首帧是强约束，保持身份、服装、体态、场景和画风一致。",
                    "负面约束：不要身份漂移、换脸、换装、静止不动、突兀转场、文字或水印。",
                ]
            )}

    monkeypatch.setenv("AFS_ALLOW_REMOTE_LLM", "true")
    monkeypatch.setattr("apps.api.runtime_llm_enhancement.load_provider_registry", lambda: FakeRegistry())
    request = PromptOptimizationRequest(
        node_id="video-node-i2v",
        node_type="video",
        prompt_text="基于当前关键帧生成视频",
        generation_target="video",
        target_platform="short_video",
        style="cinematic",
        asset_refs=["img_first_frame"],
        node_parameters={
            "llm_provider": "prompt_optimizer",
            "remote_optimizer_required": True,
            "first_frame_image_asset_id": "img_first_frame",
            "motion": "角色在沙漠中行走",
            "connected_reference_nodes": [
                {
                    "title": "角色三视图",
                    "prompt": "意图：生成角色在沙漠中行走的图片，保持服装、发型、体态一致。",
                }
            ],
        },
        generated_at="2026-06-13T15:20:00+08:00",
    )

    payload = maybe_enhance_prompt_with_llm(request, {"selected_slots": {}})
    assert payload["optimization_mode"] == "i2v"
    assert "基于首帧生成视频" in captured["prompt"]
    assert "不要把上游节点标题或完整旧提示词当成角色名字" in captured["prompt"]
    assert "图生图编辑" not in payload["user_prompt"]
    assert "单帧图像编辑" not in payload["user_prompt"]


def test_i2i_prompt_optimizer_guardrail_uses_uploaded_image_filename_hint(tmp_path, monkeypatch) -> None:
    class FakeRegistry:
        def dispatch(self, capability, service_id, request):
            assert capability == "llm"
            return {"text": "\n".join(
                [
                    "意图：围绕“将这个人物的头发改为短发”完成本次生成。",
                    "人物/主体：保留原始提示词中的主体；若写到“这个人物”，必须理解为参考图中的同一个人物。",
                    "场景/美术：保持参考图或原提示中的场景信息；未指定时不要新增具体地点。",
                    "动作/情节：只执行“将这个人物的头发改为短发”这一项变化，不扩写新剧情。",
                    "镜头/构图：关键帧清晰呈现主体变化，构图稳定，主体可辨识。",
                    "灯光：保持自然可读的光线，不改变参考图的主要光感。",
                    "运动/时间推进：单帧关键画面，不制造多阶段动作。",
                    "连续性：保持参考图人物身份、脸部辨识度、服装、体型比例和整体风格；只改变用户明确要求改变的部分。",
                    "负面约束：不要水印、文字乱码、五官畸形、身份漂移、服装漂移、背景大幅变化。",
                ]
            )}

    monkeypatch.setenv("AFS_ALLOW_REMOTE_LLM", "true")
    monkeypatch.setattr("apps.api.runtime_llm_enhancement.load_provider_registry", lambda: FakeRegistry())
    client = TestClient(create_runtime_app(runtime_root=tmp_path))

    result = client.post(
        "/projects/proj_i2i_prompt_guardrail/prompt-optimizations",
        json={
            "node_id": "image-node-i2i",
            "node_type": "image",
            "prompt_text": "将这个人物的头发改为短发",
            "generation_target": "keyframe",
            "target_platform": "short_video",
            "style": "cinematic",
            "asset_refs": ["img_reference_001"],
            "node_parameters": {
                "model": "image2-keyframe",
                "llm_provider": "prompt_optimizer",
                "remote_optimizer_required": True,
                "uploaded_images": [
                    {"asset_id": "img_reference_001", "filename": "校服周彤v1.png", "role": "reference_image"}
                ],
            },
            "generated_at": "2026-06-13T15:10:00+08:00",
        },
    )

    payload = result.json()
    assert result.status_code == 200
    assert payload["provider_calls_started"] is True
    assert payload["safe_manifest"]["llm_enhancement"]["guardrail_fallback_used"] is True
    assert "校服周彤" in payload["optimized_prompt"]
    assert "不要染发变浅" in payload["optimized_prompt"]


def test_script_prompt_optimization_returns_structured_script_plan(tmp_path) -> None:
    client = TestClient(create_runtime_app(runtime_root=tmp_path))
    client.post("/projects", json={"project_id": "proj_script_plan", "goal": "Script plan contract"})

    response = client.post(
        "/projects/proj_script_plan/prompt-optimizations",
        json={
            "node_id": "script-node-plan",
            "node_type": "script",
            "prompt_text": "Expand this idea into a formal short video script, not a shot list.",
            "generation_target": "script",
            "target_platform": "short_video",
            "style": "cinematic",
            "node_parameters": {
                "script_expansion_contract": "formal_script_before_storyboard_breakdown",
                "source_idea": "A future robot watches stars on a rural rooftop.",
                "forbidden_output": "storyboard_placeholder_outline",
            },
            "generated_at": "2026-06-27T10:00:00+08:00",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["script_plan"]["script_type"] == "formal_short_video_script"
    assert payload["script_plan"]["asset_seed_policy"]["candidate_assets_are_editable"] is True
    assert payload["script_plan"]["director_scenario"]["primary_scenario"] == "general_short_video"
    assert "script_plan" in payload["artifacts"]
    assert "storyboard_placeholder_outline" in payload["script_plan"]["forbidden_outputs"]
    assert "storyboard_placeholder_outline" not in payload["user_prompt"]
    assert payload["script_plan"]["script_expansion_strategy"]["section_count_policy"] == "llm_decides_from_idea_density"
    assert payload["script_plan"]["script_expansion_strategy"]["storyboard_split_deferred"] is True

    artifact = client.get(f"/artifacts/{payload['artifacts']['script_plan']['artifact_id']}").json()["payload"]
    assert artifact["detected_subject_hints"] == ["future robot"]
    assert "rooftop platform" in artifact["detected_scene_hints"]
    assert len(artifact["narrative_sections"]) == 3
    assert artifact["narrative_sections"][0]["section_id"] == "premise"


def test_script_generation_predicate_requires_explicit_script_contract_and_script_surface() -> None:
    from apps.api.runtime_script_generation_body import is_script_generation_request

    def request(
        *,
        node_type: str,
        generation_target: str,
        params: dict[str, object],
    ) -> PromptOptimizationRequest:
        return PromptOptimizationRequest(
            node_id="predicate-node",
            node_type=node_type,
            prompt_text="一个人在睡觉",
            generation_target=generation_target,
            target_platform="short_video",
            style="cinematic",
            node_parameters=params,
            generated_at="2026-07-06T10:05:00+08:00",
        )

    assert (
        is_script_generation_request(
            request(
                node_type="image",
                generation_target="keyframe",
                params={"source_idea": "一个人在睡觉"},
            )
        )
        is False
    )
    assert (
        is_script_generation_request(
            request(
                node_type="image",
                generation_target="keyframe",
                params={"source_idea": "一个人在睡觉", "script_generation_mode": "idea_to_script"},
            )
        )
        is False
    )
    assert (
        is_script_generation_request(
            request(
                node_type="script",
                generation_target="script",
                params={"script_generation_mode": "idea_to_script"},
            )
        )
        is True
    )
    assert (
        is_script_generation_request(
            request(
                node_type="script",
                generation_target="script",
                params={"script_expansion_contract": "formal_script_before_storyboard_breakdown"},
            )
        )
        is True
    )
    assert (
        is_script_generation_request(
            request(
                node_type="text",
                generation_target="script",
                params={"script_generation_mode": "idea_to_script"},
            )
        )
        is False
    )
    assert (
        is_script_generation_request(
            request(
                node_type="script",
                generation_target="script",
                params={"source_idea": "一个人在睡觉"},
            )
        )
        is False
    )


def test_script_prompt_generation_requires_remote_llm_when_gate_closed(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("AFS_ALLOW_REMOTE_LLM", raising=False)
    client = TestClient(create_runtime_app(runtime_root=tmp_path))
    client.post("/projects", json={"project_id": "proj_script_body", "goal": "Script body contract"})
    wrapper = "\n".join(
        [
            "请把下面的一句话扩写成正式短视频剧本正文，而不是分镜列表。",
            "输出要求：先给片名，再给连续叙事正文。",
            "原始想法：一个人在睡觉",
        ]
    )

    response = client.post(
        "/projects/proj_script_body/prompt-optimizations",
        json={
            "node_id": "script-node-body",
            "node_type": "script",
            "prompt_text": wrapper,
            "generation_target": "script",
            "target_platform": "short_video",
            "style": "cinematic",
            "node_parameters": {
                "script_expansion_contract": "formal_script_before_storyboard_breakdown",
                "script_generation_mode": "idea_to_script",
                "source_idea": "一个人在睡觉",
                "forbidden_output": "storyboard_placeholder_outline",
                "llm_provider": "prompt_optimizer",
                "remote_optimizer_required": True,
            },
            "generated_at": "2026-07-06T10:00:00+08:00",
        },
    )

    assert response.status_code == 422
    detail = _runtime_error_raw_detail(response)
    assert "remote_llm_gate_closed" in detail
    assert "remote LLM prompt optimization unavailable" in detail


def test_script_prompt_generation_applies_gated_llm_body_with_knowledge_rules(tmp_path, monkeypatch) -> None:
    calls: list[str] = []

    class FakeRegistry:
        def dispatch(self, capability, service_id, request):
            assert capability == "llm"
            assert service_id == "prompt_optimizer"
            calls.append(request.prompt)
            assert request.task_type == "prompt_enhancement"
            assert "专业知识库约束" in request.prompt
            assert "short_video_hook_visual_promise_v1" in request.prompt
            assert "storyboard_shot_numbering_handoff_v1" in request.prompt
            return {
                "text": (
                    "片名：《星光屋顶》\n\n"
                    "遥星R-17站在乡村屋顶的旧水塔旁，夜风吹过金属外壳，远处村庄灯火一点点熄灭。"
                    "它原本只是校准星图，却在一颗异常移动的星点里收到旧时代的童声。"
                    "它低头确认胸口第一次亮起的信号灯，又把手伸向夜空，像是在回答一个多年以前的问题。"
                    "结尾停在它回望屋檐下旧灯泡的瞬间，童声轻轻问它是否还记得回家的路。"
                )
            }

    monkeypatch.setenv("AFS_ALLOW_REMOTE_LLM", "true")
    monkeypatch.setattr("apps.api.runtime_llm_enhancement.load_provider_registry", lambda: FakeRegistry())
    client = TestClient(create_runtime_app(runtime_root=tmp_path))
    client.post("/projects", json={"project_id": "proj_script_llm_body", "goal": "Script body LLM contract"})

    response = client.post(
        "/projects/proj_script_llm_body/prompt-optimizations",
        json={
            "node_id": "script-node-llm-body",
            "node_type": "script",
            "prompt_text": "请把下面的一句话扩写成正式短视频剧本正文。\n原始想法：一个来自未来的机器人，在农村屋顶上看星星",
            "generation_target": "script",
            "target_platform": "short_video",
            "style": "cinematic",
            "node_parameters": {
                "script_expansion_contract": "formal_script_before_storyboard_breakdown",
                "script_generation_mode": "idea_to_script",
                "source_idea": "一个来自未来的机器人，在农村屋顶上看星星",
                "llm_provider": "prompt_optimizer",
                "llm_model": "prompt-optimizer",
                "remote_optimizer_required": True,
            },
            "generated_at": "2026-07-08T10:00:00+08:00",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert calls
    assert payload["provider_calls_started"] is True
    assert payload["safe_manifest"]["llm_enhancement"]["status"] == "applied"
    assert payload["safe_manifest"]["llm_enhancement"]["guardrail_fallback_used"] is False
    assert payload["script_generation_body"]["fallback_used"] is False
    assert payload["optimized_prompt"].startswith("片名：《星光屋顶》")
    assert "意图：" not in payload["optimized_prompt"]
    assert "角色/主体：" not in payload["optimized_prompt"]


def test_script_surface_optimizer_preserves_script_shape_when_llm_returns_prompt_labels(tmp_path, monkeypatch) -> None:
    original_script = "\n".join(
        [
            "镜号：01",
            "时长：1.8",
            "画面描述：@孙悟空 @云栈洞口。低角度仰拍，孙悟空后撤半步，赤色云海压在洞口。",
            "景别：中景",
            "光影氛围：冷灰主调，赤云边缘光。",
            "运镜：轻微推近",
            "对白/旁白：无明确对白",
            "音效：铁链拖地声与洞内回响",
        ]
    )
    captured: list[str] = []

    class FakeRegistry:
        def dispatch(self, capability, service_id, request):
            assert capability == "llm"
            assert service_id == "prompt_optimizer"
            captured.append(request.prompt)
            return {
                "text": "\n".join(
                    [
                        "意图：聚焦孙悟空与猪八戒对峙。",
                        "角色/主体：孙悟空、猪八戒、洞内呼哑重声。",
                        "场景/美术：云栈洞口。",
                        "负面约束：不要水印。",
                    ]
                )
            }

    monkeypatch.setenv("AFS_ALLOW_REMOTE_LLM", "true")
    monkeypatch.setattr("apps.api.runtime_llm_enhancement.load_provider_registry", lambda: FakeRegistry())
    client = TestClient(create_runtime_app(runtime_root=tmp_path))
    client.post("/projects", json={"project_id": "proj_script_surface", "goal": "Script surface optimization"})

    response = client.post(
        "/projects/proj_script_surface/prompt-optimizations",
        json={
            "node_id": "text-script-surface",
            "node_type": "text",
            "prompt_text": original_script,
            "generation_target": "prompt",
            "target_platform": "short_video",
            "style": "cinematic",
            "node_parameters": {
                "llm_provider": "prompt_optimizer",
                "llm_model": "prompt-optimizer",
                "remote_optimizer_required": True,
            },
            "generated_at": "2026-07-08T13:50:00+08:00",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert captured
    assert "优化已有短视频剧本或分镜脚本正文" in captured[0]
    assert "这不是生图提示词优化" in captured[0]
    assert payload["provider_calls_started"] is True
    assert payload["safe_manifest"]["llm_enhancement"]["guardrail_fallback_used"] is True
    assert payload["safe_manifest"]["llm_enhancement"]["discard_reason"] == "optimizer_label_output"
    assert payload["optimized_prompt"] == original_script
    assert payload["user_prompt_sections"][0]["title"] == "剧本/分镜正文"
    assert "意图：" not in payload["optimized_prompt"]
    assert "角色/主体：" not in payload["optimized_prompt"]


def test_script_body_validator_fallbacks_from_wrapper_echo_optimizer_labels_and_template_fillers() -> None:
    from apps.api.runtime_script_generation_body import script_body_from_candidate

    request = PromptOptimizationRequest(
        node_id="script-node-validator",
        node_type="script",
        prompt_text="请把下面的一句话扩写成正式短视频剧本正文。\n原始想法：一个人在睡觉",
        generation_target="script",
        target_platform="short_video",
        style="cinematic",
        node_parameters={
            "script_generation_mode": "idea_to_script",
            "source_idea": "一个人在睡觉",
        },
        generated_at="2026-07-06T10:10:00+08:00",
    )

    wrapper = script_body_from_candidate("请把下面的一句话扩写成正式短视频剧本正文。\n原始想法：一个人在睡觉", request)
    optimizer = script_body_from_candidate(
        "\n".join(
            [
                "意图：围绕一个人在睡觉形成清晰创作方向。",
                "角色/主体：Primary character。",
                "场景/美术：Primary scene。",
                "负面约束：不要水印。",
            ]
        ),
        request,
    )
    template = script_body_from_candidate(
        "片名：《占位》\n\n推进主体出现。\n展示变化。\n收束结果。",
        request,
    )

    assert wrapper["fallback_used"] is True
    assert wrapper["discard_reason"] == "prompt_wrapper_echo"
    assert optimizer["fallback_used"] is True
    assert optimizer["discard_reason"] == "optimizer_label_output"
    assert template["fallback_used"] is True
    assert template["discard_reason"] == "template_filler"
    assert "片名：《" in wrapper["script_body"]
    assert "沈眠" in wrapper["script_body"]
    assert "意图：" not in optimizer["script_body"]
    assert "角色/主体：" not in optimizer["script_body"]


def test_non_script_source_idea_only_request_keeps_remote_optimizer_required(tmp_path, monkeypatch) -> None:
    from apps.api.runtime_script_generation_body import is_script_generation_request

    monkeypatch.delenv("AFS_ALLOW_REMOTE_LLM", raising=False)
    request_payload = {
        "node_id": "image-node-source-idea-only",
        "node_type": "image",
        "prompt_text": "Generate a cinematic keyframe of a person sleeping in a quiet room.",
        "generation_target": "keyframe",
        "target_platform": "short_video",
        "style": "cinematic",
        "node_parameters": {
            "llm_provider": "prompt_optimizer",
            "remote_optimizer_required": True,
            "source_idea": "一个人在睡觉",
        },
        "generated_at": "2026-07-06T10:25:00+08:00",
    }
    predicate_request = PromptOptimizationRequest(**request_payload)

    assert is_script_generation_request(predicate_request) is False

    client = TestClient(create_runtime_app(runtime_root=tmp_path))
    client.post("/projects", json={"project_id": "proj_image_source_idea_only", "goal": "Image optimizer contract"})
    response = client.post(
        "/projects/proj_image_source_idea_only/prompt-optimizations",
        json=request_payload,
    )

    assert response.status_code == 422
    assert "remote_llm_gate_closed" in _runtime_error_raw_detail(response)


def test_prompt_optimizer_trace_includes_professional_reference_for_rooftop_video(tmp_path) -> None:
    client = TestClient(create_runtime_app(runtime_root=tmp_path))
    client.post("/projects", json={"project_id": "proj_prof_ref_prompt", "goal": "Professional reference trace"})

    response = client.post(
        "/projects/proj_prof_ref_prompt/prompt-optimizations",
        json={
            "node_id": "video-node-prof-ref",
            "node_type": "video",
            "prompt_text": "A future robot watches stars on a rural rooftop platform.",
            "generation_target": "video",
            "target_platform": "short_video",
            "style": "cinematic",
            "generated_at": "2026-06-27T10:20:00+08:00",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    trace = client.get(f"/artifacts/{payload['artifacts']['prompt_assembly_trace']['artifact_id']}").json()["payload"]
    reference = trace["professional_reference"]

    assert "Professional reference:" in payload["optimized_prompt"]
    assert "moderate-to-deep" in payload["optimized_prompt"]
    assert {"night", "rooftop", "video"} <= set(reference["tags"])
    assert "motivated night exterior" in reference["lighting"]["decision"]
    assert reference["writes_long_term_memory"] is False
    assert reference["writes_company_kb"] is False


def test_prompt_optimizer_trace_includes_director_scenario_for_saas_launch(tmp_path) -> None:
    client = TestClient(create_runtime_app(runtime_root=tmp_path))
    client.post("/projects", json={"project_id": "proj_director_scenario_prompt", "goal": "Director scenario trace"})

    response = client.post(
        "/projects/proj_director_scenario_prompt/prompt-optimizations",
        json={
            "node_id": "video-node-director-scenario",
            "node_type": "video",
            "prompt_text": "A SaaS launch demo shows a dashboard workflow turning a messy task into a clear result.",
            "generation_target": "video",
            "target_platform": "short_video",
            "style": "clean product demo",
            "generated_at": "2026-06-27T10:35:00+08:00",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    trace = client.get(f"/artifacts/{payload['artifacts']['prompt_assembly_trace']['artifact_id']}").json()["payload"]
    scenario = trace["director_scenario"]

    assert scenario["primary_scenario"] == "saas_launch"
    assert scenario["writes_company_kb"] is False
    assert "Director scenario:" in payload["optimized_prompt"]
    assert "SaaS Launch" in payload["optimized_prompt"]
    model_context = client.get(f"/artifacts/{payload['artifacts']['model_call_context']['artifact_id']}").json()["payload"]
    assert "director_scenario:saas_launch" in model_context["preference_context"]["expert_rule_ids"]
