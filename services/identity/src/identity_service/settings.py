"""Настройки identity (переменные окружения ``GV_*``, вложенные — через ``__``).

Значения по умолчанию безопасны для production: Secure-cookie, Яндекс выключен, письма в
лог без ссылок. Секреты (ключи подписи, client secret, пароль SMTP) — только из окружения.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from gv_auth import DEFAULT_LEEWAY_S
from gv_service import ServiceSettings


class JwtSettings(BaseModel):
    """Ключи и время жизни токенов."""

    signing_keys: SecretStr | None = None
    """JSON ``[{"kid", "private_pem", "active", "not_after"?}]``; без него — только local/ci."""
    access_ttl_s: int = Field(default=900, ge=60, le=3600)
    refresh_ttl_s: int = Field(default=2_592_000, ge=3600)
    leeway_s: int = Field(default=DEFAULT_LEEWAY_S, ge=0, le=300)
    """Допуск верификатора по ``exp``; denylist ``sid`` живёт access TTL + leeway."""


class CookieSettings(BaseModel):
    """Параметры cookie сессии."""

    secure: bool = True
    domain: str | None = None
    refresh_path: str = "/api/identity/v1/auth"
    oauth_path: str = "/api/identity/v1/oauth"


class YandexSettings(BaseModel):
    """Яндекс ID (только привязка и вход через привязанный аккаунт)."""

    enabled: bool = False
    client_id: str = ""
    client_secret: SecretStr = SecretStr("")
    redirect_uri: str = ""
    authorize_url: str = "https://oauth.yandex.ru/authorize"
    token_url: str = "https://oauth.yandex.ru/token"  # noqa: S105 — URL, не секрет
    info_url: str = "https://login.yandex.ru/info"


class EmailSettings(BaseModel):
    """Письма."""

    backend: Literal["log", "smtp"] = "log"
    sender: str = "Guessverse <no-reply@guessverse.local>"


class SmtpSettings(BaseModel):
    """SMTP-сервер."""

    host: str = "localhost"
    port: int = 587
    username: str | None = None
    password: SecretStr | None = None
    start_tls: bool = True
    use_tls: bool = False
    """Неявный TLS (порт 465); взаимоисключающ со ``start_tls``."""
    timeout_s: float = Field(default=10.0, gt=0, le=120)

    @model_validator(mode="after")
    def _one_tls_mode(self) -> SmtpSettings:
        if self.use_tls and self.start_tls:
            raise ValueError("GV_SMTP__USE_TLS и GV_SMTP__START_TLS взаимоисключающие")
        return self


class EmailCodeSettings(BaseModel):
    """Одноразовые коды из писем и доверенные устройства (ADR 0041): ``GV_EMAIL_CODE__*``."""

    length: int = Field(default=6, ge=4, le=10)
    ttl_s: int = Field(default=600, ge=60, le=3600)
    max_attempts: int = Field(default=5, ge=1, le=10)
    resend_after_s: int = Field(default=60, ge=10, le=600)
    daily_fail_limit: int = Field(default=20, ge=1)
    """Неудачных вводов в сутки на субъекта; исчерпание — ``auth.code_limit_exceeded``."""
    request_per_ip_per_min: int = Field(default=5, ge=1)
    """Запросов кода (register, login, forgot, resend, password/change) с одного IP в минуту."""
    confirm_per_ip_per_min: int = Field(default=20, ge=1)
    """Вводов кода с одного IP в минуту."""
    hmac_key: SecretStr | None = None
    """Ключ HMAC кода; без него — только local/ci (временный ключ процесса)."""
    device_cookie_ttl_days: int = Field(default=180, ge=1, le=365)
    max_lifetime_s: int = Field(default=3600, ge=300, le=43_200)
    """Абсолютный срок жизни записи кода от ``created_at``: resend не продлевает его дальше.

    Иначе запись удерживали бы живой повторными отправками, пока она не выпадет из суточного
    окна потолка неудач (``failures_since`` считает записи по ``created_at``)."""
    check_concurrency: int = Field(default=3, ge=1, le=16)
    """Одновременных проверок кода на процесс: каждая держит два соединения БД (основное и
    отдельное для списания попытки), пул конечен."""
    purge_interval_s: float = Field(default=3600, gt=0)
    purge_jitter_s: float = Field(default=300, ge=0)
    purge_batch: int = Field(default=1000, ge=1, le=10_000)
    purge_max_batches: int = Field(default=20, ge=1)
    purge_retention_s: int = Field(default=172_800, ge=86_400)
    """Сколько хранить код после истечения срока; не меньше суток — окна потолка неудач."""
    device_revoked_retention_s: int = Field(default=604_800, ge=0)
    """Сколько хранить отозванное доверенное устройство до удаления."""

    @model_validator(mode="after")
    def _lifetime_covers_ttl(self) -> EmailCodeSettings:
        if self.max_lifetime_s < self.ttl_s:
            raise ValueError("GV_EMAIL_CODE__MAX_LIFETIME_S не может быть меньше TTL кода")
        return self


class LoginLimitSettings(BaseModel):
    """Лимиты проверки пароля (вход, admin-вход, повторная аутентификация).

    Счётчик аккаунта ведётся по паре (аккаунт, подсеть клиента): неудачи злоумышленника из
    своей сети не блокируют владельца из другой. Общий мягкий лимит по аккаунту сдерживает
    распределённый перебор.
    """

    per_ip: int = Field(default=10, ge=1)
    ip_window_s: int = Field(default=60, ge=1)
    per_account_subnet: int = Field(default=5, ge=1)
    per_account: int = Field(default=50, ge=1)
    account_window_s: int = Field(default=900, ge=1)
    subnet_v4_bits: int = Field(default=24, ge=8, le=32)
    subnet_v6_bits: int = Field(default=64, ge=16, le=128)
    register_per_ip_per_min: int = Field(default=10, ge=1)
    reset_per_ip_per_min: int = Field(default=5, ge=1)


class EmailLimitSettings(BaseModel):
    """Лимит писем на один адрес (register, forgot): защита владельца адреса от рассылки."""

    per_address: int = Field(default=5, ge=1)
    window_s: int = Field(default=3600, ge=60)


class Argon2Settings(BaseModel):
    """Параметры argon2id (минимум по §8 п.1: 64 МиБ, t ≥ 3)."""

    time_cost: int = Field(default=3, ge=1)
    memory_kib: int = Field(default=65_536, ge=8)
    parallelism: int = Field(default=1, ge=1)
    max_concurrency: int = Field(default=4, ge=1)
    """Одновременных вычислений argon2 на процесс (каждое — ``memory_kib`` памяти)."""


class ConsentSettings(BaseModel):
    """Обязательные согласия ``kind:version,...``."""

    required: str = "terms:2026-10,privacy:2026-10,age_18:1"


class OAuthSettings(BaseModel):
    """Общие параметры OAuth."""

    return_to_prefixes: tuple[str, ...] = ("/",)
    state_ttl_s: int = Field(default=600, ge=60, le=1800)


class ServiceClientSettings(BaseModel):
    """Клиент сервисных токенов (элемент ``GV_SERVICE_CLIENTS``, D13 плана этапа 2).

    Пример: ``{"client_id": "game", "secret_sha256": "<sha256 секрета, hex>",
    "audiences": {"media_pipeline": ["assets.link"]}}``. Сам секрет identity не хранит.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    client_id: str
    secret_sha256: str
    audiences: dict[str, tuple[str, ...]]


class ServiceTokenSettings(BaseModel):
    """Сервисные токены: ``GV_SERVICE_TOKEN__*``."""

    ttl_s: int = Field(default=600, ge=60, le=3600)


class ServiceTokenLimitSettings(BaseModel):
    """Лимиты выдачи сервисных токенов: ``GV_SERVICE_TOKEN_LIMIT__*``.

    Неудачи считаются по паре (``client_id``, IP), успешные выдачи — по ``client_id``:
    неверные секреты с чужого адреса не блокируют честного клиента.
    """

    failures_per_client_ip_per_min: int = Field(default=30, ge=1)
    success_per_client_per_min: int = Field(default=600, ge=1)


class UsersEmailsLimitSettings(BaseModel):
    """Лимит ``POST /internal/v1/users/emails``: ``GV_USERS_EMAILS_LIMIT__*``.

    Ручка массово отдаёт чужие адреса, поэтому вызовы считаются на ``client_id``.
    """

    per_client: int = Field(default=60, ge=1)
    window_s: int = Field(default=60, ge=1, le=3600)
    ids_per_hour: int = Field(default=20000, ge=1)
    """Запрошенных идентификаторов на ``client_id`` в час (по адресам)."""


class RealtimeTokenLimitSettings(BaseModel):
    """Лимит выдачи connection-токенов Centrifugo: ``GV_REALTIME_TOKEN_LIMIT__*``.

    Счётчик — на пользователя. Клиент обновляет токен раз в ``token_ttl_s`` и при
    переподключении; запас — на несколько вкладок и обрывы сети.
    """

    per_user: int = Field(default=20, ge=1)
    window_s: int = Field(default=60, ge=1, le=3600)


class BootstrapAdminSettings(BaseModel):
    """Bootstrap-админ для автосидинга (``GV_BOOTSTRAP_ADMIN__*``), только local/ci.

    Команда ``migrate`` идемпотентно создаёт (или дополняет ролью ``admin``) пользователя с
    этим e-mail, чтобы автосидинг получил admin-токен через ``POST /internal/v1/admin-sessions``.
    Пусто (по умолчанию) — функция выключена. Непустые ``login``/``password`` вне local/ci —
    ошибка старта (``wiring.bootstrap_admin_credentials``): админ не создаётся в staging/production.
    """

    login: str = ""
    """E-mail администратора (он же логин admin-входа); пусто — функция выключена."""
    password: SecretStr = SecretStr("")
    """Пароль; должен проходить обычную политику паролей (может быть сгенерирован в env)."""
    nickname: str = ""
    """Никнейм создаваемого пользователя; пусто — ``admin``."""

    @property
    def enabled(self) -> bool:
        """Заданы и логин, и пароль."""
        return bool(self.login and self.password.get_secret_value())


class GuestSettings(BaseModel):
    """Гостевые сессии (D5 плана этапа 2): ``GV_GUEST__*``."""

    per_ip_per_hour: int = Field(default=20, ge=1)
    """Новых гостевых сессий с одного IP в час (лимитер fail-open, как у входа)."""
    refresh_ttl_s: int = Field(default=2_592_000, ge=3600)
    """Срок refresh-токена гостя; столько же живёт заброшенная гостевая сессия."""
    revoked_retention_s: int = Field(default=86_400, ge=0)
    """Сколько хранить отозванную гостевую сессию до удаления."""
    purge_interval_s: float = Field(default=3600, gt=0)
    purge_jitter_s: float = Field(default=300, ge=0)
    purge_batch: int = Field(default=1000, ge=1, le=10_000)
    purge_max_batches: int = Field(default=20, ge=1)


class Settings(ServiceSettings):
    """Настройки сервиса. Новые поля добавлять здесь с безопасными значениями по умолчанию."""

    service_name: str = "identity"
    jwt: JwtSettings = JwtSettings()
    cookies: CookieSettings = CookieSettings()
    yandex: YandexSettings = YandexSettings()
    email: EmailSettings = EmailSettings()
    smtp: SmtpSettings = SmtpSettings()
    email_code: EmailCodeSettings = EmailCodeSettings()
    login_limit: LoginLimitSettings = LoginLimitSettings()
    email_limit: EmailLimitSettings = EmailLimitSettings()
    argon2: Argon2Settings = Argon2Settings()
    consents: ConsentSettings = ConsentSettings()
    oauth: OAuthSettings = OAuthSettings()
    service_clients: tuple[ServiceClientSettings, ...] = ()
    """``GV_SERVICE_CLIENTS`` — JSON-массив клиентов сервисных токенов; пусто — выдачи нет."""
    service_token: ServiceTokenSettings = ServiceTokenSettings()
    service_token_limit: ServiceTokenLimitSettings = ServiceTokenLimitSettings()
    guest: GuestSettings = GuestSettings()
    realtime_token_limit: RealtimeTokenLimitSettings = RealtimeTokenLimitSettings()
    users_emails_limit: UsersEmailsLimitSettings = UsersEmailsLimitSettings()
    """Лимит ``POST /v1/realtime/token``; секрет и TTL — в ``GV_REALTIME__*`` (gv_service)."""
    bootstrap_admin: BootstrapAdminSettings = BootstrapAdminSettings()
    """``GV_BOOTSTRAP_ADMIN__*`` — bootstrap-админ автосидинга (только local/ci); пусто — выкл."""
    bootstrap_admin_emails: tuple[str, ...] = ()
    reannounce_staff_perms: bool = False
    """``GV_REANNOUNCE_STAFF_PERMS=true`` — команда ``migrate`` повторно публикует права всех
    сотрудников (бэкфилл проекций social/admin при выкладке, ADR 0058); после выкладки снять."""
    redis_key_prefix: str = ""
    """Пространство имён ключей Redis (тесты и несколько стендов на одном Redis).
    Для denylist ``sid`` сервисы-потребители должны использовать тот же префикс."""
