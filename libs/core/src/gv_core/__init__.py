"""gv_core — базовые строительные блоки домена Guessverse.

Без зависимостей от фреймворков и инфраструктуры: этот пакет можно импортировать из слоя
``domain`` любого сервиса.
"""

from gv_core.clock import Clock, SystemClock
from gv_core.context import RequestContext, bind_context, current_context
from gv_core.domain import AggregateRoot, DomainEvent, Entity
from gv_core.errors import (
    AppError,
    ConflictError,
    DependencyUnavailableError,
    DomainError,
    FeatureDisabledError,
    ForbiddenError,
    GoneError,
    InvalidInputError,
    NotFoundError,
    NotImplementedYetError,
    RateLimitedError,
    UnauthenticatedError,
)
from gv_core.ids import uuid7
from gv_core.random import Random, SeededRandom, SystemRandom
from gv_core.uow import UnitOfWork

__all__ = [
    "AggregateRoot",
    "AppError",
    "Clock",
    "ConflictError",
    "DependencyUnavailableError",
    "DomainError",
    "DomainEvent",
    "Entity",
    "FeatureDisabledError",
    "ForbiddenError",
    "GoneError",
    "InvalidInputError",
    "NotFoundError",
    "NotImplementedYetError",
    "Random",
    "RateLimitedError",
    "RequestContext",
    "SeededRandom",
    "SystemClock",
    "SystemRandom",
    "UnauthenticatedError",
    "UnitOfWork",
    "bind_context",
    "current_context",
    "uuid7",
]
