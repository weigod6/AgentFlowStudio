from __future__ import annotations

import json
from typing import Any


def generation_prompt(
    *,
    script_text: str,
    target_platform: str,
    style: str,
    shot_count_hint: int | None,
    knowledge_lines: list[str],
) -> str:
    count_rule = (
        f"目标镜头数为 {shot_count_hint}，仅在剧情原子动作无法合理承载时小幅调整。"
        if shot_count_hint
        else "按剧情动作、信息揭示和场景转换决定镜头数，不按段落机械等分。"
    )
    return "\n".join(
        [
            "你是影视分镜生成器。只输出一个 JSON 对象，不要 Markdown、解释或前后缀。",
            count_rule,
            f"平台：{target_platform}；风格：{style}。",
            "知识约束：",
            *(knowledge_lines[:8] or ["- 保持镜头原子性、叙事顺序、视觉连续性和来源可追溯。"]),
            "必须先理解完整剧本，再按叙事顺序拆分。每镜只承载一个可拍摄的主要动作或信息揭示。",
            "禁止把片名、章节名、提示词说明复制进画面描述；禁止重复相同正文、相同来源片段或同义镜头来凑数量。",
            "description、shot_size、light_atmosphere、camera_motion、dialogue、sound 使用中文；原文中的英文姓名、型号或台词可保留。",
            "所有显示字段都必须是非空字符串。没有对白时 dialogue 必须写“无明确对白”；没有可确认音效时 sound 必须写“无明确音效”，不能使用空字符串或 null。",
            "资产定义：character 包含人类、动物、机器人及其他持续行动主体；scene 是镜头实际发生且可复用的空间环境；prop 仅包含被持有、操作、传递、特写、推动剧情或需要跨镜连续的物件。",
            "不要把动作短语、代词指代之外的身体部位、情绪、气味、声音、光线、普通背景装饰或片名标成资产。",
            "每镜必须列出本镜实际出现或被直接操作的全部资产提及。不得因为资产在其他镜头出现而跨镜复制。",
            "asset_mentions.label 必须是 evidence.quote 中逐字出现的本镜表面称呼；遇到‘他/她/它/那把剑’等指代也保留表面称呼，后续实体解析器负责归并，不要擅自改名。",
            "source_evidence 和每个资产 evidence 都必须逐字复制剧本连续原文。start/end 是 Python 字符索引，end 为开区间；不确定偏移时仍需给出 quote，系统只会在 quote 唯一出现时校正偏移。",
            "unsupported_additions 列出无法从剧本得到、但画面描述中新增的内容；正常应为空数组。",
            "严格输出以下结构：",
            '{"shots":[{"shot_id":"shot_01","index":1,"duration":"3s","description":"...","shot_size":"...","light_atmosphere":"...","camera_motion":"...","dialogue":"...","sound":"...","source_evidence":[{"quote":"剧本逐字片段","start":0,"end":6}],"asset_mentions":[{"asset_type":"character|scene|prop","label":"证据中的表面称呼","evidence":{"quote":"剧本逐字片段","start":0,"end":6},"relevance":"为什么是可复用视觉资产或连续性道具"}],"unsupported_additions":[]}]}',
            "剧本正文：",
            script_text,
        ]
    )


def shot_verification_prompt(*, script_text: str, shot: dict[str, Any]) -> str:
    return "\n".join(
        [
            "你是独立分镜审校器。只输出一个 JSON 对象，不要 Markdown。",
            "逐项核查当前镜头，不得默认生成器正确：",
            "1. source_evidence 是否逐字来自剧本且对应当前叙事位置；画面是否是单一可拍摄镜头，是否与其他内容无关或夹带片名。",
            "2. character/scene/prop 是否按语义分类；动物属于 character；剧情或连续性关键物件属于 prop。",
            "3. 是否漏掉本镜实际出现或被直接操作的角色、动物、场景或连续性道具；是否误把动作、身体部位、感官描述、声音、光影或普通背景装饰当资产。",
            "4. label 必须保留剧本证据中的表面称呼。不要在本阶段把代词、别名或描述性称呼改成你猜测的实体名。",
            "4.1 机械合同：每个 asset_mentions.label 必须作为连续子串逐字出现在对应 evidence.quote 中。只要一个标签不满足，就不得返回 accepted；能依据原文修正时返回 corrected 和完整 corrected_shot，否则返回 rejected 或 requires_review。",
            "5. 画面描述、景别、光影、运镜、对白、音效是否相互一致，且没有无证据新增。",
            "状态规则：完全正确用 accepted；可在不猜测实体的前提下修正用 corrected；存在事实错误用 rejected；存在无法由文本确定的指代或分类歧义用 requires_review。",
            "accepted 不输出 corrected_shot。corrected 必须输出完整 corrected_shot，结构与输入镜头相同。",
            '输出结构：{"shot_id":"...","status":"accepted|corrected|rejected|requires_review","reason_codes":["..."],"corrected_shot":{...}}',
            "完整剧本：",
            script_text,
            "待核查镜头：",
            json.dumps(shot, ensure_ascii=False, separators=(",", ":")),
        ]
    )


def entity_resolution_prompt(*, script_text: str, shots: list[dict[str, Any]]) -> str:
    mention_payload = [
        {
            "shot_id": shot["shot_id"],
            "mention_id": mention["mention_id"],
            "asset_type": mention["asset_type"],
            "label": mention["label"],
            "evidence": mention["evidence"],
        }
        for shot in shots
        for mention in shot.get("asset_mentions", [])
    ]
    return "\n".join(
        [
            "你是全局实体解析与连续性审校器。只输出一个 JSON 对象，不要 Markdown。",
            "输入中的 mention 已通过逐镜审校。你的任务只是在完整剧本范围内判断哪些 mention 指向同一视觉实体。",
            "必须处理人名、英文名、昵称、别名、代词、描述性称呼和跨镜指代；不得依靠固定词表。",
            "同物种、同职业或同名不等于同一实体；证据不足时放入 unresolved_mentions，禁止强行合并。",
            "不得新增 mention，不得改变 mention 的 asset_type，不得把 character、scene、prop 混合归并。",
            "canonical_label 必须从该实体 mention 的 label 中选择最明确的一个，不能创造新名称。",
            "character_subtype 仅 character 使用，值为 human、animal、robot、other；scene/prop 使用空字符串。",
            "grounded_facts 只写能从引用证据直接推出、且对外观或连续性有用的事实。每条 fact 必须携带剧本逐字 evidence；无法确认就不写。",
            "grounded_facts.field 使用语义字段，不使用具体名词映射。角色可用 identity/species/appearance/color/marking/body/clothing/state/relationship；场景可用 location/layout/element/lighting/palette/time_weather；道具可用 category/appearance/color/marking/material/scale/usage/interaction/state/relationship。",
            "每个 mention_id 必须且只能出现在一个 entity 或一个 unresolved_mentions 项中。",
            '输出结构：{"entities":[{"asset_type":"character|scene|prop","character_subtype":"human|animal|robot|other|","canonical_label":"某个输入 label","mention_ids":["mention:..."],"grounded_facts":[{"field":"species|appearance|layout|material|usage|...","fact":"...","evidence":{"quote":"剧本逐字片段","start":0,"end":6}}]}],"unresolved_mentions":[{"mention_ids":["mention:..."],"reason_code":"ambiguous_coreference|same_name_collision|insufficient_evidence"}]}',
            "完整剧本：",
            script_text,
            "已核查资产提及：",
            json.dumps(mention_payload, ensure_ascii=False, separators=(",", ":")),
        ]
    )


__all__ = ("entity_resolution_prompt", "generation_prompt", "shot_verification_prompt")
