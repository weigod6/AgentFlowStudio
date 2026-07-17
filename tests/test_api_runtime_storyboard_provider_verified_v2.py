from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from agentflow.algorithms.asset_auto_binding import build_asset_auto_binding_graph
from agentflow_studio.model_gateway.errors import ModelGatewayError
from apps.api.runtime_asset_graph import build_asset_graph
from apps.api.runtime_models import StoryboardBreakdownRequest
from apps.api.runtime_service import create_runtime_app
from apps.api.runtime_storyboard_contract_v2 import validated_generation, validated_resolution
from apps.api.runtime_storyboard_generation_v2 import ProviderCallSession
from apps.api.runtime_storyboard_knowledge import storyboard_llm_request


PIPELINE_PARAMS = {"llm_provider": "prompt_optimizer", "storyboard_pipeline": "provider_verified_v2"}


class Descriptor:
    modality = "llm"


class SequenceRegistry:
    _descriptors = {"prompt_optimizer": Descriptor()}

    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses
        self.calls: list[Any] = []

    def dispatch(self, capability: str, service_id: str, request: Any) -> dict[str, Any]:
        assert capability == "llm"
        assert service_id == "prompt_optimizer"
        self.calls.append(request)
        response = self.responses.get(str(request.task_type or ""))
        if isinstance(response, Exception):
            raise response
        if callable(response):
            response = response(request)
        if response is None:
            raise AssertionError(f"unexpected provider stage: {request.task_type}")
        return {"text": response if isinstance(response, str) else json.dumps(response, ensure_ascii=False)}


def test_v2_gate_closed_fails_without_semantic_fallback(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("AFS_ALLOW_REMOTE_LLM", raising=False)
    client, project_id = _client(tmp_path, "v2_gate_closed")

    response = _breakdown(client, project_id, "黑屏中响起一声钟鸣。")

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["error"] == "provider_gate_closed"
    assert detail["details"]["pipeline"] == "provider_verified_v2"
    assert "shots" not in response.json()


def test_v2_generates_verifies_and_resolves_authoritative_assets(tmp_path, monkeypatch) -> None:
    script = "小华站在公园长椅旁。Alice牵着一只黑色拉布拉多走来。拉布拉多叼着荧光绿网球冲向小华。"
    generation = {
        "shots": [
            _shot(
                script,
                1,
                "小华站在公园长椅旁。",
                "小华站在公园长椅旁，神情低落。",
                [("character", "小华"), ("scene", "公园长椅")],
            ),
            _shot(
                script,
                2,
                "Alice牵着一只黑色拉布拉多走来。",
                "Alice牵着黑色拉布拉多进入画面。",
                [("character", "Alice"), ("character", "黑色拉布拉多")],
            ),
            _shot(
                script,
                3,
                "拉布拉多叼着荧光绿网球冲向小华。",
                "拉布拉多叼着荧光绿网球冲向小华。",
                [("character", "拉布拉多"), ("prop", "荧光绿网球"), ("character", "小华")],
            ),
        ]
    }
    normalized = validated_generation(generation, script)
    mentions = {shot["index"]: shot["asset_mentions"] for shot in normalized}
    resolver = {
        "entities": [
            _entity(script, "character", "human", "小华", [mentions[1][0], mentions[3][2]], "identity", "小华"),
            _entity(script, "scene", "", "公园长椅", [mentions[1][1]], "location", "公园长椅"),
            _entity(script, "character", "human", "Alice", [mentions[2][0]], "identity", "Alice"),
            _entity(
                script,
                "character",
                "animal",
                "黑色拉布拉多",
                [mentions[2][1], mentions[3][0]],
                "color",
                "黑色拉布拉多",
            ),
            _entity(script, "prop", "", "荧光绿网球", [mentions[3][1]], "appearance", "荧光绿网球"),
        ],
        "unresolved_mentions": [],
    }
    registry = SequenceRegistry(
        {
            "storyboard_generation": generation,
            "storyboard_verify_01": _accepted("shot_01"),
            "storyboard_verify_02": _accepted("shot_02"),
            "storyboard_verify_03": _accepted("shot_03"),
            "storyboard_entity_resolution": resolver,
        }
    )
    client, project_id = _v2_client(tmp_path, monkeypatch, registry, "v2_happy")

    response = _breakdown(client, project_id, script)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["pipeline"] == "provider_verified_v2"
    assert payload["safe_manifest"]["status"] == "provider_verified_v2"
    assert payload["fallback_visible_to_user"] is False
    assert payload["verification_summary"]["semantic_fallback_used"] is False
    assert payload["verification_summary"]["provider_call_summary"]["call_count"] == 5
    assert payload["verification_summary"]["provider_call_summary"]["max_concurrency"] == 1
    third_refs = {item["label"]: item for item in payload["shots"][2]["asset_refs"]}
    assert set(third_refs) == {"黑色拉布拉多", "荧光绿网球", "小华"}
    assert third_refs["黑色拉布拉多"]["character_subtype"] == "animal"
    assert third_refs["荧光绿网球"]["asset_type"] == "prop"
    assert all(item["verification_status"] == "verified" for item in third_refs.values())
    assert all(shot["asset_refs_authoritative"] is True for shot in payload["shots"])
    graph_assets = payload["asset_graph"]["assets"]
    assert len(graph_assets) == 5
    assert len({item["entity_id"] for item in graph_assets}) == 5
    assert all(item["graph_asset_id"].startswith("graph:entity:") for item in graph_assets)


def test_v2_empty_authoritative_assets_remain_empty(tmp_path, monkeypatch) -> None:
    script = "黑屏持续三秒，只听见一声钟鸣。"
    generation = {"shots": [_shot(script, 1, script, "黑屏持续三秒。", [])]}
    registry = SequenceRegistry(
        {
            "storyboard_generation": generation,
            "storyboard_verify_01": _accepted("shot_01"),
        }
    )
    client, project_id = _v2_client(tmp_path, monkeypatch, registry, "v2_empty_assets")

    response = _breakdown(client, project_id, script)

    assert response.status_code == 200, response.text
    shot = response.json()["shots"][0]
    assert shot["asset_refs"] == []
    assert shot["asset_refs_authoritative"] is True
    assert response.json()["asset_graph"]["asset_count"] == 0
    assert len(registry.calls) == 2


def test_v2_verifier_correction_replaces_generator_asset_errors(tmp_path, monkeypatch) -> None:
    script = "她伸出手指，把旧钥匙放在桌面。"
    generated = _shot(
        script,
        1,
        script,
        "她伸出手指，把旧钥匙放在桌面。",
        [("prop", "手指")],
    )
    corrected = _shot(script, 1, script, "她把旧钥匙放在桌面。", [("character", "她"), ("prop", "旧钥匙")])
    normalized = validated_generation({"shots": [corrected]}, script)[0]
    resolver = {
        "entities": [
            _entity(script, "character", "human", "她", [normalized["asset_mentions"][0]], "identity", "她"),
            _entity(script, "prop", "", "旧钥匙", [normalized["asset_mentions"][1]], "usage", "旧钥匙"),
        ],
        "unresolved_mentions": [],
    }
    registry = SequenceRegistry(
        {
            "storyboard_generation": {"shots": [generated]},
            "storyboard_verify_01": {
                "shot_id": "shot_01",
                "status": "corrected",
                "reason_codes": ["body_part_is_not_asset", "missing_continuity_prop"],
                "corrected_shot": corrected,
            },
            "storyboard_entity_resolution": resolver,
        }
    )
    client, project_id = _v2_client(tmp_path, monkeypatch, registry, "v2_corrected")

    response = _breakdown(client, project_id, script)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["verification_summary"]["corrected_count"] == 1
    assert {item["label"] for item in payload["shots"][0]["asset_refs"]} == {"她", "旧钥匙"}
    assert "手指" not in {item["label"] for item in payload["asset_graph"]["assets"]}


def test_v2_invalid_output_provider_failure_and_manual_review_fail_closed(tmp_path, monkeypatch) -> None:
    cases = [
        ("invalid", {"storyboard_generation": "not json"}, 422, "provider_output_invalid"),
        (
            "partial",
            {"storyboard_generation": {"shots": [{"shot_id": "shot_01", "index": 1, "duration": "3s"}]}},
            422,
            "provider_output_invalid",
        ),
        (
            "timeout",
            {"storyboard_generation": ModelGatewayError("provider timeout")},
            503,
            "provider_unavailable",
        ),
    ]
    for suffix, responses, status_code, error_code in cases:
        registry = SequenceRegistry(responses)
        client, project_id = _v2_client(tmp_path / suffix, monkeypatch, registry, f"v2_{suffix}")
        response = _breakdown(client, project_id, "一名旅人走进车站。")
        assert response.status_code == status_code
        assert response.json()["detail"]["error"] == error_code
        assert "shots" not in response.json()

    script = "一名旅人走进车站。"
    generation = {"shots": [_shot(script, 1, script, script, [("character", "旅人"), ("scene", "车站")])]}
    registry = SequenceRegistry(
        {
            "storyboard_generation": generation,
            "storyboard_verify_01": {
                "shot_id": "shot_01",
                "status": "requires_review",
                "reason_codes": ["ambiguous_identity"],
            },
        }
    )
    client, project_id = _v2_client(tmp_path / "review", monkeypatch, registry, "v2_review")
    response = _breakdown(client, project_id, script)
    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "manual_review_required"


def test_v2_global_ambiguity_and_call_budget_fail_closed(tmp_path, monkeypatch) -> None:
    script = "两只黑犬从左右两侧跑来。它停在门边。"
    generation = {
        "shots": [
            _shot(script, 1, "两只黑犬从左右两侧跑来。", "两只黑犬从左右两侧跑来。", [("character", "黑犬")]),
            _shot(script, 2, "它停在门边。", "它停在门边。", [("character", "它"), ("scene", "门边")]),
        ]
    }
    normalized = validated_generation(generation, script)
    mention_ids = [item["mention_id"] for shot in normalized for item in shot["asset_mentions"]]
    registry = SequenceRegistry(
        {
            "storyboard_generation": generation,
            "storyboard_verify_01": _accepted("shot_01"),
            "storyboard_verify_02": _accepted("shot_02"),
            "storyboard_entity_resolution": {
                "entities": [],
                "unresolved_mentions": [{"mention_ids": mention_ids, "reason_code": "ambiguous_coreference"}],
            },
        }
    )
    client, project_id = _v2_client(tmp_path / "ambiguous", monkeypatch, registry, "v2_ambiguous")
    response = _breakdown(client, project_id, script)
    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "manual_review_required"

    budget_script = "甲抬头。乙转身。丙离开。"
    budget_generation = {
        "shots": [
            _shot(budget_script, 1, "甲抬头。", "甲抬头。", []),
            _shot(budget_script, 2, "乙转身。", "乙转身。", []),
            _shot(budget_script, 3, "丙离开。", "丙离开。", []),
        ]
    }
    registry = SequenceRegistry({"storyboard_generation": budget_generation})
    client, project_id = _v2_client(tmp_path / "budget", monkeypatch, registry, "v2_budget")
    response = _breakdown(client, project_id, budget_script, params={**PIPELINE_PARAMS, "storyboard_v2_max_calls": 4})
    assert response.status_code == 422
    assert response.json()["detail"]["details"]["pipeline_state"] == "provider_output_invalid"
    assert len(registry.calls) == 1


def test_entity_resolution_keeps_same_name_distinct_and_groups_cross_language_aliases() -> None:
    script = "Alice抱起小狗。她看向另一只小狗。"
    generation = {
        "shots": [
            _shot(script, 1, "Alice抱起小狗。", "Alice抱起小狗。", [("character", "Alice"), ("character", "小狗")]),
            _shot(script, 2, "她看向另一只小狗。", "她看向另一只小狗。", [("character", "她"), ("character", "小狗")]),
        ]
    }
    shots = validated_generation(generation, script)
    first, second = shots[0]["asset_mentions"], shots[1]["asset_mentions"]
    payload = {
        "entities": [
            _entity(script, "character", "human", "Alice", [first[0], second[0]], "identity", "Alice"),
            _entity(script, "character", "animal", "小狗", [first[1]], "species", "小狗"),
            _entity(script, "character", "animal", "小狗", [second[1]], "species", "小狗", occurrence=2),
        ],
        "unresolved_mentions": [],
    }

    entities, unresolved = validated_resolution(payload, shots, script)
    repeated_entities, _ = validated_resolution(payload, shots, script)

    assert unresolved == []
    assert len(entities) == 3
    assert set(entities[0]["aliases"]) == {"Alice", "她"}
    dog_ids = [item["entity_id"] for item in entities if item["canonical_label"] == "小狗"]
    assert len(dog_ids) == 2 and dog_ids[0] != dog_ids[1]
    assert [item["entity_id"] for item in repeated_entities] == [item["entity_id"] for item in entities]


def test_provider_call_session_caches_identical_calls_and_reports_serial_bound(tmp_path) -> None:
    registry = SequenceRegistry({"storyboard_generation": {"ok": True}})
    request = StoryboardBreakdownRequest(
        script_text="测试文本",
        node_parameters=PIPELINE_PARAMS,
        generated_at="2026-07-17T10:00:00+08:00",
    )
    session = ProviderCallSession(registry, storyboard_llm_request(request), Path(tmp_path), max_calls=4)

    first = session.call("same prompt", stage="generation", timeout_sec=1.0)
    second = session.call("same prompt", stage="generation", timeout_sec=1.0)

    assert first == second
    assert len(registry.calls) == 1
    assert session.summary()["cache_hits"] == 1
    assert session.summary()["max_concurrency"] == 1


def test_shot_asset_plan_treats_verified_refs_including_empty_as_authoritative(tmp_path) -> None:
    client, project_id = _client(tmp_path, "v2_asset_plan_authority")
    base_shot = {
        "shot_id": "shot_01",
        "index": 1,
        "description": "小华站在屋顶，手中握着钥匙。",
        "source_text": "小华站在屋顶，手中握着钥匙。",
        "asset_refs_authoritative": True,
        "asset_ref_authority": "runtime_provider_verified_v2",
    }

    empty_response = client.post(
        f"/projects/{project_id}/shot-asset-plans",
        json={
            "node_id": "shot_01",
            "shot": {**base_shot, "asset_refs": []},
            "script_text": "小华站在屋顶，手中握着钥匙。",
            "generated_at": "2026-07-17T10:00:00+08:00",
        },
    )
    assert empty_response.status_code == 200
    assert empty_response.json()["asset_refs"] == []
    assert empty_response.json()["safe_manifest"]["asset_refs_authoritative"] is True

    verified_prop = {
        "entity_id": "entity:verified-key",
        "label": "钥匙",
        "asset_type": "prop",
        "status": "verified_candidate",
        "source": "provider_verified_v2",
        "verification_status": "verified",
        "evidence_text": "手中握着钥匙",
        "grounded_facts": [],
    }
    prop_response = client.post(
        f"/projects/{project_id}/shot-asset-plans",
        json={
            "node_id": "shot_01",
            "shot": {**base_shot, "asset_refs": [verified_prop]},
            "script_text": "小华站在屋顶，手中握着钥匙。",
            "generated_at": "2026-07-17T10:00:00+08:00",
        },
    )
    assert prop_response.status_code == 200
    assert [(item["label"], item["asset_type"]) for item in prop_response.json()["asset_refs"]] == [("钥匙", "prop")]
    assert prop_response.json()["asset_refs"][0]["entity_id"] == "entity:verified-key"


def test_v2_implementation_has_no_local_semantic_fallback_or_example_name_rules() -> None:
    api_root = Path("apps/api")
    v2_sources = "\n".join(
        (api_root / name).read_text(encoding="utf-8")
        for name in (
            "runtime_storyboard_contract_v2.py",
            "runtime_storyboard_generation_v2.py",
            "runtime_storyboard_json_v2.py",
            "runtime_storyboard_entity_resolver.py",
            "runtime_storyboard_verification_prompt.py",
        )
    )

    assert "runtime_storyboard_local" not in v2_sources
    assert "include_inferred" not in v2_sources
    assert "local_storyboard_shots" not in v2_sources
    for example_name in ("小明", "小华", "橘猫", "煤球", "沈砚", "拉布拉多", "断戟", "青铜虎符"):
        assert example_name not in v2_sources


def test_same_label_distinct_entities_are_not_auto_bound_to_one_fixed_asset() -> None:
    refs = [
        {
            "entity_id": entity_id,
            "label": "小狗",
            "asset_type": "character",
            "status": "verified_candidate",
            "source": "provider_verified_v2",
            "confidence": 0.95,
            "evidence_text": evidence,
            "modality_gate_status": "accepted",
        }
        for entity_id, evidence in (("entity:first", "第一只小狗"), ("entity:second", "第二只小狗"))
    ]
    graph = build_asset_graph(
        [
            {
                "shot_id": "shot_01",
                "source_span": {"span_id": "span_01", "text": "第一只小狗和第二只小狗"},
                "asset_refs": refs,
            }
        ],
        source_text="第一只小狗和第二只小狗",
        graph_source="storyboard_provider_verified_v2",
    )
    binding = build_asset_auto_binding_graph(
        project_id="v2_collision",
        asset_graph=graph,
        fixed_visual_assets=[
            {
                "asset_id": "fixed-dog",
                "asset_type": "character",
                "label": "小狗",
                "status": "fixed",
                "source_evidence": {"source_contract": "fixed_asset_promotion_gate"},
            }
        ],
    )

    assert graph["asset_count"] == 2
    assert all(item["identity_collision"] is True for item in graph["assets"])
    assert binding["summary"]["established_binding_count"] == 0
    assert binding["summary"]["blocked_candidate_count"] == 2
    assert all(
        "same_label_distinct_entities" in item["block_reasons"] for item in binding["blocked_candidates"]
    )


def _client(tmp_path: Path, project_id: str) -> tuple[TestClient, str]:
    client = TestClient(create_runtime_app(runtime_root=tmp_path))
    response = client.post(
        "/projects",
        json={"project_id": project_id, "project_type": "short_video_campaign", "goal": "Test storyboard v2."},
    )
    assert response.status_code in {200, 201}
    return client, project_id


def _v2_client(tmp_path: Path, monkeypatch, registry: SequenceRegistry, project_id: str) -> tuple[TestClient, str]:
    monkeypatch.setenv("AFS_ALLOW_REMOTE_LLM", "true")
    monkeypatch.setattr("apps.api.runtime_storyboard_generation_v2.load_provider_registry", lambda: registry)
    return _client(tmp_path, project_id)


def _breakdown(
    client: TestClient,
    project_id: str,
    script: str,
    *,
    params: dict[str, Any] | None = None,
):
    return client.post(
        f"/projects/{project_id}/storyboard-breakdowns",
        json={
            "node_id": "text_001",
            "script_text": script,
            "target_platform": "short_video",
            "style": "cinematic",
            "node_parameters": params or PIPELINE_PARAMS,
            "generated_at": "2026-07-17T10:00:00+08:00",
        },
    )


def _shot(
    script: str,
    index: int,
    quote: str,
    description: str,
    mentions: list[tuple[str, str]],
) -> dict[str, Any]:
    evidence = _evidence(script, quote)
    return {
        "shot_id": f"shot_{index:02d}",
        "index": index,
        "duration": "3s",
        "description": description,
        "shot_size": "中景",
        "light_atmosphere": "自然光，主体轮廓清晰",
        "camera_motion": "缓慢推近",
        "dialogue": "无明确对白",
        "sound": "环境声随动作同步",
        "source_evidence": [evidence],
        "asset_mentions": [
            {
                "asset_type": asset_type,
                "label": label,
                "evidence": evidence,
                "relevance": "本镜可复用视觉实体",
            }
            for asset_type, label in mentions
        ],
        "unsupported_additions": [],
    }


def _accepted(shot_id: str) -> dict[str, Any]:
    return {"shot_id": shot_id, "status": "accepted", "reason_codes": []}


def _entity(
    script: str,
    asset_type: str,
    subtype: str,
    label: str,
    mentions: list[dict[str, Any]],
    field: str,
    evidence_quote: str,
    *,
    occurrence: int = 1,
) -> dict[str, Any]:
    return {
        "asset_type": asset_type,
        "character_subtype": subtype,
        "canonical_label": label,
        "mention_ids": [item["mention_id"] for item in mentions],
        "grounded_facts": [
            {
                "field": field,
                "fact": f"剧本明确为{label}",
                "evidence": _evidence(script, evidence_quote, occurrence=occurrence),
            }
        ],
    }


def _evidence(script: str, quote: str, *, occurrence: int = 1) -> dict[str, Any]:
    start = -1
    cursor = 0
    for _ in range(occurrence):
        start = script.find(quote, cursor)
        if start < 0:
            raise AssertionError(f"quote not found: {quote}")
        cursor = start + 1
    return {"quote": quote, "start": start, "end": start + len(quote)}
