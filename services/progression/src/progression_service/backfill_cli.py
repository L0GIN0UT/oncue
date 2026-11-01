"""``service-entry backfill-collection-authors --collections-url URL``: догрузка авторов.

Однократная команда оператора после выката защиты от накрутки: дописывает в проекцию
``collection_authors`` сборники, опубликованные до выката (владельцы известны только из
публичного каталога collections). Повтор безопасен; существующие записи не меняются.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from gv_service import Infrastructure
from progression_service.application.admin.collection_authors import BackfillCollectionAuthors
from progression_service.infrastructure.collections_directory import HttpCollectionDirectory
from progression_service.infrastructure.container import container_for
from progression_service.infrastructure.db.tables import TABLES
from progression_service.settings import Settings

DEFAULT_COLLECTIONS_URL = "http://collections:8000"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="progression backfill-collection-authors")
    parser.add_argument(
        "--collections-url",
        default=DEFAULT_COLLECTIONS_URL,
        help="адрес сервиса collections внутри сети (публичный GET /v1/collections)",
    )
    return parser


async def _run(args: argparse.Namespace) -> int:
    infra = await Infrastructure.open(Settings(), TABLES)
    try:
        container = container_for(infra)
        report = await BackfillCollectionAuthors(
            container.uow_factory(), HttpCollectionDirectory(args.collections_url), infra.clock
        )()
        sys.stdout.write(
            f"сборников={report.seen} добавлено={report.written} уже_были={report.skipped}\n"
        )
        return 0
    finally:
        await infra.close()


def backfill_main(argv: list[str] | None = None) -> None:
    """Точка входа команды."""
    args = _parser().parse_args(sys.argv[2:] if argv is None else argv)
    sys.exit(asyncio.run(_run(args)))
