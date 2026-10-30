"""Схемы публичного API bff_web (план этапа 2, §2.6; этап 3a, §2.5; этап 3b, §2.5) — экраны
веб-клиента.

bff только читает (D4): агрегирует game, identity и catalog. Блок упавшего сервиса
отдаётся как ``{"status": "unavailable"}`` — страница рендерится без него. Подачу (формат
ответа, вид итога, выбор перед стартом) фронтенд выбирает по данным (D12), не по ключу режима.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, JsonValue

Domain = Literal["music", "film"]
AnswerFormat = Literal["classic", "pick"]
Presentation = Literal["score", "streak", "bracket"]


class Block[T](BaseModel):
    """Блок страницы: данные или признак недоступности источника."""

    status: Literal["ok", "unavailable"]
    data: T | None = None


# ----------------------------------------------------------------------------- субъект
class Subject(BaseModel):
    """Кто смотрит страницу."""

    kind: Literal["user", "guest"]
    display_name: str | None = None


class SessionInfo(BaseModel):
    """``GET /v1/session``: ``subject = null`` — нет токена или он недействителен."""

    subject: Subject | None


# ----------------------------------------------------------------------------- карточки
class PresetCard(BaseModel):
    """Пресет случайного режима."""

    id: str
    title_key: str


class SubjectPickCard(BaseModel):
    """Выбор персон перед стартом (марафон): ``GET game /v1/modes/{key}/subjects``."""

    roles: list[str]
    max: int = Field(ge=1)


class ModeStartCard(BaseModel):
    """Что игрок выбирает перед стартом режима."""

    subject: SubjectPickCard | None = None


class ModeCard(BaseModel):
    """Карточка режима (из ``game /v1/modes``)."""

    key: str
    domain: Domain
    title_key: str
    source_type: str
    clue_type: str
    rounds: int = Field(description="Число раундов; у марафона и пары — максимум")
    allow_guest: bool
    presets: list[PresetCard] | None = None
    answer_format: AnswerFormat = Field(default="classic", description="Реестр ответов фронта")
    presentation: Presentation = Field(default="score", description="Реестр итогов фронта")
    start: ModeStartCard | None = Field(default=None, description="null — выбирать нечего")


class ModeGroup(BaseModel):
    """Группа витрины: режимы с одинаковыми ``source_type`` и ``presentation``."""

    source_type: str
    presentation: Presentation
    modes: list[ModeCard]


class DailyCard(BaseModel):
    """Карточка планового режима периода + статус субъекта (``game /v1/modes`` + ``/v1/daily``).

    Дейлик — период «день», недельная сетка — «неделя»; ``day`` — начало периода,
    ``next_reset_at`` — его конец.
    """

    mode_key: str
    domain: Domain
    title_key: str
    day: date
    status: Literal["not_started", "in_progress", "finished"]
    session_id: UUID | None = None
    score: int | None = None
    max_score: int
    next_reset_at: datetime
    presentation: Presentation = "score"


class RecordCard(BaseModel):
    """Личный рекорд режима (``game /v1/me/records``)."""

    mode_key: str
    domain: Domain
    title_key: str
    presentation: Presentation
    best: int
    percentile: float | None = Field(default=None, ge=0, le=100)
    achieved_at: datetime


# ----------------------------------------------------------------------------- progression (5a)
class StreakCard(BaseModel):
    """Серия дейлика по домену."""

    domain: Domain
    current: int = Field(ge=0)
    best: int = Field(ge=0)


class BoostCard(BaseModel):
    """Действующий буст опыта."""

    multiplier: float = Field(gt=1)
    matches_left: int = Field(ge=1)


class ProgressCard(BaseModel):
    """Уровень и опыт игрока (progression ``/v1/me/progress``)."""

    level: int = Field(ge=1)
    max_level: int = Field(ge=1)
    total_xp: int = Field(ge=0)
    xp_in_level: int = Field(ge=0)
    xp_to_next: int | None = Field(ge=1, description="null — высший уровень")
    solo_xp_today: int = Field(ge=0, description="Опыт из соло за сегодня")
    solo_xp_cap: int = Field(ge=0, description="Суточный потолок опыта из соло")
    streaks: list[StreakCard]
    boost: BoostCard | None = None


class WalletCard(BaseModel):
    """Версы игрока (progression ``/v1/me/wallet``)."""

    earned: int = Field(ge=0)
    paid: int = Field(ge=0)
    total: int = Field(ge=0)


class QuestCard(BaseModel):
    """Задание текущего периода."""

    key: str
    title: str
    description: str
    target: int = Field(ge=1)
    progress: int = Field(ge=0)
    completed: bool
    reward_xp: int = Field(ge=0)
    reward_verses: int = Field(ge=0)


class QuestsCard(BaseModel):
    """Задания игрока: дневные и недельные."""

    daily: list[QuestCard]
    weekly: list[QuestCard]


class BadgeCard(BaseModel):
    """Бейдж игрока."""

    key: str
    title: str
    description: str
    target: int = Field(ge=1)
    progress: int = Field(ge=0)
    unlocked_at: datetime | None = None


PrizeKind = Literal["xp", "verses", "boost", "item", "none"]


class PrizeCard(BaseModel):
    """Приз сектора колеса: тип и параметры из конфига progression."""

    kind: PrizeKind
    amount: int = Field(ge=0)
    multiplier: float | None = None
    matches: int | None = None
    item_key: str | None = None


class WheelSectorCard(BaseModel):
    """Сектор колеса: название, шанс и приз — из ответа progression."""

    key: str
    title: str
    chance: float = Field(ge=0, le=1)
    prize: PrizeCard


class SpinCard(BaseModel):
    """Сегодняшний результат спина (если крутили)."""

    day: date
    sector_key: str
    prize: PrizeCard
    spun_at: datetime
    repeated: bool


class WheelCard(BaseModel):
    """Состояние колеса на сегодня (progression ``/v1/wheel``)."""

    enabled: bool
    can_spin: bool
    next_spin_at: datetime | None = Field(description="null — можно крутить")
    sectors: list[WheelSectorCard]
    today: SpinCard | None = Field(default=None, description="null — сегодня не крутили")


class HomeProgressCard(BaseModel):
    """Свёртка прогресса для главной: уровень, серия, колесо."""

    level: int = Field(ge=1)
    xp_in_level: int = Field(ge=0)
    xp_to_next: int | None = Field(ge=1)
    best_streak: int = Field(ge=0, description="Наибольшая текущая серия дейлика по доменам")
    can_spin: bool
    quests_open: int = Field(ge=0, description="Невыполненных заданий на сегодня")


class CosmeticCard(BaseModel):
    """Предмет косметики (progression, 5b): картинка или анимация по ``media_kind``."""

    id: UUID
    key: str
    slot: str
    rarity: str
    title: str
    media_kind: Literal["static", "animated"]
    asset_url: str
    poster_url: str | None = Field(description="Статичный кадр для prefers-reduced-motion")
    level_required: int | None = None
    price: int | None = None


class AppearanceSlotCard(BaseModel):
    """Слот оформления: предмет или пусто."""

    slot: str
    item: CosmeticCard | None = None


class AppearanceCard(BaseModel):
    """Оформление профиля: по одному предмету на слот (ник и аватар берёт фронт из блока игрока)."""

    slots: list[AppearanceSlotCard]
    custom_avatar_url: str | None = Field(
        default=None, description="Одобренная своя аватарка; null — аватар из слота"
    )
    custom_avatar_asset_id: UUID | None = Field(
        default=None,
        description=(
            "Ассет одобренной аватарки: объект жалобы `avatar` (ADR 0069); null — аватарки нет "
            "или источник не отдал"
        ),
    )


class PlayerAppearanceCard(BaseModel):
    """Оформление игрока в матче: ник, аватар, рамка, титул (по ``user_id``)."""

    user_id: UUID
    appearance: AppearanceCard


class InventoryEntryCard(BaseModel):
    """Предмет инвентаря."""

    item: CosmeticCard
    source: str
    equipped: bool
    expires_at: datetime | None = None


class InventoryCard(BaseModel):
    """Инвентарь игрока."""

    items: list[InventoryEntryCard]


class AvatarRequestCard(BaseModel):
    """Последняя заявка на свою аватарку."""

    status: Literal["pending", "approved", "rejected"]
    submitted_at: datetime
    reason_code: str | None = None


class AvatarCard(BaseModel):
    """Своя аватарка: право, заявка, лимиты загрузки."""

    entitled: bool
    request: AvatarRequestCard | None = None
    max_bytes: int = Field(ge=1)
    formats: list[str]


class ShopEntryCard(BaseModel):
    """Предмет витрины с состоянием для игрока."""

    item: CosmeticCard
    state: Literal["locked", "available", "owned"]


class ShopCatalogCard(BaseModel):
    """Витрина: предметы и баланс (покупка — прямо в progression, bff только читает)."""

    items: list[ShopEntryCard]
    balance: int = Field(ge=0)
    slots: list[str] = Field(description="Слоты в порядке конфига — для вкладок витрины")


class PackCard(BaseModel):
    """Пак косметики (виден только при ``billing.enabled=true``)."""

    id: UUID
    key: str
    title: str
    style: str
    items: list[CosmeticCard]
    verses_amount: int = Field(ge=0)
    price_kopecks: int = Field(ge=1)


class ShopBlocks(BaseModel):
    """Блоки витрины."""

    catalog: Block[ShopCatalogCard]
    packs: Block[list[PackCard]] | None = Field(
        default=None, description="null — продажа выключена (billing.enabled=false)"
    )


class ShopPage(BaseModel):
    """``GET /v1/pages/shop``: только пользователь с аккаунтом, `private, no-store`."""

    blocks: ShopBlocks


class ThemeCard(BaseModel):
    """Активная тема оформления (ADR 0049): только ключ, наборы токенов лежат во фронтенде."""

    key: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=64)


class BellCard(BaseModel):
    """Колокольчик: число непрочитанных уведомлений."""

    unread: int = Field(ge=0)


class EventBannerCard(BaseModel):
    """Активное сезонное событие для шапки и главной (5d, ADR 0054)."""

    key: str
    title: str
    ends_at: datetime
    found: int | None = Field(default=None, ge=0, description="Найдено игроком; null - аноним")
    total: int | None = Field(default=None, ge=0, description="Всего находок; null - аноним")


class EventArchiveCard(BaseModel):
    """Запуск события в архиве игрока (5d)."""

    key: str
    title: str
    status: Literal["active", "finished", "stopped"]
    starts_at: datetime
    ends_at: datetime
    found: int = Field(ge=0)
    total: int = Field(ge=0)
    completed: bool


class EventRewardCard(BaseModel):
    """Основная награда события."""

    item_key: str
    title: str
    image_url: str


class EventFindRewardCard(BaseModel):
    """Награда находки."""

    xp: int = Field(ge=0)
    verses: int = Field(ge=0)


class EventFindTriggerCard(BaseModel):
    """Условие открытия находки; у закрытой финальной `progress = null`."""

    type: Literal["server", "ui_spot"]
    page_key: str | None = None
    metric: str | None = None
    progress: int | None = None
    target: int | None = None
    period: Literal["day", "event"] | None = None


class EventFindCard(BaseModel):
    """Находка события с состоянием для игрока."""

    key: str
    number: int = Field(ge=1, le=20)
    title: str
    x_pct: float = Field(ge=0, le=100)
    y_pct: float = Field(ge=0, le=100)
    final: bool
    state: Literal["locked", "open", "found"]
    diary: str
    reward: EventFindRewardCard
    play_page: str | None = None
    trigger: EventFindTriggerCard
    found_at: datetime | None = None
    task_i18n: str | None = Field(
        default=None,
        description="Ключ i18n текста задания («что сделать»); null - задания нет",
    )


class EventPageView(BaseModel):
    """``GET /v1/pages/events/{key}``: страница события + тема (5d). Только пользователь."""

    key: str
    title: str
    description: str
    theme_key: str
    theme: ThemeCard
    status: Literal["active", "finished", "stopped"]
    starts_at: datetime
    ends_at: datetime
    map_url: str | None = None
    found: int = Field(ge=0)
    total: int = Field(ge=0)
    completed: bool
    reward_item: EventRewardCard | None = None
    finds: list[EventFindCard]


class ShellPage(BaseModel):
    """``GET /v1/pages/shell``: оболочка приложения — тема и колокольчик."""

    theme: ThemeCard
    bell: Block[BellCard] | None = Field(
        default=None, description="null — аноним или гость, notifications не вызывается"
    )
    sanctions: Block[SanctionsCard] | None = Field(
        default=None, description="5c: действующие санкции; null — аноним или гость"
    )
    event: EventBannerCard | None = Field(
        default=None,
        description="5d: активное событие; null - нет события или progression недоступен",
    )


class ProfileBlocks(BaseModel):
    """Блоки профиля: каждый гаснет независимо."""

    progress: Block[ProgressCard]
    wallet: Block[WalletCard]
    quests: Block[QuestsCard]
    badges: Block[list[BadgeCard]]
    wheel: Block[WheelCard]
    appearance: Block[AppearanceCard] | None = Field(
        default=None, description="Оформление; null до потока S5 этапа 5b"
    )
    inventory: Block[InventoryCard] | None = Field(
        default=None, description="Инвентарь; null до потока S5 этапа 5b"
    )
    avatar: Block[AvatarCard] | None = Field(
        default=None, description="Своя аватарка; null до потока S5 этапа 5b"
    )
    events: list[EventArchiveCard] = Field(
        default_factory=list, description="5d: архив событий игрока (новые первыми); пусто - нет"
    )


class ProfilePage(BaseModel):
    """``GET /v1/pages/profile``: только пользователь с аккаунтом, ответ ``private, no-store``."""

    blocks: ProfileBlocks


class BadgeRefCard(BaseModel):
    """Полученный бейдж в публичном профиле игрока."""

    key: str
    title: str
    unlocked_at: datetime


class PublicProgressCard(BaseModel):
    """Публичная часть прогресса игрока: уровень, опыт, бейджи."""

    user_id: UUID
    level: int = Field(ge=1)
    total_xp: int = Field(ge=0)
    badges: list[BadgeRefCard]


class PlayerSocialCard(BaseModel):
    """Отношение зрителя к игроку (social ``/v1/players/{id}/relation``, 5c)."""

    is_friend: bool
    can_send_request: bool
    blocked_by_me: bool


class PlayerBlocks(BaseModel):
    """Блоки профиля игрока."""

    progress: Block[PublicProgressCard]
    appearance: Block[AppearanceCard] | None = Field(
        default=None, description="Оформление игрока; null до потока S5 этапа 5b"
    )
    social: Block[PlayerSocialCard] | None = Field(
        default=None, description="Друзья и блокировка; null — не вызывалось (5c, поток F5)"
    )


class PlayerPage(BaseModel):
    """``GET /v1/pages/players/{user_id}``: только пользователь с аккаунтом, `private, no-store`."""

    blocks: PlayerBlocks
    nickname: str | None = Field(
        default=None,
        description=(
            "Ник игрока из social (ADR 0069); null — профиль скрыт, свой профиль или social "
            "недоступен: фронт показывает нейтральное «Игрок»"
        ),
    )
    restricted: bool = Field(
        default=False,
        description=(
            "5c: профиль скрыт, игрок заблокировал зрителя или неизвестен — показаны только ник, "
            "аватар и оформление, `progress` = `unavailable`; ответы неразличимы (нет перечисления)"
        ),
    )


class FriendCard(BaseModel):
    """Друг в блоке «друзья» (только владельцу)."""

    user_id: UUID
    nickname: str
    status: Literal["online", "in_match", "offline"]


class FriendsCard(BaseModel):
    """Блок друзей для хаба онлайна и лобби (приглашение в комнату)."""

    items: list[FriendCard]
    incoming_requests: int = Field(ge=0)


class CommentCard(BaseModel):
    """Комментарий к сборнику."""

    id: UUID
    author_id: UUID
    author_nickname: str
    body: str
    created_at: datetime
    edited_at: datetime | None
    can_edit: bool
    can_delete: bool


class CommentsCard(BaseModel):
    """Первая страница комментариев сборника; дальше - social ``/v1/collections/{id}/comments``."""

    items: list[CommentCard]
    total: int = Field(ge=0)
    next_cursor: str | None


class SanctionCard(BaseModel):
    """Действующая санкция игрока."""

    kind: Literal["ranked", "chat", "online", "account"]
    expires_at: datetime | None = Field(description="null - бессрочно")
    reason: str


class SanctionsCard(BaseModel):
    """Блок санкций оболочки (экран санкции); пусто - ограничений нет."""

    items: list[SanctionCard]


class FriendDailyResult(BaseModel):
    """Результат друга в дейлике одного домена (бейдж на домен)."""

    domain: Domain
    mode_key: str
    day: date = Field(description="Начало периода набора")
    score: int = Field(ge=0)
    max_score: int = Field(ge=0)


class FriendDailyCard(BaseModel):
    """Друг, сыгравший дейлик в текущем периоде: до одного результата на домен."""

    user_id: UUID
    nickname: str
    results: list[FriendDailyResult]
    appearance: AppearanceCard | None = Field(
        default=None, description="Аватар, рамка, титул; null — источник не отдал"
    )


class DailyFeedCard(BaseModel):
    """Лента дейлика друзей: результаты и счётчик «сыграли из всех друзей»."""

    friends_total: int = Field(ge=0, description="Всего друзей")
    friends_played: int = Field(
        ge=0,
        description=(
            "Сыграли дейлик в текущем периоде и открыли статистику (друг со скрытой "
            "статистикой не считается и не показывается); «друзья 4 из 9» = played из total"
        ),
    )
    items: list[FriendDailyCard] = Field(
        description="Лучшие первыми, не больше `GV_PAGES__HOME_FEED_ITEMS_LIMIT`"
    )


class HomeBlocks(BaseModel):
    """Блоки главной."""

    dailies: Block[list[DailyCard]] = Field(description="Наборы дня (без недельной сетки)")
    modes: Block[list[ModeCard]]
    weekly: Block[list[DailyCard]] = Field(description="Наборы недели (сетка)")
    progress: Block[HomeProgressCard] | None = Field(
        default=None, description="Прогресс игрока; null — гость или аноним"
    )
    event: EventBannerCard | None = Field(
        default=None,
        description="5d: активное событие; null - нет события или progression недоступен",
    )
    feed: Block[DailyFeedCard] | None = Field(
        default=None,
        description=(
            "Друзья и дейлик (результаты по доменам, счётчик «сыграли из всех»); null — гость "
            "или аноним, источники не вызывались; unavailable — сбой любой части"
        ),
    )


class HomePage(BaseModel):
    """``GET /v1/pages/home``."""

    blocks: HomeBlocks


class SoloBlocks(BaseModel):
    """Блоки хаба соло-режимов."""

    daily: Block[list[DailyCard]] = Field(description="Наборы дня")
    random: Block[list[ModeCard]] = Field(description="Случайные режимы (витрина этапа 2)")
    modes: Block[list[ModeGroup]] = Field(
        description="Витрина: неплановые режимы домена по группам (source_type, presentation)"
    )
    weekly: Block[list[DailyCard]] = Field(description="Наборы недели (сетка)")
    records: Block[list[RecordCard]] | None = Field(
        default=None, description="Личные рекорды; null — гость или аноним"
    )


class SoloPage(BaseModel):
    """``GET /v1/pages/solo``."""

    blocks: SoloBlocks


# ----------------------------------------------------------------------------- итог
class PersonCard(BaseModel):
    """Персона в карточке единицы."""

    name: str
    role: str


class ExternalLink(BaseModel):
    """Ссылка на внешний сервис (TMDB, MusicBrainz …)."""

    kind: str
    url: str


class ItemCard(BaseModel):
    """Карточка единицы из ``catalog /v1/items/{id}``."""

    media_item_id: UUID
    domain: Domain
    kind: str
    title: str
    original_title: str | None = None
    year: int | None = None
    genres: list[str]
    persons: list[PersonCard]
    external_links: list[ExternalLink] = Field(default_factory=list)


class ResultRoundCard(BaseModel):
    """Раунд итога (из ``game /v1/sessions/{id}/result``)."""

    index: int
    outcome: Literal["exact", "partial", "miss", "gave_up", "void"]
    level: int | None
    points: int
    media_item_id: UUID | None = Field(description="null у раунда void")
    exact_pct: float | None = Field(
        default=None, ge=0, le=100, description="Доля угадавших (только дейлик)"
    )


class ResultRecord(BaseModel):
    """Рекорд в итоге (только пользователи, режимы с рекордами)."""

    best: int
    is_new: bool
    percentile: float | None = Field(default=None, ge=0, le=100)


class BracketPick(BaseModel):
    """Выбор в сетке: тур и названия победителя и проигравшего."""

    stage: int
    winner: str
    loser: str


class BracketSummary(BaseModel):
    """Итог сетки: чемпион и выборы по турам."""

    champion: str
    picks: list[BracketPick]


class ResultSummary(BaseModel):
    """Итог сессии для экрана результатов."""

    session_id: UUID
    mode_key: str | None = None
    score: int
    max_score: int
    share_text: str
    rounds: list[ResultRoundCard]
    presentation: Presentation = Field(default="score", description="Реестр итогов фронта")
    record: ResultRecord | None = Field(default=None, description="null — гость или без рекордов")
    bracket: BracketSummary | None = Field(default=None, description="Только у сетки")


class RewardStreakCard(BaseModel):
    """Серия дейлика, в которую засчитана сессия."""

    domain: Domain
    current: int = Field(ge=1)


class RewardsCard(BaseModel):
    """Награды за игру. `settled = false` — ещё не начислено (асинхронно): повторить позже."""

    settled: bool
    xp: int = Field(ge=0, description="Опыт за сессию (с бустом)")
    verses: int = Field(ge=0, description="Версы за сессию, целые")
    level_before: int | None = Field(default=None, ge=1, description="Уровень до сессии")
    level_after: int | None = Field(default=None, ge=1, description="Уровень после сессии")
    leveled_up: bool = Field(description="Уровень вырос")
    streak: RewardStreakCard | None = Field(
        default=None, description="Серия дейлика на день сессии; null — не дейлик"
    )


class FriendsTodayRow(BaseModel):
    """Друг и его результат в том же дейлике."""

    user_id: UUID
    nickname: str
    score: int = Field(ge=0)
    max_score: int = Field(ge=0)
    appearance: AppearanceCard | None = None


class FriendsTodayCard(BaseModel):
    """«Среди друзей» итога дейлика: тот же режим и период, свой результат в сравнении."""

    mode_key: str
    domain: Domain
    day: date
    my_score: int = Field(ge=0)
    my_max_score: int = Field(ge=0)
    my_place: int = Field(ge=1, description="Место среди сыгравших друзей и вас; равные делят")
    friends_total: int = Field(ge=0)
    friends_played: int = Field(ge=0, description="Сыграли этот дейлик и открыли статистику")
    average_score: float | None = Field(
        default=None, description="Среднее очков сыгравших друзей; null — таких нет"
    )
    items: list[FriendsTodayRow] = Field(
        description="Лучшие первыми, не больше `GV_PAGES__RESULTS_FRIENDS_LIMIT`"
    )


class ResultsBlocks(BaseModel):
    """Блоки экрана результатов."""

    result: Block[ResultSummary]
    items: Block[list[ItemCard]]
    rewards: Block[RewardsCard] | None = Field(
        default=None, description="Награды за игру; null — гость или аноним"
    )
    friends_today: Block[FriendsTodayCard] | None = Field(
        default=None,
        description=(
            "Только итог дейлика текущего периода: друзья в том же режиме; null — гость, аноним, "
            "не дейлик или период закончился"
        ),
    )


class ResultsPage(BaseModel):
    """``GET /v1/pages/results/{session_id}``."""

    blocks: ResultsBlocks


# ----------------------------------------------------------------------------- сборники (3b)
CollectionSort = Literal["featured", "popular", "new", "likes", "difficulty"]
Difficulty = Literal["easy", "medium", "hard"]
RoundsChoice = Literal[5, 10, 25, "all"]


class CollectionAuthor(BaseModel):
    """Автор сборника (``collections``): пользователь, «Guessverse» или удалённый."""

    kind: Literal["user", "curator", "deleted"]
    user_id: UUID | None = None
    nickname: str | None = None


class CollectionCover(BaseModel):
    """Обложка — пресет (градиент и эмодзи из ``collections /v1/meta``)."""

    preset: str


class CollectionCard(BaseModel):
    """Карточка сборника в каталоге (``collections /v1/collections``), без состава."""

    collection_id: UUID
    domain: Domain
    kind: Literal["ugc", "curated"]
    title: str
    cover: CollectionCover | None = None
    tags: list[str]
    author: CollectionAuthor
    default_mode_key: str
    items_count: int = Field(ge=0)
    likes_count: int = Field(ge=0)
    plays_count: int = Field(ge=0)
    difficulty: Difficulty | None = None
    featured: bool = False
    published_at: datetime


class CollectionResults(BaseModel):
    """Страница карточек по курсору; курсор привязан к сортировке блока."""

    items: list[CollectionCard]
    next_cursor: str | None = Field(
        default=None,
        description=(
            "Продолжение: `GET /v1/pages/collections?sort=<сортировка блока>&cursor=…` "
            "(с теми же фильтрами) вернёт блок `results`; null — дальше пусто"
        ),
    )


class CollectionsBlocks(BaseModel):
    """Блоки каталога: без ``q`` и ``cursor`` — витрины, иначе только ``results``.

    Отсутствующий в этом виде страницы блок — ``null``.
    """

    featured: Block[CollectionResults] | None = Field(
        default=None, description="Рекомендуемые (sort=featured)"
    )
    popular: Block[CollectionResults] | None = Field(
        default=None, description="Популярные (sort=popular)"
    )
    new: Block[CollectionResults] | None = Field(default=None, description="Новые (sort=new)")
    results: Block[CollectionResults] | None = Field(
        default=None, description="Поиск (`q`) или продолжение по `cursor` в сортировке `sort`"
    )


class CollectionsPage(BaseModel):
    """``GET /v1/pages/collections``."""

    blocks: CollectionsBlocks


class CustomClueCard(BaseModel):
    """Своя загадка единицы (эмодзи); приходит только автору и админу."""

    emoji: str | None = None


class CollectionItemCard(BaseModel):
    """Единица состава: только если состав открыт субъекту (D6)."""

    position: int = Field(ge=0)
    media_item_id: UUID
    title: str
    year: int | None = None
    available: bool
    custom: CustomClueCard | None = Field(default=None, description="Только автору")


class CollectionDetail(CollectionCard):
    """Сборник целиком (``collections /v1/collections/{id}``)."""

    description: str | None = None
    visibility: Literal["public", "unlisted"]
    status: Literal["published", "hidden"]
    version: int = Field(ge=1)
    items_visible: bool
    items: list[CollectionItemCard] | None = None


class CollectionModeCard(BaseModel):
    """Режим сборника (``game /v1/collections/{id}/modes``)."""

    mode_key: str
    title_key: str
    clue_type: str
    allow_guest: bool
    playable: int = Field(ge=0)
    total: int = Field(ge=0)
    available: bool
    round_options: list[RoundsChoice]


class CollectionMe(BaseModel):
    """Отношение пользователя к сборнику; гостю и анониму — ``me: null``."""

    liked: bool
    favorite: bool
    completed: bool
    is_author: bool = False


class CollectionBlocks(BaseModel):
    """Блоки страницы сборника: недоступный game даёт ``modes: unavailable``."""

    collection: Block[CollectionDetail]
    modes: Block[list[CollectionModeCard]]
    comments: Block[CommentsCard] | None = Field(
        default=None, description="5c: комментарии (social); null — не вызывалось"
    )


class CollectionPage(BaseModel):
    """``GET /v1/pages/collections/{id}``."""

    blocks: CollectionBlocks
    me: CollectionMe | None = None


# ----------------------------------------------------------------------------- медиатека (3b)
class ShelfCard(BaseModel):
    """Полка (``library /v1/shelves``)."""

    ref: str
    shelf_id: UUID | None = None
    kind: Literal["system", "custom"]
    key: str | None = None
    title: str | None = None
    title_key: str | None = None
    domains: list[Domain]
    exclusive_with: list[str] = Field(default_factory=list)
    items_count: int = Field(ge=0)
    updated_at: datetime | None = None


class ShelfItemCard(BaseModel):
    """Единица на полке: карточка из проекции catalog в library."""

    media_item_id: UUID
    domain: Domain
    kind: str
    title: str
    year: int | None = None
    persons: list[str] = Field(default_factory=list)
    status: Literal["available", "unavailable"]
    source: Literal["reveal", "results", "card", "collection", "library"]
    added_at: datetime


class LibraryBlocks(BaseModel):
    """Блоки медиатеки."""

    shelves: Block[list[ShelfCard]]


class LibraryPage(BaseModel):
    """``GET /v1/pages/library`` (только пользователь)."""

    blocks: LibraryBlocks


class ShelfItems(BaseModel):
    """Полка и её единицы (страница по курсору)."""

    shelf: ShelfCard
    items: list[ShelfItemCard]
    next_cursor: str | None = None


class LibraryShelfBlocks(BaseModel):
    """Блоки страницы полки."""

    shelf: Block[ShelfItems]


class LibraryShelfPage(BaseModel):
    """``GET /v1/pages/library/{ref}`` (только пользователь)."""

    blocks: LibraryShelfBlocks


# ----------------------------------------------------------------------------- онлайн (4a)
MatchKind = Literal["duel", "party", "lobby"]
MatchFinish = Literal["completed", "forfeit", "tech_win", "aborted"]
PlayerFinish = Literal["completed", "forfeit", "left", "tech_win", "tech_loss"]
DuelOutcome = Literal["win", "loss", "draw"]


class MatchModeCard(BaseModel):
    """Шаблон режима комнаты или матча (``match ModeRef``)."""

    key: str
    version: int = Field(ge=1)
    domain: Domain
    kind: MatchKind
    title_key: str
    clue_type: str
    answer_format: str
    ranked: bool = False


class RoomSettingsCard(BaseModel):
    """Действующие настройки комнаты (шаблон + переопределения хоста)."""

    rounds: int = Field(ge=1)
    level_window_s: int = Field(ge=1, description="Окно уровня, с")
    answer_format: str
    clue_type: str
    players_max: int = Field(ge=2)
    visibility: Literal["public", "private"]
    collection_ids: list[UUID] = Field(default_factory=list)


class OpenRoomCard(BaseModel):
    """Открытое лобби (``match /v1/rooms/open``): вход — по ``code`` напрямую в match."""

    code: str = Field(pattern=r"^[A-HJ-NP-Z2-9]{8}$")
    mode: MatchModeCard
    host_nickname: str
    players: int = Field(ge=0)
    players_max: int = Field(ge=2)
    settings: RoomSettingsCard
    created_at: datetime


class OpenRoomsList(BaseModel):
    """Открытые лобби, новые первыми; ``next_cursor`` — для ``match /v1/rooms/open``."""

    items: list[OpenRoomCard]
    next_cursor: str | None = None


class MatchParticipantCard(BaseModel):
    """Другой игрок матча в карточке истории (для жалобы)."""

    user_id: UUID
    nickname: str
    can_report: bool


class MyMatchCard(BaseModel):
    """Матч в «Моих матчах» (``match /v1/me/matches``)."""

    match_id: UUID
    mode: MatchModeCard
    finished_at: datetime
    finish: MatchFinish
    players: int = Field(ge=1)
    place: int = Field(ge=1)
    score: int = Field(ge=0)
    outcome: DuelOutcome | None = Field(description="Только у дуэли")
    my_finish: PlayerFinish
    participants: list[MatchParticipantCard] = Field(
        default_factory=list,
        description=(
            "ADR 0069: другие игроки матча для жалобы из истории; пусто — игроки не получены "
            "(источник недоступен, лимит `online_match_players_limit` исчерпан)"
        ),
    )


class MyMatchesList(BaseModel):
    """Мои последние матчи; ``next_cursor`` — для ``match /v1/me/matches``."""

    items: list[MyMatchCard]
    next_cursor: str | None = None


class TierCard(BaseModel):
    """Звание ladder-а (ключи i18n из конфига rating)."""

    key: str
    family: str
    step: int | None = Field(ge=1, description="Градация внутри звания; null — без градации")
    title_key: str


class QueuePoolCard(BaseModel):
    """Очередь ladder-а и формата: сколько игроков ищет матч (ADR 0038)."""

    ladder: str
    format: Literal["classic", "lite"]
    searching: int = Field(ge=0)


class RatingCard(BaseModel):
    """Рейтинг пользователя в ladder-е; в калибровке `rating` и `tier` — null (ADR 0037)."""

    ladder: str
    rating: int | None
    tier: TierCard | None
    next_tier_at: int | None = None
    calibrating: bool
    calibration_left: int = Field(ge=0, description="«Осталось N матчей»")
    matches_played: int = Field(ge=0)
    place: int | None = Field(default=None, ge=1)


class SeasonCard(BaseModel):
    """Действующий сезон ladder-а."""

    ladder: str
    number: int = Field(ge=1)
    starts_at: datetime
    ends_at: datetime = Field(description="«Осталось X д.» считает клиент")


class WeeklyEntryCard(BaseModel):
    """Строка топа недели."""

    place: int = Field(ge=1)
    user_id: UUID
    nickname: str
    delta: int


class WeeklyTopCard(BaseModel):
    """Топ недели ladder-а (прирост с понедельника, ADR 0039)."""

    ladder: str
    week_start: date
    items: list[WeeklyEntryCard]
    me: WeeklyEntryCard | None = None


class LadderCard(BaseModel):
    """Ladder для переключателя на странице лидербордов."""

    key: str
    domain: Domain
    formats: list[Literal["classic", "lite"]]
    season: SeasonCard | None = None
    tiers: list[TierCard]


class LeaderboardEntryCard(BaseModel):
    """Строка лидерборда."""

    place: int = Field(ge=1)
    user_id: UUID
    nickname: str
    rating: int
    tier: TierCard
    matches_played: int = Field(ge=0)


class LeaderboardCard(BaseModel):
    """Страница лидерборда; `next_cursor` — для `rating /v1/ladders/{key}/leaderboard`."""

    ladder: str
    items: list[LeaderboardEntryCard]
    next_cursor: str | None = None
    me: LeaderboardEntryCard | None = None


class LeaderboardsBlocks(BaseModel):
    """Блоки страницы лидербордов; каждый деградирует независимо."""

    ladders: Block[list[LadderCard]]
    leaderboard: Block[LeaderboardCard]
    weekly: Block[WeeklyTopCard]


class LeaderboardsPage(BaseModel):
    """``GET /v1/pages/leaderboards``: публичная страница, кэш 10 с (с ``around=me`` — личная)."""

    blocks: LeaderboardsBlocks


class OnlineChatCard(BaseModel):
    """Эмоции и готовые фразы (ключи i18n ``match.emotes.*``/``match.phrases.*``)."""

    emotes: list[str]
    phrases: list[str]
    free_text: bool = Field(description="Свободный чат — только кастомное лобби (ADR 0036)")


class OnlineRulesCard(BaseModel):
    """Правила комнаты онлайн-режима (``game ModeOut.online``, ADR 0032)."""

    kind: MatchKind
    players_min: int = Field(ge=2)
    players_max: int = Field(ge=2)
    rounds: int = Field(ge=1)
    level_windows_s: list[int] = Field(description="Окно каждого уровня загадки, с")
    contest_type: str = Field(description="Плагин состязания (`race`, `parallel`)")
    ranked: bool
    chat: OnlineChatCard
    ladder: str | None = None
    max_collections: int | None = Field(
        default=None, ge=1, description="Только у лобби по сборнику: сколько сборников выбрать"
    )
    lobby: dict[str, JsonValue] | None = Field(
        default=None,
        description="Только у кастомного лобби: `{visibility, overridable}` (LobbySpec конфига)",
    )


class OnlineModeCard(BaseModel):
    """Онлайн-режим для создания комнаты (``game /v1/modes?session=online``)."""

    key: str
    version: int = Field(ge=1)
    domain: Domain
    title_key: str
    source_type: str
    clue_type: str
    answer_format: AnswerFormat
    presentation: Presentation
    online: OnlineRulesCard


class OnlineBlocks(BaseModel):
    """Блоки хаба онлайна: каждый деградирует независимо."""

    open_rooms: Block[OpenRoomsList]
    my_matches: Block[MyMatchesList]
    modes: Block[list[OnlineModeCard]] = Field(
        description="Онлайн-режимы для создания комнаты (`game /v1/modes?session=online`)"
    )
    queues: Block[list[QueuePoolCard]] = Field(
        description="4b: сколько игроков ищет матч (`match /v1/queue/pool`)"
    )
    ratings: Block[list[RatingCard]] = Field(
        description=(
            "4b: мои звания и калибровка (`rating /v1/me/ratings`); в калибровке "
            "`rating`/`tier` — null, «осталось N матчей» — `calibration_left`"
        )
    )
    seasons: Block[list[SeasonCard]] = Field(
        description="4b: действующие сезоны (ladder без сезона пропускается)"
    )
    weekly: Block[list[WeeklyTopCard]] = Field(
        description="4b: топ недели по каждому ladder-у (`rating /v1/ladders/{key}/weekly`)"
    )
    friends: Block[FriendsCard] | None = Field(
        default=None,
        description="5c: друзья со статусом и число входящих заявок (social); null — не вызывалось",
    )


class OnlinePage(BaseModel):
    """``GET /v1/pages/online``: блоки — только при ``status = ok``, иначе ``blocks: null``."""

    status: Literal["ok", "auth_required", "session_expired"] = Field(
        description=(
            "`auth_required` — гость или аноним (экран регистрации, D13); "
            "`session_expired` — токен не прошёл проверку или match ответил 401 "
            "(обновить сессию и повторить)"
        )
    )
    blocks: OnlineBlocks | None = None


class MatchPlayerCard(BaseModel):
    """Игрок в итоге матча: место с ничьими (1, 1, 3), ``outcome`` — только у дуэли."""

    user_id: UUID
    nickname: str
    slot: int = Field(ge=0)
    place: int = Field(ge=1)
    score: int = Field(ge=0)
    outcome: DuelOutcome | None
    finish: PlayerFinish
    can_report: bool = Field(
        default=False,
        description="5c: показывать кнопку жалобы (не сам игрок, матч не соло); решает bff",
    )
    can_add_friend: bool = Field(
        default=False,
        description=(
            "ADR 0069: можно позвать в друзья (social `can_send_request` по участникам пачкой); "
            "social недоступен — false"
        ),
    )


class RoundPlayerResultCard(BaseModel):
    """Результат игрока в раунде."""

    user_id: UUID
    level: int | None = Field(description="Уровень угадывания; null — не угадал")
    partial: bool
    score: int = Field(ge=0)
    elapsed_ms: int | None = Field(default=None, ge=0, description="От открытия уровня")


class MatchRoundCard(BaseModel):
    """Раунд итога: карточка единицы — в блоке ``items`` по ``media_item_id``."""

    index: int = Field(ge=0)
    media_item_id: UUID
    results: list[RoundPlayerResultCard]
    void: bool = Field(default=False, description="Раунд аннулирован (ассет недоступен)")


class MatchSummary(BaseModel):
    """Итог матча (``match /v1/matches/{id}``)."""

    match_id: UUID
    room_code: str
    mode: MatchModeCard
    started_at: datetime
    finished_at: datetime
    finish: MatchFinish
    players: list[MatchPlayerCard]
    rounds: list[MatchRoundCard]
    collection_ids: list[UUID] = Field(default_factory=list)


class ItemShelvesCard(BaseModel):
    """Флаг «в медиатеке»: полки пользователя с единицей (пусто — не сохранена)."""

    media_item_id: UUID
    shelves: list[str]


class MatchBlocks(BaseModel):
    """Блоки экрана итога матча."""

    match: Block[MatchSummary]
    items: Block[list[ItemCard]]
    library: Block[list[ItemShelvesCard]]
    appearance: Block[list[PlayerAppearanceCard]] | None = Field(
        default=None, description="Оформление игроков матча; null до потока S5 этапа 5b"
    )


class MatchPage(BaseModel):
    """``GET /v1/pages/match/{match_id}`` (только пользователь)."""

    blocks: MatchBlocks
