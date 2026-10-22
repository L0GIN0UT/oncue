"""Настройки Django (``DJANGO_SETTINGS_MODULE=admin_service.django_settings``).

Строятся из ``admin_service.settings.Settings`` (env ``GV_*``). Секреты в коде не хранятся;
без ``GV_ADMIN__SECRET_KEY`` на проде процесс не стартует.
"""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from django.core.exceptions import ImproperlyConfigured

from admin_service.settings import Settings
from gv_service import Environment

GV = Settings()
BASE_DIR = Path(__file__).resolve().parent


def database_from_url(url: str | None) -> dict[str, Any]:
    """Параметры БД Django из ``GV_DATABASE_URL`` (тот же формат, что у остальных сервисов).

    ``postgresql+asyncpg://`` — диалект SQLAlchemy; Django работает через psycopg, поэтому
    драйвер из схемы отбрасывается. Без URL — заглушка: команды без БД (проверки, тесты
    без базы) работают, а запрос к БД падает явно.
    """
    if not url:
        return {"ENGINE": "django.db.backends.dummy"}
    parts = urlsplit(url)
    if parts.scheme.split("+", 1)[0] not in {"postgresql", "postgres"}:
        raise ImproperlyConfigured("GV_DATABASE_URL: поддерживается только PostgreSQL")
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": parts.path.lstrip("/"),
        "USER": unquote(parts.username or ""),
        "PASSWORD": unquote(parts.password or ""),
        "HOST": parts.hostname or "",
        "PORT": str(parts.port or ""),
        # ASGI: соединение на запрос; долгоживущие соединения в потоках sync_to_async текут.
        "CONN_MAX_AGE": 0,
        "OPTIONS": {"application_name": GV.service_name},
        "TEST": {},
    }


def _secret_key() -> str:
    if GV.admin.secret_key is not None:
        return GV.admin.secret_key.get_secret_value()
    if GV.is_production:
        raise ImproperlyConfigured("задайте GV_ADMIN__SECRET_KEY")
    # Локально и в тестах — случайный ключ: сессии живут до перезапуска процесса.
    return secrets.token_urlsafe(50)


def _caches() -> dict[str, Any]:
    """Кэш Django — счётчики лимита попыток входа.

    С ``GV_REDIS_URL`` — Redis (общий для всех реплик); без него — память процесса: лимит
    действует на каждую реплику отдельно (для local/ci достаточно, на проде нужен Redis).
    """
    if GV.redis_url is not None:
        return {
            "default": {
                "BACKEND": "django.core.cache.backends.redis.RedisCache",
                "LOCATION": GV.redis_url.get_secret_value(),
                "KEY_PREFIX": "admin",
            }
        }
    return {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


_LOCAL = GV.environment is Environment.LOCAL

SECRET_KEY = _secret_key()
DEBUG = False  # подробные страницы ошибок раскрывают настройки; локально — логи
ALLOWED_HOSTS = list(GV.admin.allowed_hosts)
CSRF_TRUSTED_ORIGINS = list(GV.admin.csrf_trusted_origins)

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "admin_service.core",
    "admin_service.audit",
    "admin_service.apps.catalog",
    "admin_service.apps.users",
    "admin_service.apps.game_config",
    "admin_service.apps.game_daily",
    "admin_service.apps.collections",
    "admin_service.apps.matches",
]

MIDDLEWARE = [
    # Health — первым: пробы Kubernetes приходят с Host = IP пода и не из allowlist.
    "admin_service.core.middleware.HealthMiddleware",
    "admin_service.core.middleware.RequestIdMiddleware",
    "admin_service.core.middleware.IpAllowlistMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "admin_service.urls"
ASGI_APPLICATION = "admin_service.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "admin_service.core.menu.menu",
            ],
        },
    },
]

DATABASES = {
    "default": database_from_url(
        GV.database_url.get_secret_value() if GV.database_url is not None else None
    )
}
# Каркас чтения реплик (ADR 0013, этап 5): роутеры подключаются здесь, пока пусто.
DATABASE_ROUTERS: list[str] = []
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

CACHES = _caches()

# Своих паролей нет: вход только через identity (D2 плана этапа 1).
AUTH_USER_MODEL = "core.AdminUser"
AUTHENTICATION_BACKENDS = ["admin_service.core.auth.IdentityAuthBackend"]
LOGIN_URL = "admin:login"
AUTH_PASSWORD_VALIDATORS: list[dict[str, str]] = []

SESSION_ENGINE = "django.contrib.sessions.backends.db"  # токены identity — только на сервере
SESSION_COOKIE_NAME = "gv_admin_session"
SESSION_COOKIE_AGE = GV.admin.session_ttl_s
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Strict"
SESSION_COOKIE_SECURE = not _LOCAL
CSRF_COOKIE_NAME = "gv_admin_csrf"
CSRF_COOKIE_SECURE = not _LOCAL
CSRF_COOKIE_SAMESITE = "Strict"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

DATA_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024  # крупнее — во временный файл

LANGUAGE_CODE = "ru"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"

# Логи настраивает gv_observability (structlog, JSON в stdout) в admin_service.main.
LOGGING_CONFIG = None

# ---------------------------------------------------------------- Guessverse
GV_ENVIRONMENT = GV.environment.value
GV_IDENTITY_URL = GV.admin.identity_url
GV_JWKS_URL = GV.effective_jwks_url
GV_JWT_ISSUER = GV.jwt_issuer
GV_JWT_AUDIENCE = GV.jwt_audience
GV_SERVICE_URLS = dict(GV.admin.service_urls)
GV_SERVICE_TIMEOUT_S = GV.admin.service_timeout_s
GV_IP_ALLOWLIST = list(GV.effective_ip_allowlist)
GV_MAX_UPLOAD_MB = GV.admin.max_upload_mb
GV_MODE_CONFIG_MAX_BYTES = GV.admin.mode_config_max_bytes
GV_DAILY_TIMEZONE = GV.admin.daily_timezone
GV_DAILY_DEFAULT_DAYS = GV.admin.daily_default_days
GV_DAILY_RANGE_MAX_DAYS = GV.admin.daily_range_max_days
GV_COLLECTION_TEXT_MAX_BYTES = GV.admin.collection_text_max_bytes
GV_SESSION_ENC_KEY = (
    GV.admin.session_enc_key.get_secret_value() if GV.admin.session_enc_key is not None else None
)
GV_LOGIN_ATTEMPTS_LIMIT = GV.admin.login_attempts_limit
GV_LOGIN_ATTEMPTS_WINDOW_S = GV.admin.login_attempts_window_s
