"""Агрегат сборника: неизменяемые версии, один черновик с ревизией, события (D1, D2, ADR 0028).

Инварианты:

* ``content_version`` растёт только при новой опубликованной версии состава (публикация
  черновика, правка куратора, удаление заблокированной единицы); ``version`` агрегата —
  при каждом событии топика ``collections.collection.v1`` (лайки двигают её атомарным
  UPDATE в репозитории, см. ``liked_event``);
* ревизия черновика монотонна у агрегата (``draft_seq``): после публикации черновик
  исчезает, но старая ревизия другого устройства уже не совпадёт (нет ABA);
* скрытый сборник автор не правит и не публикует (``collections.hidden``), удалённый —
  тем более (``collections.deleted``);
* кураторский сборник (``kind = curated``) без автора и без черновика: публикуется сразу.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from collections_service.domain.errors import (
    CollectionDeletedError,
    CollectionHiddenError,
    DraftConflictError,
    NotCuratedError,
    NotPublishedError,
    VersionConflictError,
)
from collections_service.domain.events import (
    EVENT_TITLE_MAX,
    CollectionDeleted,
    CollectionLiked,
    CollectionPublished,
    CollectionUpdated,
    EventCustom,
    EventItem,
)
from collections_service.domain.model import (
    CollectionBody,
    Domain,
    Draft,
    DraftBody,
    Hidden,
    Kind,
    Status,
)
from gv_core.domain import AggregateRoot

DEFAULT_FEATURED_RANK = 1000
"""Порядок «рекомендуемого» без явного ``rank`` — в конце блока (максимум схемы API)."""


@dataclass(slots=True)
class CollectionState:
    """Состояние агрегата (то, что хранит репозиторий).

    Attributes:
        current: Опубликованная версия ``content_version``; ``None`` — ещё не публиковался.
        draft_seq: Последняя выданная ревизия черновика.
        first_published_at: Первая публикация (сортировка «новые», карточка).
        published_at: Публикация текущей версии состава.
    """

    domain: Domain
    kind: Kind
    status: Status
    author_id: UUID | None
    created_at: datetime
    updated_at: datetime
    content_version: int = 0
    current: CollectionBody | None = None
    draft: Draft | None = None
    draft_seq: int = 0
    hidden: Hidden | None = None
    featured: bool = False
    featured_rank: int | None = None
    first_published_at: datetime | None = None
    published_at: datetime | None = None
    deleted_at: datetime | None = None
    changed_content: bool = field(default=False, repr=False)


class Collection(AggregateRoot[UUID]):
    """Сборник (UGC или кураторский).

    Args:
        id: ``collection_id`` (uuid7).
        state: Состояние из репозитория.
        version: Версия агрегата (последнее событие).
    """

    def __init__(self, id: UUID, state: CollectionState, version: int = 0) -> None:  # noqa: A002
        super().__init__(id, version)
        self.state = state

    # ------------------------------------------------------------------ создание
    @classmethod
    def create(
        cls,
        collection_id: UUID,
        *,
        domain: Domain,
        title: str,
        author_id: UUID,
        now: datetime,
    ) -> Collection:
        """Новый UGC-сборник: черновик ревизии 1 с нормализованным названием."""
        draft = Draft(
            revision=1,
            base_version=None,
            body=DraftBody(title=title),
            client_label=None,
            updated_at=now,
        )
        state = CollectionState(
            domain=domain,
            kind=Kind.UGC,
            status=Status.DRAFT,
            author_id=author_id,
            created_at=now,
            updated_at=now,
            draft=draft,
            draft_seq=1,
        )
        return cls(collection_id, state)

    @classmethod
    def create_curated(
        cls,
        collection_id: UUID,
        *,
        domain: Domain,
        body: CollectionBody,
        now: datetime,
    ) -> Collection:
        """Кураторский сборник (D14): публикуется сразу, автор — «Guessverse»."""
        state = CollectionState(
            domain=domain,
            kind=Kind.CURATED,
            status=Status.DRAFT,
            author_id=None,
            created_at=now,
            updated_at=now,
        )
        collection = cls(collection_id, state)
        collection._publish_body(body, now)
        return collection

    # ------------------------------------------------------------------ свойства
    @property
    def is_published(self) -> bool:
        """Есть опубликованная версия, и сборник не скрыт и не удалён."""
        return self.state.status is Status.PUBLISHED

    @property
    def current_revision(self) -> int:
        """Ревизия черновика; 0 — черновика нет."""
        return self.state.draft.revision if self.state.draft else 0

    def is_author(self, user_id: UUID | None) -> bool:
        """Пользователь — автор сборника."""
        return user_id is not None and self.state.author_id == user_id

    def draft_view(self) -> Draft:
        """Черновик или тело опубликованной версии с ``revision = 0`` (``GET .../draft``)."""
        s = self.state
        if s.draft is not None:
            return s.draft
        if s.current is None:  # черновик-только без черновика невозможен (create)
            raise CollectionDeletedError()
        return Draft(
            revision=0,
            base_version=s.content_version,
            body=s.current.as_draft(),
            client_label=None,
            updated_at=s.published_at or s.updated_at,
        )

    # ------------------------------------------------------------------ черновик
    def _require_editable(self) -> None:
        if self.state.status is Status.DELETED:
            raise CollectionDeletedError()
        if self.state.status is Status.HIDDEN:
            raise CollectionHiddenError()

    def _conflict(self) -> DraftConflictError:
        # details.current заполняет application (DraftOut с телом): домен JSON API не знает.
        return DraftConflictError(
            "черновик изменён на другом устройстве", details={"revision": self.current_revision}
        )

    def save_draft(
        self, body: DraftBody, *, base_revision: int, client_label: str | None, now: datetime
    ) -> Draft:
        """Сохранить черновик с оптимистичной блокировкой (D2).

        Raises:
            CollectionDeletedError, CollectionHiddenError: сборник удалён или скрыт.
            DraftConflictError: ``base_revision`` ≠ текущей ревизии.
        """
        self._require_editable()
        if self.state.kind is Kind.CURATED:
            raise NotCuratedError("кураторский сборник правится через admin-API")
        if base_revision != self.current_revision:
            raise self._conflict()
        s = self.state
        s.draft_seq += 1
        s.draft = Draft(
            revision=s.draft_seq,
            base_version=s.content_version or None,
            body=body,
            client_label=client_label,
            updated_at=now,
        )
        s.updated_at = now
        return s.draft

    def require_draft(self, revision: int) -> Draft:
        """Черновик ревизии ``revision`` для публикации.

        Raises:
            CollectionDeletedError, CollectionHiddenError: сборник удалён или скрыт.
            DraftConflictError: черновика нет или ревизия другая.
        """
        self._require_editable()
        draft = self.state.draft
        if draft is None or draft.revision != revision:
            raise self._conflict()
        return draft

    def publish(self, revision: int, body: CollectionBody, now: datetime) -> None:
        """Опубликовать проверенное тело черновика ревизии ``revision`` (новая версия).

        Raises:
            DraftConflictError: черновик за это время изменился.
        """
        self.require_draft(revision)
        self.state.draft = None
        self._publish_body(body, now)

    def _publish_body(self, body: CollectionBody, now: datetime) -> None:
        s = self.state
        s.current = body
        s.content_version += 1
        s.changed_content = True
        s.status = Status.PUBLISHED if s.status is not Status.HIDDEN else Status.HIDDEN
        s.first_published_at = s.first_published_at or now
        s.published_at = now
        s.updated_at = now
        self._record_snapshot(CollectionPublished, now)

    # ------------------------------------------------------------------ кураторские
    def update_curated(self, body: CollectionBody, *, base_version: int, now: datetime) -> None:
        """Новая версия кураторского сборника (оптимистичная блокировка по версии).

        Raises:
            NotCuratedError: сборник пользовательский.
            CollectionDeletedError: сборник удалён.
            VersionConflictError: ``base_version`` ≠ текущей ``content_version``.
        """
        if self.state.status is Status.DELETED:
            raise CollectionDeletedError()
        if self.state.kind is not Kind.CURATED:
            raise NotCuratedError()
        if base_version != self.state.content_version:
            raise VersionConflictError(
                "версия сборника устарела",
                details={"current_version": self.state.content_version},
            )
        self._publish_body(body, now)

    # ------------------------------------------------------------------ модерация
    def hide(self, *, reason: str, actor_id: UUID, now: datetime) -> bool:
        """Скрыть (не играется, не ищется); ``False`` — уже скрыт.

        Raises:
            CollectionDeletedError: удалён; NotPublishedError: ещё не опубликован.
        """
        s = self.state
        if s.status is Status.DELETED:
            raise CollectionDeletedError()
        if s.status is Status.HIDDEN:
            return False
        if s.status is not Status.PUBLISHED:
            raise NotPublishedError()
        s.status = Status.HIDDEN
        s.hidden = Hidden(reason=reason, actor_id=actor_id, at=now)
        s.featured, s.featured_rank = False, None
        s.updated_at = now
        self._record_snapshot(CollectionUpdated, now)
        return True

    def unhide(self, now: datetime) -> bool:
        """Показать скрытый; ``False`` — не был скрыт.

        Raises:
            CollectionDeletedError: удалён.
        """
        s = self.state
        if s.status is Status.DELETED:
            raise CollectionDeletedError()
        if s.status is not Status.HIDDEN:
            return False
        s.status = Status.PUBLISHED
        s.hidden = None
        s.updated_at = now
        self._record_snapshot(CollectionUpdated, now)
        return True

    def set_featured(self, *, featured: bool, rank: int | None, now: datetime) -> None:
        """Отметка «рекомендуемое» (D14): только опубликованные; события нет.

        Raises:
            NotPublishedError: сборник не опубликован (черновик, скрыт или удалён).
        """
        if featured and not self.is_published:
            raise NotPublishedError("«рекомендуемое» — только для опубликованных")
        self.state.featured = featured
        self.state.featured_rank = (rank or DEFAULT_FEATURED_RANK) if featured else None
        self.state.updated_at = now

    # ------------------------------------------------------------------ состав
    def remove_item(self, media_item_id: UUID, now: datetime) -> bool:
        """Убрать заблокированную единицу из опубликованного состава (новая версия).

        Черновик не трогается: при публикации единица не пройдёт проверку активности.

        Returns:
            ``True`` — состав изменился (событие ``CollectionUpdated``).
        """
        s = self.state
        if s.current is None or s.status not in (Status.PUBLISHED, Status.HIDDEN):
            return False
        if all(i.media_item_id != media_item_id for i in s.current.items):
            return False
        s.current = s.current.without(media_item_id)
        s.content_version += 1
        s.changed_content = True
        s.published_at = now
        s.updated_at = now
        self._record_snapshot(CollectionUpdated, now)
        return True

    # ------------------------------------------------------------------ удаление
    def delete(self, now: datetime) -> bool:
        """Удалить; ``False`` — уже удалён. Опубликованный — событие ``CollectionDeleted``."""
        s = self.state
        if s.status is Status.DELETED:
            return False
        was_public = s.current is not None
        s.status = Status.DELETED
        s.draft = None
        s.featured, s.featured_rank = False, None
        s.deleted_at = now
        s.updated_at = now
        if was_public:
            self.version += 1
            self.record(
                CollectionDeleted(
                    aggregate_id=str(self.id),
                    collection_id=self.id,
                    version=self.version,
                    deleted_at=now,
                )
            )
        return True

    def forget_author(self, now: datetime) -> bool:
        """Автор удалил аккаунт (решение владельца): черновик удаляется, опубликованный
        сборник остаётся с автором «удалён» (``CollectionUpdated``).

        Returns:
            ``True`` — сборник никогда не публиковался: репозиторий удаляет его целиком.
        """
        s = self.state
        if s.current is None:
            return True
        s.author_id = None
        s.draft = None
        s.updated_at = now
        if s.status in (Status.PUBLISHED, Status.HIDDEN):
            self._record_snapshot(CollectionUpdated, now)
        return False

    # ------------------------------------------------------------------ события
    def _record_snapshot(
        self, event: type[CollectionPublished] | type[CollectionUpdated], now: datetime
    ) -> None:
        s = self.state
        assert s.current is not None  # noqa: S101 — снимок только у опубликованного
        self.version += 1
        self.record(
            event(
                aggregate_id=str(self.id),
                collection_id=self.id,
                version=self.version,
                content_version=s.content_version,
                domain=str(s.domain),
                kind=str(s.kind),
                visibility=str(s.current.visibility),
                status=str(Status.HIDDEN if s.status is Status.HIDDEN else Status.PUBLISHED),
                author_id=s.author_id,
                default_mode_key=s.current.default_mode_key,
                items=tuple(
                    EventItem(
                        media_item_id=item.media_item_id,
                        position=position,
                        custom=EventCustom(emoji=item.emoji) if item.emoji else None,
                    )
                    for position, item in enumerate(s.current.items)
                ),
                published_at=s.published_at or now,
                title=s.current.title[:EVENT_TITLE_MAX],
                tags=s.current.tags,
            )
        )


def liked_event(
    collection_id: UUID, *, version: int, user_id: UUID, likes_count: int, now: datetime
) -> CollectionLiked:
    """Событие нового лайка (счётчик и ``version`` уже увеличены атомарным UPDATE)."""
    return CollectionLiked(
        aggregate_id=str(collection_id),
        collection_id=collection_id,
        version=version,
        user_id=user_id,
        likes_count=likes_count,
        liked_at=now,
    )
