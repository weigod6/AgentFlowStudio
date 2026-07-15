from __future__ import annotations

from typing import Any

from apps.api.runtime_models import PromptOptimizationRequest, StoryboardBreakdownRequest
from apps.api.runtime_prompt_memory_engine import assemble_prompt_context


def storyboard_llm_request(request: StoryboardBreakdownRequest) -> PromptOptimizationRequest:
    params = dict(request.node_parameters or {})
    params.setdefault("llm_provider", "prompt_optimizer")
    return PromptOptimizationRequest(
        node_id=request.node_id,
        node_type="text",
        prompt_text=request.script_text,
        generation_target="script",
        target_platform=request.target_platform,
        style=request.style,
        node_parameters=params,
        generated_at=request.generated_at,
    )


def storyboard_knowledge_context(request: StoryboardBreakdownRequest) -> dict[str, Any]:
    return assemble_prompt_context(storyboard_llm_request(request), {})


def storyboard_instruction(request: StoryboardBreakdownRequest, storyboard_knowledge: dict[str, Any]) -> str:
    count_line = f"建议镜头数量：{request.shot_count_hint}" if request.shot_count_hint else "根据剧情自动决定镜头数量，避免机械三段切分。"
    return "\n".join(
        [
            "你是影视分镜导演。请把输入剧本拆成专业分镜脚本，输出严格 JSON，不要 Markdown。",
            count_line,
            "专业知识库约束：",
            *_storyboard_knowledge_lines(storyboard_knowledge, limit=8),
            "JSON 格式：{\"shots\":[{shot_id,index,duration,description,shot_size,light_atmosphere,camera_motion,dialogue,sound,source_span,unsupported_additions,asset_refs}]}",
            "显示字段语言约束：description、shot_size、light_atmosphere、camera_motion、dialogue、sound 必须使用中文输出。",
            "不要在显示字段输出英文摄影、光影、声音术语；不要输出 snake_case 或 camelCase。",
            "只有剧本原文已经出现的英文名词、角色名、台词，或 5s、720p、1080p、4K、16:9 等单位、比例、型号类短语可以保留。",
            "shot_size 使用中文景别词，例如：特写、中景、远景、全景、极近特写。",
            "camera_motion 使用中文运镜词，例如：固定机位、手持轻晃、缓慢推近、向上摇镜。",
            "source_span 必须包含 span_id 与 text，text 必须逐字来自剧本原文；不能为镜头效果擅自新增人物、道具、家具、屋檐或场景结构。",
            "unsupported_additions 必须列出所有剧本未提供但你认为需要补入的内容；正常情况下应为空数组，不能静默添加。",
            "description 与 asset_refs 只能使用剧本已经出现或可由 source_span 直接支持的人物、动物、场景、道具和数量关系；不要给未命名角色擅自取名。",
            "如果剧本没有出现某个具体道具、角色名、额外人数/动物数量或场景结构，不要把它写进 description，也不要写进 asset_refs。",
            "asset_refs 每项必须包含 label, asset_type(character|scene), status, source, evidence_text, confidence。默认只识别可复用人物与场景资产；道具不自动建资产，除非用户后续手动新增。",
            "不要用泛化的“主角”“主要场景”替代剧本里的真实名称；例如孙悟空、猪八戒、金刚狼必须分别作为 character；云栈洞口、山巅石台战场等必须作为 scene。",
            "每个镜头要包含时长、画面描述、景别、光影氛围、运镜、对白/旁白、音效。",
            f"平台：{request.target_platform}；风格：{request.style}",
            "剧本：",
            request.script_text,
        ]
    )


def knowledge_rule_ids(storyboard_knowledge: dict[str, Any]) -> list[str]:
    rules = storyboard_knowledge.get("knowledge_rules")
    if not isinstance(rules, list):
        return []
    return [str(rule.get("rule_id") or "") for rule in rules if isinstance(rule, dict) and rule.get("rule_id")][:12]


def _storyboard_knowledge_lines(storyboard_knowledge: dict[str, Any], *, limit: int) -> list[str]:
    rules = storyboard_knowledge.get("knowledge_rules")
    if not isinstance(rules, list):
        return ["- 使用分镜、短视频节奏、导演意图、镜头连续性和负面约束规则，但不要回显规则文本。"]
    preferred_domains = {"storyboard", "short_video_script", "directing", "cinematography", "keyframe_continuity"}
    lines: list[str] = []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        if str(rule.get("domain") or "") not in preferred_domains:
            continue
        transform = rule.get("prompt_transform")
        guidance = str(transform.get("guidance") or "").strip() if isinstance(transform, dict) else ""
        rule_id = str(rule.get("rule_id") or "").strip()
        if not guidance or not rule_id:
            continue
        lines.append(f"- {rule_id}: {guidance}")
        if len(lines) >= limit:
            break
    return lines or ["- 使用分镜、短视频节奏、导演意图、镜头连续性和负面约束规则，但不要回显规则文本。"]


__all__ = (
    "knowledge_rule_ids",
    "storyboard_instruction",
    "storyboard_knowledge_context",
    "storyboard_llm_request",
)
