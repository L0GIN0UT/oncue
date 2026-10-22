"""gv_service — каркас процесса сервиса: настройки, API, health, запуск компонентов."""

from gv_service.app import create_app
from gv_service.definition import Infrastructure, ServiceDefinition
from gv_service.health import ReadinessCheck
from gv_service.mutations import IdempotencyKey, Reason, idempotency_key
from gv_service.periodic import JobResult, PeriodicJob, PeriodicJobRunner
from gv_service.problems import Problem, problem_responses
from gv_service.runner import main
from gv_service.runners import Runner, RunnerFailedError, supervise_runners
from gv_service.service_auth import ServiceAuthNotConfiguredError, build_service_token_provider
from gv_service.settings import (
    Environment,
    InternalAuthSettings,
    RealtimeSettings,
    RunnersSettings,
    ServiceAuthSettings,
    ServiceSettings,
    UgcSettings,
)

__all__ = [
    "Environment",
    "IdempotencyKey",
    "Infrastructure",
    "InternalAuthSettings",
    "JobResult",
    "PeriodicJob",
    "PeriodicJobRunner",
    "Problem",
    "ReadinessCheck",
    "RealtimeSettings",
    "Reason",
    "Runner",
    "RunnerFailedError",
    "RunnersSettings",
    "ServiceAuthNotConfiguredError",
    "ServiceAuthSettings",
    "ServiceDefinition",
    "ServiceSettings",
    "UgcSettings",
    "build_service_token_provider",
    "create_app",
    "idempotency_key",
    "main",
    "problem_responses",
    "supervise_runners",
]
