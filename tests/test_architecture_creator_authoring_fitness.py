from __future__ import annotations

import ast
from pathlib import Path

from apps.api.runtime_service import create_runtime_app


ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "apps" / "api"
AUTHORING_MODULES = (
    API / "runtime_episode_authoring_service.py",
    API / "runtime_episode_command_routes.py",
    API / "runtime_episode_workspace_projection.py",
    API / "runtime_episode_workspace_routes.py",
    API / "runtime_studio_state_creator_authoring.py",
)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return imports


def test_creator_canonical_write_path_does_not_import_forbidden_truth_owners() -> None:
    forbidden = (
        "runtime_commercial_production",
        "runtime_production_runs",
        "runtime_product_read_models",
        "runtime_domain_crew",
        "runtime_creator_golden_trial",
        "runtime_production_control",
    )
    for path in AUTHORING_MODULES:
        imported = _imports(path)
        assert not any(any(part in module for part in forbidden) for module in imported), path


def test_creator_modules_do_not_depend_on_provider_or_route_private_modules() -> None:
    for path in AUTHORING_MODULES:
        imported = _imports(path)
        assert not any("provider" in module or "gateway" in module for module in imported), path
        if not path.stem.endswith("_routes"):
            assert not any(module.endswith("_routes") for module in imported), path


def test_openapi_exposes_typed_creator_command_union_and_read_only_preview_routes(tmp_path: Path) -> None:
    schema = create_runtime_app(runtime_root=tmp_path).openapi()
    paths = schema["paths"]

    assert "/projects/{project_id}/creator-workspace" in paths
    assert "/projects/{project_id}/episode-production-aggregate/shot-impact-preview" in paths
    assert "/projects/{project_id}/episode-production-aggregate/shot-restore-preview" in paths
    assert "/projects/{project_id}/episode-production-aggregate/shot-version-diff" in paths
    command_operation = paths[
        "/projects/{project_id}/episode-production-aggregate/commands"
    ]["post"]
    body_schema = command_operation["requestBody"]["content"]["application/json"]["schema"]
    serialized = str(body_schema)
    for command in (
        "AuthoringCreateCommand",
        "AuthoringReviseCommand",
        "AuthoringReorderCommand",
        "ShotReviseIntentCommand",
        "ShotRestoreCommand",
    ):
        assert command in serialized


def test_creator_projection_and_frontend_do_not_expose_private_media_or_signed_urls() -> None:
    source = "\n".join(path.read_text(encoding="utf-8") for path in AUTHORING_MODULES)
    frontend = "\n".join(
        (ROOT / "apps" / "studio" / "episode-workspace" / name).read_text(encoding="utf-8")
        for name in (
            "authoring-app.mjs",
            "authoring-api-client.mjs",
            "authoring-model.mjs",
            "authoring-commands.mjs",
        )
    )
    for forbidden in ("signed_url", "media_bytes", "provider_response", "absolute_path"):
        assert forbidden not in source + frontend
