"""Общее для роутеров: модель набора ассетов."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from media_pipeline_service.application.dto import AssetSetView


class AssetScopeOut(BaseModel):
    """Владелец набора (ADR 0029): свои загадки сборника."""

    kind: Literal["collection"]
    id: UUID = Field(description="collection_id")


class AssetSetItem(BaseModel):
    """Набор ассетов."""

    asset_set_id: UUID
    media_item_id: UUID
    clue_type: str
    profile_key: str
    params_hash: str
    status: str
    levels_count: int
    generation: int
    error_code: str | None
    scope: AssetScopeOut | None = Field(
        default=None,
        description="null — каталожный набор; collection — свои загадки сборника (ADR 0029)",
    )

    @classmethod
    def of(cls, view: AssetSetView) -> AssetSetItem:
        """Из DTO use-case."""
        return cls(
            asset_set_id=view.asset_set_id,
            media_item_id=view.media_item_id,
            clue_type=view.clue_type,
            profile_key=view.profile_key,
            params_hash=view.params_hash,
            status=view.status,
            levels_count=view.levels_count,
            generation=view.generation,
            error_code=view.error_code,
            scope=(
                AssetScopeOut(kind=view.scope.kind.value, id=view.scope.id) if view.scope else None
            ),
        )
