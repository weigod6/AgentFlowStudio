from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Annotated, Any, Literal

from fastapi import Header
from pydantic import BaseModel, ConfigDict, Field, field_validator

from apps.api.runtime_store import safe_id


CREATOR_GOLDEN_TRIAL_SCHEMA = "afs_creator_golden_trial.v0.1"
TRIAL_SHOT_IDS = ("shot-001", "shot-002", "shot-003")
DEFAULT_CURRENCY = "USD"
IDEMPOTENCY_REPLAYABLE_FIELDS = frozenset({"created_at", "generated_at"})


class MissionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objective: str = Field(min_length=1, max_length=1200)
    constraints: tuple[str, ...] = Field(default_factory=tuple, max_length=12)
    project_ceiling_amount: float = Field(gt=0, le=100000)
    estimated_unit_cost_amount: float = Field(gt=0, le=100000)
    currency: str = Field(default=DEFAULT_CURRENCY, pattern=r"^[A-Z]{3}$")
    created_at: str = Field(min_length=1, max_length=64)

    @field_validator("created_at")
    @classmethod
    def _created_at_is_iso8601(cls, value: str) -> str:
        stamp(value)
        return value


class ApproveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_event_count: int = Field(ge=0, strict=True)
    created_at: str = Field(min_length=1, max_length=64)

    @field_validator("created_at")
    @classmethod
    def _created_at_is_iso8601(cls, value: str) -> str:
        stamp(value)
        return value


class DispatchNextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_event_count: int = Field(ge=0, strict=True)
    provider_service_id: str = Field(default="image_relay", min_length=1, max_length=120)
    capability: Literal["image"] = "image"
    aspect_ratio: str = Field(default="9:16", min_length=3, max_length=20)
    candidate_count: int = Field(default=1, ge=1, le=4)
    estimated_cost_amount: float | None = Field(default=None, gt=0, le=100000)
    generated_at: str = Field(min_length=1, max_length=64)

    @field_validator("generated_at")
    @classmethod
    def _generated_at_is_iso8601(cls, value: str) -> str:
        stamp(value)
        return value


IdempotencyKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=1,
        max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,159}$",
    ),
]


def replayable_dispatch_body(body: DispatchNextRequest) -> dict[str, Any]:
    payload = body.model_dump(mode="json")
    for field in IDEMPOTENCY_REPLAYABLE_FIELDS:
        payload.pop(field, None)
    return payload


def stamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError("timestamp must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat()


def fingerprint(payload: dict[str, Any]) -> str:
    return digest(payload)


def digest(value: object) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def object_id(prefix: str, *parts: object) -> str:
    return safe_id(f"{prefix}-{digest([str(part) for part in parts])[:20]}")


__all__ = (
    "CREATOR_GOLDEN_TRIAL_SCHEMA",
    "DEFAULT_CURRENCY",
    "TRIAL_SHOT_IDS",
    "ApproveRequest",
    "DispatchNextRequest",
    "IdempotencyKey",
    "MissionRequest",
    "digest",
    "fingerprint",
    "object_id",
    "replayable_dispatch_body",
    "stamp",
)
