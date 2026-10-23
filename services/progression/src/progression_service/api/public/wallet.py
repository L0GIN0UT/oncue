"""Ручка кошелька: ``GET /v1/me/wallet`` (поток P1)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response

from gv_auth import Principal, require_user
from gv_service import problem_responses
from progression_service.api.public.schemas import LedgerEntryView, MyWallet
from progression_service.application.queries import ProgressQueries

User = Annotated[Principal, Depends(require_user)]
PRIVATE = "private, no-store"


def register_wallet_routes(router: APIRouter, queries: ProgressQueries) -> None:
    """Подключить ручки кошелька к роутеру ``/v1``."""

    @router.get(
        "/me/wallet",
        response_model=MyWallet,
        summary="Мой кошелёк",
        description="Версы по бакетам `earned` и `paid` и последние проводки.",
        responses=problem_responses(401, 403),
    )
    async def my_wallet(user: User, response: Response) -> MyWallet:
        response.headers["Cache-Control"] = PRIVATE
        view = await queries.my_wallet(user.user_id)
        return MyWallet(
            earned=view.earned,
            paid=view.paid,
            total=view.earned + view.paid,
            recent=[
                LedgerEntryView.model_validate(
                    {
                        "op_id": e.op_id,
                        "direction": e.direction.value,
                        "amount": e.amount,
                        "bucket": e.bucket.value,
                        "reason": e.reason,
                        "at": e.at,
                    }
                )
                for e in view.recent
            ],
        )
