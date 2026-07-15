from __future__ import annotations

import json
import re
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from agentflow.contracts.project_manifest import validate_project_manifest
from agentflow.harness.constants import AGENTFLOW_FORBIDDEN_PRIVATE_FRAGMENTS
from agentflow.harness.json_io import exclusive_file_lock, write_json


SAFE_ID_PATTERN = re.compile(r"[^a-zA-Z0-9_.-]+")
RUN_JOB_PATH_TOKEN_MAX_LEN = 24
JOB_FILE_PATH_TOKEN_MAX_LEN = 80


class RuntimeStore:
    def __init__(self, runtime_root: Path) -> None:
        self.root = Path(runtime_root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.projects_dir = self.root / "projects"
        self.runs_dir = self.root / "runs"
        self.jobs_dir = self.root / "jobs"
        self.feedback_dir = self.root / "feedback"
        for path in (self.projects_dir, self.runs_dir, self.jobs_dir, self.feedback_dir):
            path.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / "artifact_index.json"
        self.index_transaction_lock_path = self.root / "artifact_index.transaction.lock"
        with exclusive_file_lock(self.index_transaction_lock_path):
            if not self.index_path.exists():
                write_json(self.index_path, {"artifacts": {}})
            else:
                write_json(self.index_path, self._artifact_index())

    def project_manifest_path(self, project_id: str) -> Path:
        safe = safe_id(project_id)
        return self.projects_dir / safe / "project_manifest.json"

    def project_deleted_marker_path(self, project_id: str) -> Path:
        safe = safe_id(project_id)
        return self.projects_dir / safe / "project_deleted.json"

    def production_runs_dir(self, project_id: str) -> Path:
        return self.projects_dir / safe_id(project_id) / "production_runs"

    def production_run_path(self, project_id: str, run_id: str) -> Path:
        return self.production_runs_dir(project_id) / safe_id(run_id) / "production_run.json"

    def production_run_lock_path(self, project_id: str) -> Path:
        return self.production_runs_dir(project_id) / "production_runs.lock"

    def domain_crew_path(self, project_id: str) -> Path:
        return self.projects_dir / safe_id(project_id) / "domain_crew.json"

    def domain_crew_lock_path(self, project_id: str) -> Path:
        return self.projects_dir / safe_id(project_id) / "domain_crew.lock"

    def write_domain_crew(self, project_id: str, crew: dict[str, Any]) -> dict[str, Any]:
        self.ensure_project_manifest(project_id)
        if str(crew.get("project_id") or "") != project_id:
            raise ValueError("domain crew project id does not match storage scope")
        reject_unsafe_payload(crew)
        write_json(self.domain_crew_path(project_id), crew)
        return crew

    def load_domain_crew(self, project_id: str) -> dict[str, Any]:
        path = self.domain_crew_path(project_id)
        if not path.exists():
            raise KeyError(project_id)
        crew = read_json(path)
        reject_unsafe_payload(crew)
        if str(crew.get("project_id") or "") != project_id:
            raise ValueError("domain crew storage identity mismatch")
        return crew

    def is_project_deleted(self, project_id: str) -> bool:
        return self.project_deleted_marker_path(project_id).is_file()

    def create_project_manifest(
        self,
        *,
        project_id: str,
        project_type: str,
        goal: str,
        status: str,
    ) -> dict[str, Any]:
        payload = {
            "artifact_type": "agentflow_project_manifest",
            "schema_version": "0.1.0",
            "project_id": project_id,
            "project_type": project_type,
            "goal": goal,
            "source_assets": [],
            "content_cards": [],
            "runs": [],
            "packages": [],
            "feedback_refs": [],
            "profile_version_refs": [],
            "status": status,
            "does_not_store_secrets": True,
            "does_not_store_private_asset_bytes": True,
            "does_not_auto_sync": True,
        }
        validate_project_manifest(payload)
        path = self.project_manifest_path(project_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        marker = self.project_deleted_marker_path(project_id)
        if marker.exists():
            marker.unlink()
        write_json(path, payload)
        return payload

    def ensure_project_manifest(self, project_id: str) -> dict[str, Any]:
        path = self.project_manifest_path(project_id)
        if not path.exists():
            return self.create_project_manifest(
                project_id=project_id,
                project_type="short_video_campaign",
                goal="未命名项目",
                status="in_progress",
            )
        payload = read_json(path)
        validate_project_manifest(payload)
        return payload

    def import_project_manifest(self, manifest: dict[str, Any]) -> dict[str, Any]:
        payload = dict(manifest)
        reject_unsafe_payload(payload)
        validate_project_manifest(payload)
        project_id = str(payload["project_id"])
        path = self.project_manifest_path(project_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json(path, payload)
        return payload

    def list_project_summaries(self) -> list[dict[str, Any]]:
        summaries: list[dict[str, Any]] = []
        for path in sorted(self.projects_dir.glob("*/project_manifest.json")):
            if (path.parent / "project_deleted.json").is_file():
                continue
            manifest = read_json(path)
            validate_project_manifest(manifest)
            artifact = self.register_artifact(path, role="project_manifest")
            summaries.append(project_summary(manifest, artifact))
        return summaries

    def soft_delete_project(self, project_id: str, *, deleted_by: str = "", reason: str = "user_requested") -> dict[str, Any]:
        path = self.project_manifest_path(project_id)
        if not path.exists():
            raise KeyError(project_id)
        manifest = read_json(path)
        validate_project_manifest(manifest)
        marker = {
            "schema_version": "0.1.0",
            "project_id": str(manifest.get("project_id") or project_id),
            "deleted": True,
            "delete_mode": "soft_delete",
            "deleted_at": datetime.now(timezone.utc).isoformat(),
            "deleted_by": str(deleted_by or ""),
            "reason": str(reason or "user_requested"),
            "does_not_delete_project_bytes": True,
        }
        marker_path = self.project_deleted_marker_path(project_id)
        marker_path.parent.mkdir(parents=True, exist_ok=True)
        write_json(marker_path, marker)
        return marker

    def update_project_manifest(self, project_id: str, updates: dict[str, list[dict[str, Any]]], status: str) -> dict[str, Any]:
        manifest = self.ensure_project_manifest(project_id)
        for field, refs in updates.items():
            existing = list(manifest.get(field, []))
            for ref in refs:
                if ref not in existing:
                    existing.append(ref)
            manifest[field] = existing
        manifest["status"] = status
        validate_project_manifest(manifest)
        write_json(self.project_manifest_path(project_id), manifest)
        return manifest

    def add_source_asset(self, project_id: str, asset_ref: dict[str, Any]) -> dict[str, Any]:
        reject_unsafe_payload(asset_ref)
        return self.update_project_manifest(project_id, {"source_assets": [asset_ref]}, status="in_progress")

    def add_content_card(self, project_id: str, content_card: dict[str, Any]) -> dict[str, Any]:
        reject_unsafe_payload(content_card)
        return self.update_project_manifest(project_id, {"content_cards": [content_card]}, status="in_progress")

    def write_production_run(self, project_id: str, run: dict[str, Any]) -> dict[str, Any]:
        self.ensure_project_manifest(project_id)
        if str(run.get("project_id") or "") != project_id:
            raise ValueError("production run project id does not match storage scope")
        run_id = str(run.get("run_id") or "")
        if not run_id:
            raise ValueError("production run requires run_id")
        reject_unsafe_payload(run)
        path = self.production_run_path(project_id, run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json(path, run)
        run_ref = {
            "run_id": run_id,
            "artifact_type": str(run.get("artifact_type") or "afs_runtime_production_run"),
            "schema_version": str(run.get("schema_version") or ""),
            "status": str(run.get("status") or ""),
        }
        manifest = self.ensure_project_manifest(project_id)
        manifest["runs"] = [
            item
            for item in manifest.get("runs", [])
            if not isinstance(item, dict) or str(item.get("run_id") or "") != run_id
        ]
        manifest["runs"].append(run_ref)
        manifest["status"] = "in_progress"
        validate_project_manifest(manifest)
        write_json(self.project_manifest_path(project_id), manifest)
        return run

    def load_production_run(self, project_id: str, run_id: str) -> dict[str, Any]:
        path = self.production_run_path(project_id, run_id)
        if not path.exists():
            raise KeyError(run_id)
        run = read_json(path)
        reject_unsafe_payload(run)
        if str(run.get("project_id") or "") != project_id or str(run.get("run_id") or "") != run_id:
            raise ValueError("production run storage identity mismatch")
        return run

    def list_production_runs(self, project_id: str) -> list[dict[str, Any]]:
        self.ensure_project_manifest(project_id)
        runs: list[dict[str, Any]] = []
        for path in sorted(self.production_runs_dir(project_id).glob("*/production_run.json")):
            run = read_json(path)
            reject_unsafe_payload(run)
            if str(run.get("project_id") or "") == project_id:
                runs.append(run)
        return runs

    def update_content_card(self, project_id: str, card_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        manifest = self.ensure_project_manifest(project_id)
        cards = list(manifest.get("content_cards", []))
        for index, item in enumerate(cards):
            if isinstance(item, dict) and item.get("card_id") == card_id:
                merged = {**item, **updates}
                reject_unsafe_payload(merged)
                cards[index] = merged
                manifest["content_cards"] = cards
                manifest["status"] = "in_progress"
                validate_project_manifest(manifest)
                write_json(self.project_manifest_path(project_id), manifest)
                return manifest
        raise ValueError("content card not found")

    def new_job_id(self, action: str, project_id: str) -> str:
        return f"{safe_id(project_id)}-{safe_id(action)}-{uuid4().hex[:12]}"

    def run_dir(self, project_id: str, job_id: str) -> Path:
        return self.runs_dir / safe_id(project_id) / storage_path_token(job_id, max_len=RUN_JOB_PATH_TOKEN_MAX_LEN)

    def feedback_run_dir(self, project_id: str, job_id: str) -> Path:
        return self.feedback_dir / safe_id(project_id) / storage_path_token(job_id, max_len=RUN_JOB_PATH_TOKEN_MAX_LEN)

    def write_job(self, job: dict[str, Any]) -> dict[str, Any]:
        write_json(self.job_path(str(job["job_id"])), job)
        return public_job(job)

    def job_path(self, job_id: str) -> Path:
        return self.jobs_dir / f"{storage_path_token(job_id, max_len=JOB_FILE_PATH_TOKEN_MAX_LEN)}.json"

    def load_job(self, job_id: str) -> dict[str, Any]:
        path = self.job_path(job_id)
        legacy_path = self.jobs_dir / f"{safe_id(job_id)}.json"
        if not path.exists() and legacy_path != path and legacy_path.exists():
            path = legacy_path
        if not path.exists():
            raise KeyError(job_id)
        return read_json(path)

    def list_project_jobs(self, project_id: str) -> list[dict[str, Any]]:
        jobs: list[dict[str, Any]] = []
        for path in sorted(self.jobs_dir.glob("*.json"), key=lambda item: item.stat().st_mtime):
            job = read_json(path)
            if job.get("project_id") == project_id:
                jobs.append(public_job(job))
        return jobs

    def register_artifact(self, path: Path, *, role: str) -> dict[str, Any]:
        resolved = Path(path).resolve()
        if not resolved.exists():
            raise FileNotFoundError(str(path))
        relative_path = resolved.relative_to(self.root.resolve()).as_posix()
        artifact_id = safe_id(str(resolved.relative_to(self.root.resolve()).with_suffix("")))
        payload = read_json(resolved) if resolved.suffix.lower() == ".json" else None
        artifact_type = str((payload or {}).get("artifact_type") or (payload or {}).get("kind") or "text_artifact")
        entry = {
            "artifact_id": artifact_id,
            "relative_path": relative_path,
            "filename": resolved.name,
            "artifact_type": artifact_type,
            "role": role,
            "media_type": "application/json" if resolved.suffix.lower() == ".json" else "text/markdown",
        }
        project_id = _project_id_from_artifact_relative_path(relative_path)
        if project_id:
            entry["project_id"] = project_id
        with exclusive_file_lock(self.index_transaction_lock_path):
            index = self._artifact_index()
            index.setdefault("artifacts", {})[artifact_id] = entry
            write_json(self.index_path, index)
        return public_artifact_ref(entry)

    def artifact_project_id(self, artifact_id: str) -> str:
        index = self._artifact_index()
        entry = dict(index.get("artifacts", {}).get(artifact_id) or {})
        if not entry:
            raise KeyError(artifact_id)
        return str(entry.get("project_id") or _project_id_from_artifact_relative_path(str(entry.get("relative_path") or "")))

    def read_artifact(self, artifact_id: str) -> dict[str, Any]:
        index = self._artifact_index()
        entry = dict(index.get("artifacts", {}).get(artifact_id) or {})
        if not entry:
            raise KeyError(artifact_id)
        path = (self.root / str(entry["relative_path"])).resolve()
        if not _is_within(path, self.root.resolve()):
            raise ValueError("artifact path escapes runtime root")
        ref = public_artifact_ref(entry)
        if entry["media_type"] == "application/json":
            payload = read_json(path)
            reject_unsafe_payload(payload)
            return {**ref, "payload": payload}
        text = path.read_text(encoding="utf-8")
        reject_unsafe_text(text)
        return {**ref, "text": text}

    def _artifact_index(self) -> dict[str, Any]:
        try:
            index = read_json(self.index_path)
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            index = {}
        artifacts = index.get("artifacts")
        if not isinstance(artifacts, dict):
            index["artifacts"] = {}
        return index


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"{Path(path).name} must be a JSON object")
    return payload


def public_job(job: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in job.items() if not key.startswith("_")}


def public_artifact_ref(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "artifact_id": entry["artifact_id"],
        "artifact_type": entry["artifact_type"],
        "filename": entry["filename"],
        "role": entry["role"],
        "media_type": entry["media_type"],
    }


def project_summary(manifest: dict[str, Any], artifact: dict[str, Any]) -> dict[str, Any]:
    return {
        "project_id": manifest["project_id"],
        "project_type": manifest["project_type"],
        "goal": manifest["goal"],
        "status": manifest["status"],
        "run_count": len(manifest.get("runs", [])),
        "package_count": len(manifest.get("packages", [])),
        "feedback_count": len(manifest.get("feedback_refs", [])),
        "profile_version_count": len(manifest.get("profile_version_refs", [])),
        "content_card_count": len(manifest.get("content_cards", [])) if isinstance(manifest.get("content_cards"), list) else 0,
        "artifact": artifact,
    }


def safe_id(value: str) -> str:
    cleaned = SAFE_ID_PATTERN.sub("-", str(value).strip()).strip("-._")
    return cleaned or "item"


def storage_path_token(value: str, *, max_len: int) -> str:
    cleaned = safe_id(value)
    if len(cleaned) <= max_len:
        return cleaned
    digest = hashlib.sha256(cleaned.encode("utf-8")).hexdigest()[:8]
    prefix = cleaned[: max(1, max_len - 9)].rstrip("-._") or "item"
    return f"{prefix}-{digest}"[:max_len]


def reject_unsafe_payload(payload: dict[str, Any]) -> None:
    reject_unsafe_text(json.dumps(payload, ensure_ascii=False))


def reject_unsafe_text(value: str) -> None:
    lowered = value.lower()
    for fragment in AGENTFLOW_FORBIDDEN_PRIVATE_FRAGMENTS:
        if fragment.lower() in lowered:
            raise ValueError("artifact contains private path, media ref, or secret-like fragment")


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _project_id_from_artifact_relative_path(relative_path: str) -> str:
    parts = [part for part in str(relative_path or "").replace("\\", "/").split("/") if part]
    if len(parts) >= 2 and parts[0] in {"projects", "runs", "feedback"}:
        return safe_id(parts[1])
    return ""


__all__ = (
    "RuntimeStore",
    "project_summary",
    "public_job",
    "read_json",
    "safe_id",
    "storage_path_token",
)
