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
            "asset_refs 每项必须包含 label, asset_type(character|scene|prop), status, source, evidence_text, confidence；当 asset_type=character 时必须包含 character_subtype(human|animal|robot|subject)。",
            "每个镜头的 asset_refs 必须覆盖该镜头画面中可复用的主要角色/动物主体、明确地点或空间场景，以及会影响连续性的关键道具；不要只列角色而漏掉正在发生动作的场景或关键物件。",
            "资产覆盖审计：生成每个镜头前，先在内部检查 source_span.text 中是否存在 1) 做动作或被镜头聚焦的人/动物主体；2) 当前可见地点、空间、环境或延续场景；3) 被握住、叼着、放下、拾起、展示、读写、反射、震颤或影响后续连续性的物件。上述三类只要有明确文本证据，就必须进入 asset_refs。",
            "资产类型判定：人类、动物、机器人等能行动或被塑造成主体的对象写 character；地点、空间、环境写 scene；武器、纸张、球、绳、手机、地图、竹简、试卷、伞等可被持有/移动/阅读/展示的物体写 prop，不得写成 character 或 scene。",
            "回指判定：如果本镜头写“他/她/它/那狗/那猫/球/戟/试卷”等回指词，且前文已唯一建立对应资产，本镜头 asset_refs 必须使用前文真实 label；如果候选不唯一，不要强行合并，保留可人工复核的具体可见资产。",
            "逐镜资产约束：asset_refs 只能来自该镜头 source_span.text 或 description 的当前画面；后文镜头才出现的虎符、竹简、军籍册、启事等不得提前写入前面镜头。",
            "主体优先约束：如果 source_span.text 中出现“某人 + 动作/身体状态”，例如“沈砚单膝陷在泥中”“小华蹲在长椅旁”，该人物必须作为 character 写入本镜头 asset_refs。",
            "资产命名约束：asset_refs.label 必须是可复用实体名，不能是动作短语、身体局部、代词短语或谓语片段；例如不要输出“它挣脱怀”“转身轻巧”“右眼”“他掏出手机”，应分别回指真实角色/动物，或在当前镜头确有物件时输出“手机”等具体 prop。",
            "局部画面约束：每个镜头只列当前画面可见或明确被动作作用的资产；前后镜头连续出现的角色/场景可以复用同一真实 label，但不能把整篇剧本里其他段落才出现的道具提前复制到本镜头。",
            "证据约束：每个 asset_ref.evidence_text 必须是 source_span.text 中能直接支撑该资产的原文片段；不要使用“最近一片”“某个道具”“场景1”这类原文没有的转述。",
            "猫、狗、犬、鸟、马、龙、拉布拉多、柴犬、奶狗等动物主体必须标为 asset_type=character 且 character_subtype=animal；人类角色必须标为 human。",
            "同一动物或角色后续被写成“狗/猫/它/他/她/那只狗”等代词时，必须回指到前文已建立的真实 label，例如“黑色拉布拉多”，不要新建泛化资产，也不要漏掉它。",
            "道具只能使用 asset_type=prop；例如网球、绳子、手机、地图、武器不能写成 character。默认不把普通道具晋升为角色或场景资产；只有对后续画面连续性必须固定的关键道具才作为 prop 引用。",
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
