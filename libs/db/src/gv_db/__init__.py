"""gv_db — работа сервиса со своей базой PostgreSQL.

* ``create_metadata`` — метаданные с соглашением об именах ограничений и служебными
  таблицами outbox/inbox;
* ``create_engine`` / ``create_session_factory`` — асинхронный движок и сессии;
* ``SqlAlchemyUnitOfWork`` — транзакция use-case с записью доменных событий в outbox.
"""

from gv_db.engine import create_engine, create_session_factory
from gv_db.metadata import NAMING_CONVENTION, ServiceTables, create_metadata
from gv_db.migrations import run_migrations
from gv_db.uow import SqlAlchemyUnitOfWork

__all__ = [
    "NAMING_CONVENTION",
    "ServiceTables",
    "SqlAlchemyUnitOfWork",
    "create_engine",
    "create_metadata",
    "create_session_factory",
    "run_migrations",
]
