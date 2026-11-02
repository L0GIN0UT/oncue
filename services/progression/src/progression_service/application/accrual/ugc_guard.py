"""Защита от накрутки наград через пользовательские сборники (соло и матчи по сборнику).

Правила (``ugc_guard`` конфига, значения по умолчанию — ``domain.config.UgcGuardConfig``):

1. **Свой сборник.** Игрок, который сам автор сборника, не получает за него ни опыта, ни Версов
   (если ``own_collection_rewards`` не включён). Иначе можно «крутить» опыт, создав сборник и
   проходя его самому.
2. **Повтор.** Награда за сборник засчитывается игроку не чаще раза в окно
   ``repeat_window_hours`` (ключ «игрок + сборник»). Метку занимает одна атомарная команда БД,
   поэтому два параллельных события не пройдут оба.
3. **Суточный потолок** опыта с пользовательских сборников (``ugc_daily_xp_cap``) применяет
   вызывающий через счётчик ``ugc_xp``.

Сборник без записи в проекции (событие ``CollectionPublished`` ещё не приходило, старые
данные) считается «владелец неизвестен»: награда начисляется как раньше, случай логируется
(``progression.ugc_guard_owner_unknown``). Проекцию дополняет команда догрузки авторов.

Вердикт записывается нулевой отметкой предмета (``accrual_marks``), поэтому повтор события
не начислит награду задним числом, даже когда окно уже прошло.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID

import structlog

from progression_service.application.ports import ProgressionUnitOfWork
from progression_service.domain.config import UgcGuardConfig

log = structlog.get_logger(__name__)

KIND_UGC = "ugc"


class Verdict(StrEnum):
    """Что делать с наградой за сборник."""

    ALLOWED = "allowed"
    OWN_COLLECTION = "own_collection"
    REPEAT = "repeat"

    @property
    def mark_reason(self) -> str:
        """Причина нулевой отметки предмета."""
        return f"ugc_{self.value}"


@dataclass(frozen=True, slots=True)
class Decision:
    """Итог проверки.

    Attributes:
        verdict: ``ALLOWED`` — начислять по обычным правилам.
        ugc: Среди сборников есть пользовательский: опыт идёт в суточный потолок UGC.
    """

    verdict: Verdict
    ugc: bool = False

    @property
    def allowed(self) -> bool:
        """Награда положена."""
        return self.verdict is Verdict.ALLOWED


NO_COLLECTION = Decision(Verdict.ALLOWED)


async def decide(
    uow: ProgressionUnitOfWork,
    cfg: UgcGuardConfig,
    user_id: UUID,
    collection_ids: Iterable[UUID],
    at: datetime,
) -> Decision:
    """Проверить награду игрока за сборники сессии или матча.

    Занимает метку прохождения (побочный эффект) только когда вердикт ``ALLOWED``; вызывать
    один раз на игрока и событие, и только если награда ещё не начислена.

    Args:
        at: Момент события (``finished_at``): окно считается от него, а не от времени обработки.
    """
    ids = tuple(dict.fromkeys(collection_ids))
    if not ids:
        return NO_COLLECTION
    owners = await uow.collection_authors.get_many(ids)
    unknown = [str(i) for i in ids if i not in owners]
    if unknown:
        log.warning(
            "progression.ugc_guard_owner_unknown", collections=unknown, user_id=str(user_id)
        )
    known = [(cid, owner) for cid, owner in owners.items() if cid in ids]
    ugc = any(owner.kind == KIND_UGC for _, owner in known)
    if not cfg.own_collection_rewards and any(
        owner.kind == KIND_UGC and owner.author_id == user_id for _, owner in known
    ):
        log.info("progression.ugc_guard_own_collection", user_id=str(user_id))
        return Decision(Verdict.OWN_COLLECTION, ugc=True)
    if cfg.repeat_window_hours > 0:
        scoped = sorted(
            cid for cid, owner in known if cfg.repeat_scope == "all" or owner.kind == KIND_UGC
        )
        if scoped:
            not_after = at - timedelta(hours=cfg.repeat_window_hours)
            claimed = [
                await uow.play_marks.claim(user_id, cid, at=at, not_after=not_after)
                for cid in scoped
            ]
            if not any(claimed):
                log.info("progression.ugc_guard_repeat", user_id=str(user_id))
                return Decision(Verdict.REPEAT, ugc=ugc)
    return Decision(Verdict.ALLOWED, ugc=ugc)
