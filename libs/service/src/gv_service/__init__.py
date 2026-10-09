"""gv_service — каркас процесса сервиса: настройки, API, health, запуск компонентов."""

from gv_service.app import create_app
from gv_service.definition import Infrastructure, ServiceDefinition
from gv_service.health import ReadinessCheck
from gv_service.mutations import IdempotencyKey, Reason, idempotency_key
from gv_service.problems import Problem, problem_responses
from gv_service.runner import main
from gv_service.settings import Environment, ServiceSettings

__all__ = [
    "Environment",
    "IdempotencyKey",
    "Infrastructure",
    "Problem",
    "ReadinessCheck",
    "Reason",
    "ServiceDefinition",
    "ServiceSettings",
    "create_app",
    "idempotency_key",
    "main",
    "problem_responses",
]
