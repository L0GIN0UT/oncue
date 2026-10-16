"""Схемы API collections (план этапа 3b §2.5, ADR 0028, 0030) — контракт для bff, фронтенда
и админки.

* Публичные запросы — ``extra="forbid"`` (mass assignment, §12 п. 2): ``kind``,
  ``author_id``, ``featured``, ``status`` задаются только через admin-API.
* Пользовательский текст (название, описание, название тега) сервис нормализует
  ``gv_text.ugc`` и отвечает уже нормализованным; ограничения длины в схеме — грубая
  граница ввода, точные лимиты — ``GET /v1/meta`` (``GV_COLLECTIONS__LIMITS__*``).
* Состав сборника (``items``) отдаётся только автору, админу и прошедшему игроку (D6).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from gv_service import Reason

Domain = Literal["music", "film"]
Kind = Literal["ugc", "curated"]
Visibility = Literal["public", "unlisted"]
CollectionStatus = Literal["draft", "published", "hidden"]
"""Статус для автора и админа; удалённый сборник не отдаётся никому (404)."""
Sort = Literal["popular", "new", "likes", "difficulty"]
Difficulty = Literal["easy", "medium", "hard"]
CollectionModeKey = Annotated[
    str,
    StringConstraints(pattern=r"^(music|film)\.collection\.[a-z0-9_]+$", max_length=100),
]
PresetId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,31}$")]
TagKey = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,31}$")]
ClientLabel = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
Cursor = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,512}$")]
TitleIn = Annotated[str, StringConstraints(min_length=1, max_length=200)]
"""Сырое название: граница ввода; после нормализации — не длиннее ``limits.title_max``."""
DescriptionIn = Annotated[str, StringConstraints(max_length=2000)]
EmojiIn = Annotated[str, StringConstraints(min_length=1, max_length=128)]
MAX_ITEMS = 200


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ----------------------------------------------------------------------------- состав
class CustomClue(_Request):
    """Свои загадки автора к единице (ADR 0029): в 3b только эмодзи.

    Эмодзи — строго RGI (``gv_text.emoji.is_strict_emoji``), не больше
    ``limits.emoji_max_graphemes`` графем, без текста и пробелов; иначе при публикации —
    ``collections.emoji_invalid``.
    """

    emoji: EmojiIn | None = None


class DraftItem(_Request):
    """Единица черновика в порядке автора."""

    media_item_id: UUID
    custom: CustomClue | None = None


class Cover(_Request):
    """Обложка — пресет из ``GET /v1/meta`` (градиент и эмодзи из конфига), D6."""

    preset: PresetId


class DraftBody(_Request):
    """Тело черновика: всё, что автор правит в конструкторе (домен фиксирован при создании).

    Черновик сохраняется и неполным (меньше ``items_min`` единиц, без режима); полная
    проверка — при публикации.
    """

    title: TitleIn
    description: DescriptionIn | None = None
    tags: list[TagKey] = Field(default_factory=list, max_length=20)
    cover: Cover | None = None
    default_mode_key: CollectionModeKey | None = None
    visibility: Visibility = "public"
    items: list[DraftItem] = Field(default_factory=list, max_length=MAX_ITEMS)


class CreateCollectionRequest(_Request):
    """Новый сборник: создаётся черновик ``revision = 1`` с этим названием."""

    domain: Domain
    title: TitleIn


class DraftOut(BaseModel):
    """Черновик сборника (виден только автору; чужой — 404 ``collections.not_found``)."""

    collection_id: UUID
    domain: Domain
    status: CollectionStatus
    revision: int = Field(
        ge=0, description="0 — черновика нет, тело взято из опубликованной версии"
    )
    base_version: int | None = Field(
        default=None, description="content_version, поверх которой начат черновик"
    )
    client_label: str | None = Field(
        default=None, description="Подпись устройства последнего сохранения (экран конфликта)"
    )
    updated_at: datetime
    body: DraftBody


class SaveDraftRequest(_Request):
    """Сохранить черновик с оптимистичной блокировкой (D2).

    ``base_revision`` — ревизия, которую клиент правил; не совпала с текущей — 409
    ``collections.draft_conflict``, ``details.current`` — актуальный ``DraftOut``.
    """

    base_revision: int = Field(ge=0)
    client_label: ClientLabel | None = None
    body: DraftBody


class PublishRequest(_Request):
    """Опубликовать ревизию черновика (новая ``content_version``)."""

    draft_revision: int = Field(ge=1)


# ----------------------------------------------------------------------------- карточки
class AuthorOut(BaseModel):
    """Автор: пользователь (ник из проекции identity), «Guessverse» или удалённый."""

    kind: Literal["user", "curator", "deleted"]
    user_id: UUID | None = None
    nickname: str | None = None


class CoverOut(BaseModel):
    """Обложка сборника."""

    preset: str


class CollectionCard(BaseModel):
    """Карточка в каталоге и списках (без состава)."""

    collection_id: UUID
    domain: Domain
    kind: Kind
    title: str
    cover: CoverOut | None = None
    tags: list[str]
    author: AuthorOut
    default_mode_key: str
    items_count: int = Field(ge=0)
    likes_count: int = Field(ge=0)
    plays_count: int = Field(ge=0, description="Завершений пользователями (не автором)")
    difficulty: Difficulty | None = Field(
        default=None, description="null — завершений меньше min_plays (D9)"
    )
    featured: bool = False
    published_at: datetime


class CollectionPage(BaseModel):
    """Страница каталога (keyset-курсор)."""

    items: list[CollectionCard]
    next_cursor: str | None = None


class MeOut(BaseModel):
    """Отношение текущего пользователя к сборнику (гостю и анониму — ``me: null``)."""

    liked: bool
    favorite: bool
    completed: bool = Field(description="Есть завершение текущей версии (открывает состав)")
    is_author: bool = False


class CustomClueOut(BaseModel):
    """Свои загадки единицы (видит только автор и админ)."""

    emoji: str | None = None


class CollectionItemOut(BaseModel):
    """Единица состава: только по D6 (автор, админ, прошедший игрок)."""

    position: int = Field(ge=0)
    media_item_id: UUID
    title: str
    year: int | None = None
    available: bool = Field(description="false — единица заблокирована или удалена в catalog")
    custom: CustomClueOut | None = None


class CollectionDetail(CollectionCard):
    """Страница сборника. ``items`` — ``null``, если состав текущему субъекту не открыт."""

    description: str | None = None
    visibility: Visibility
    status: Literal["published", "hidden"]
    version: int = Field(ge=1, description="content_version")
    items_visible: bool
    items: list[CollectionItemOut] | None = None
    me: MeOut | None = None


class MyCollectionOut(BaseModel):
    """Сборник в «моих сборниках»."""

    collection_id: UUID
    domain: Domain
    kind: Kind
    status: CollectionStatus
    title: str
    visibility: Visibility
    has_draft: bool
    draft_revision: int | None = None
    version: int | None = Field(default=None, description="Опубликованная content_version")
    items_count: int = Field(ge=0)
    likes_count: int = Field(ge=0)
    hidden_by_moderator: bool = Field(description="Плашка «скрыт модератором»")
    updated_at: datetime
    published_at: datetime | None = None


class MyCollectionsPage(BaseModel):
    """Мои сборники (keyset-курсор по ``updated_at``)."""

    items: list[MyCollectionOut]
    next_cursor: str | None = None


class LikeOut(BaseModel):
    """Состояние лайка после запроса (повтор — тот же ответ, D13)."""

    liked: bool
    likes_count: int = Field(ge=0)


class FavoriteOut(BaseModel):
    """Состояние избранного после запроса."""

    favorite: bool


# ----------------------------------------------------------------------------- meta
class MetaLimits(BaseModel):
    """Лимиты конструктора (``GV_COLLECTIONS__LIMITS__*``)."""

    title_max: int
    description_max: int
    items_min: int
    items_max: int
    tags_max: int
    emoji_max_graphemes: int
    drafts_max: int
    publish_per_day: int


class CoverPresetOut(BaseModel):
    """Пресет обложки."""

    id: str
    gradient: list[str] = Field(description="Цвета CSS (#rrggbb) по порядку")
    emoji: str


class TagOut(BaseModel):
    """Тег из словаря (свободных тегов нет)."""

    key: str
    title_key: str
    domains: list[Domain]


class CollectionsMeta(BaseModel):
    """Сведения о сервисе и словари конструктора (``limits``, ``cover_presets``, ``tags``)."""

    service: str
    status: str
    limits: MetaLimits | None = None
    cover_presets: list[CoverPresetOut] | None = None
    tags: list[TagOut] | None = None


# ----------------------------------------------------------------------------- admin
class HiddenOut(BaseModel):
    """Кто и почему скрыл сборник (аудит; автору показывается только факт скрытия)."""

    reason: str
    actor_id: UUID | None = None
    at: datetime


class AdminCollectionOut(BaseModel):
    """Сборник в админке (включая скрытые и черновики)."""

    collection_id: UUID
    domain: Domain
    kind: Kind
    status: CollectionStatus
    visibility: Visibility
    title: str
    author: AuthorOut
    author_restricted: bool
    featured: bool
    featured_rank: int | None = None
    version: int | None = None
    items_count: int = Field(ge=0)
    likes_count: int = Field(ge=0)
    plays_count: int = Field(ge=0)
    difficulty: Difficulty | None = None
    hidden: HiddenOut | None = None
    created_at: datetime
    updated_at: datetime
    published_at: datetime | None = None


class AdminCollectionDetail(AdminCollectionOut):
    """Карточка в админке с составом и текстами."""

    description: str | None = None
    tags: list[str]
    cover: CoverOut | None = None
    default_mode_key: str | None = None
    draft_revision: int | None = None
    items: list[CollectionItemOut]


class AdminCollectionPage(BaseModel):
    """Список в админке."""

    items: list[AdminCollectionOut]
    next_cursor: str | None = None


class CuratedCollectionIn(_Request):
    """Кураторский сборник из YAML-редактора админки (D14): публикуется сразу.

    Автор отображается как «Guessverse» (``kind = curated``, ``author_id = null``);
    единицы — по ``media_item_id``, проверяются как при публикации UGC.
    """

    domain: Domain
    title: TitleIn
    description: DescriptionIn | None = None
    tags: list[TagKey] = Field(default_factory=list, max_length=20)
    cover: Cover | None = None
    default_mode_key: CollectionModeKey
    visibility: Visibility = "public"
    items: list[DraftItem] = Field(min_length=1, max_length=MAX_ITEMS)
    reason: Reason


class CuratedCollectionUpdate(_Request):
    """Новая версия кураторского сборника (оптимистичная блокировка по ``base_version``)."""

    base_version: int = Field(ge=1)
    title: TitleIn
    description: DescriptionIn | None = None
    tags: list[TagKey] = Field(default_factory=list, max_length=20)
    cover: Cover | None = None
    default_mode_key: CollectionModeKey
    visibility: Visibility = "public"
    items: list[DraftItem] = Field(min_length=1, max_length=MAX_ITEMS)
    reason: Reason


class ReasonRequest(_Request):
    """Действие модератора с причиной (аудит)."""

    reason: Reason


class FeaturedRequest(_Request):
    """Отметка «рекомендуемое» (кураторские и UGC, D14)."""

    featured: bool
    rank: int | None = Field(
        default=None, ge=1, le=1000, description="Порядок в блоке, меньше — выше"
    )
    reason: Reason


class RestrictionOut(BaseModel):
    """Ограничение автора: не может создавать, править и публиковать сборники."""

    user_id: UUID
    restricted: bool
    reason: str | None = None
    actor_id: UUID | None = None
    at: datetime | None = None
