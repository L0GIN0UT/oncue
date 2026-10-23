"""Ошибки домена rating: машинные коды ``rating.*`` (контракт OpenAPI rating)."""

from __future__ import annotations

from typing import Any

from gv_core.errors import ConflictError, InvalidInputError, NotFoundError


class LadderInvalidError(InvalidInputError):
    """Конфиг ladder-а не проходит проверку; ``details.errors`` — пути и сообщения."""

    default_code = "rating.ladder_invalid"

    def __init__(self, errors: list[dict[str, str]]) -> None:
        super().__init__(
            f"конфиг ladder-а невалиден: {len(errors)} ошибок", details={"errors": errors}
        )


class LadderNotFoundError(NotFoundError):
    """Нет такого ladder-а (или он ещё не опубликован)."""

    default_code = "rating.ladder_not_found"

    def __init__(self, message: str = "ladder не найден") -> None:
        super().__init__(message)


class SeasonNotFoundError(NotFoundError):
    """Нет такого сезона."""

    default_code = "rating.season_not_found"

    def __init__(self, message: str = "сезон не найден") -> None:
        super().__init__(message)


class NotRankedError(NotFoundError):
    """Игрок вне лидерборда: калибровка или нет матчей в сезоне."""

    default_code = "rating.not_ranked"

    def __init__(self, message: str = "игрок пока вне лидерборда") -> None:
        super().__init__(message)


class UserNotFoundError(NotFoundError):
    """Пользователь неизвестен rating или удалён."""

    default_code = "rating.user_not_found"

    def __init__(self, message: str = "пользователь не найден") -> None:
        super().__init__(message)


class MatchNotRatedError(NotFoundError):
    """Матч не рейтинговый, не обработан или вызывающий не его участник."""

    default_code = "rating.match_not_rated"

    def __init__(self, message: str = "рейтинг матча не найден") -> None:
        super().__init__(message)


class VersionNotFoundError(NotFoundError):
    """Нет такой версии конфига ladder-а."""

    default_code = "rating.version_not_found"

    def __init__(self, message: str = "версия не найдена") -> None:
        super().__init__(message)


class VersionNotDraftError(ConflictError):
    """Публикуемая версия — не черновик."""

    default_code = "rating.version_not_draft"


class VersionNotPublishedError(ConflictError):
    """Откат к версии, которая никогда не публиковалась."""

    default_code = "rating.version_not_published"


class SeasonOverlapError(ConflictError):
    """Сезон пересекается с существующим."""

    default_code = "rating.season_overlap"


class SeasonNotActiveError(ConflictError):
    """Закрыть можно только действующий сезон."""

    default_code = "rating.season_not_active"


class NoActiveSeasonError(ConflictError):
    """Для ladder-а нет действующего сезона (обычно — миг между сезонами); повторимо."""

    default_code = "rating.no_active_season"


class LadderConflictError(ConflictError):
    """Прочие конфликты конфига (например, смена домена опубликованного ladder-а)."""

    default_code = "rating.ladder_conflict"

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message, details=details or None)


class MatchContractError(Exception):
    """Событие матча не соответствует контракту или правилам рейтинга.

    Повтор бессмыслен: consumer превращает её в ``PermanentError`` (сразу в DLQ).
    """
