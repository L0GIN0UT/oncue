"""Фабрика FastAPI-приложения сервиса."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI

from gv_auth.verifier import JwtVerifier, http_jwks_loader
from gv_observability.metrics import MetricsMiddleware
from gv_observability.tracing import instrument_app
from gv_service.definition import Infrastructure, ServiceDefinition
from gv_service.health import health_router
from gv_service.middleware import RequestContextMiddleware
from gv_service.problems import register_problem_handlers


def create_app(
    definition: ServiceDefinition,
    infra: Infrastructure,
    *,
    is_shutting_down: Callable[[], bool] = lambda: False,
    tracing_enabled: bool = False,
    jwt_verifier: JwtVerifier | None = None,
    service_jwt_verifier: JwtVerifier | None = None,
) -> FastAPI:
    """Собрать приложение: роутеры сервиса, health, сбор метрик, Problem Details, middleware.

    Жизненным циклом ``infra`` управляет вызывающий (runner или тест), а не приложение:
    так в режиме ``all`` одна инфраструктура разделяется между api и фоновыми процессами.

    Args:
        jwt_verifier: Явный верификатор (тесты). По умолчанию создаётся по ``GV_JWKS_URL``;
            без него ручки с ``require_perm`` отвечают 500 — это ошибка конфигурации.
        service_jwt_verifier: Верификатор сервисных токенов для internal-ручек
            (``gv_auth.ServiceAuthenticator``). По умолчанию — по ``GV_JWKS_URL`` с
            ``aud = settings.internal_audience`` (D13 плана этапа 2).
    """
    settings = infra.settings
    docs_enabled = not settings.is_production
    app = FastAPI(
        title=f"guessverse-{definition.name}",
        description=definition.description,
        version="0.1.0",
        docs_url="/docs" if docs_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )
    app.state.infra = infra
    if jwt_verifier is None and settings.jwks_url:
        jwt_verifier = JwtVerifier(
            http_jwks_loader(settings.jwks_url),
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
        )
    if service_jwt_verifier is None and settings.jwks_url:
        service_jwt_verifier = JwtVerifier(
            http_jwks_loader(settings.jwks_url),
            issuer=settings.jwt_issuer,
            audience=settings.internal_audience,
        )
    app.state.jwt_verifier = jwt_verifier
    app.state.service_jwt_verifier = service_jwt_verifier
    app.state.csrf_origins = frozenset(settings.public_origins)

    register_problem_handlers(app)
    for router in definition.build_routers(infra):
        app.include_router(router)
    app.include_router(health_router(infra.readiness_checks(), is_shutting_down))
    # /metrics здесь нет намеренно: метрики отдаёт отдельный сервер на GV_METRICS_PORT
    # (gv_service.metrics_server), чтобы основной порт за gateway их не раскрывал.

    # Порядок: последний добавленный выполняется первым. Контекст запроса должен быть
    # установлен до метрик и обработки, чтобы correlation_id попал во все логи.
    app.add_middleware(MetricsMiddleware)
    app.add_middleware(RequestContextMiddleware)
    if tracing_enabled:
        instrument_app(app)
    return app
