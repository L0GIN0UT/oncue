"""Запуски событий: пересечение периодов отвергает сама БД (ADR 0054, 0071).

Добавляется ограничение ``EXCLUDE USING gist`` на ``event_runs``: у опубликованных запусков
(``published | active | finished | stopped``) занятый период ``[starts_at, конец)`` не пересекается,
глобально по всем событиям. Конец - ``LEAST(ends_at, COALESCE(stopped_at, ends_at))``, но не раньше
начала: остановка ускоряет конец (время после неё свободно), а запуск, остановленный до старта,
занимает пустой период. Это тот же предикат, что и проверка в транзакции под advisory-lock
(``EventRepository.overlaps``): она остаётся первой линией и даёт 409
``progression.event_period_overlap``, ограничение - последняя (прямая запись, баг, гонка мимо
lock-а). Черновики не участвуют.

Расширение ``btree_gist`` НЕ нужно: оно требуется для равенства скаляров внутри gist, а здесь
единственный оператор - ``&&`` по диапазону, его gist-класс встроен в Postgres. Суперпользователь
не нужен, роль ``progression_app`` (ADR 0060) ничего не наследует отдельно: таблица та же, права на
неё уже выданы ``0009``.

Expand-миграция. До добавления ограничения проверяются данные: при существующих пересечениях она
падает с перечнем пар id и ничего не меняет (в Postgres DDL транзакционен). Эти запуски админ
разводит по периодам вручную. Блокировки: ``LOCK TABLE`` и ``ADD CONSTRAINT`` берут
``ACCESS EXCLUSIVE`` на ``event_runs`` на время построения индекса - таблица крошечная (десятки
строк на сезон), но ``lock_timeout`` ограничивает ожидание, чтобы миграция не встала в очередь за
долгой транзакцией и не заблокировала за собой запросы. Откат снимает только ограничение, данные не
трогает.

Цепочка ревизий: ``0010`` -> ``0011`` (эта).

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-26
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NAME = "ex_event_runs_no_overlap"
_LOCK_TIMEOUT = "10s"
_LIVE = "('published','active','finished','stopped')"


def _busy(alias: str) -> str:
    """Занятый период запуска: ``[starts_at, конец)``, конец не раньше начала."""
    end = (
        f"GREATEST({alias}.starts_at, "
        f"LEAST({alias}.ends_at, COALESCE({alias}.stopped_at, {alias}.ends_at)))"
    )
    return f"tstzrange({alias}.starts_at, {end}, '[)')"


_FIND_OVERLAPS = f"""
SELECT a.id, b.id FROM event_runs a JOIN event_runs b ON a.id < b.id
WHERE a.status IN {_LIVE} AND b.status IN {_LIVE}
  AND {_busy("a")} && {_busy("b")}
ORDER BY a.starts_at, a.id
LIMIT 20
"""  # noqa: S608 — константы модуля, не ввод

_ADD = f"""
ALTER TABLE event_runs ADD CONSTRAINT {_NAME}
EXCLUDE USING gist ({_busy("event_runs")} WITH &&)
WHERE (status IN {_LIVE})
"""


def upgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'")
    op.execute("LOCK TABLE event_runs IN ACCESS EXCLUSIVE MODE")  # проверка и DDL - на одном срезе
    pairs = op.get_bind().exec_driver_sql(_FIND_OVERLAPS).all()
    if pairs:
        listing = "; ".join(f"{a} и {b}" for a, b in pairs)
        raise RuntimeError(
            "Миграция 0011 прервана: в event_runs есть опубликованные запуски с пересекающимися "
            f"периодами (первые пары id: {listing}). Разведите их по времени (остановите один или "
            "сдвиньте период) и повторите миграцию; схема не изменена."
        )
    op.execute(_ADD)


def downgrade() -> None:
    op.execute(f"ALTER TABLE event_runs DROP CONSTRAINT IF EXISTS {_NAME}")
