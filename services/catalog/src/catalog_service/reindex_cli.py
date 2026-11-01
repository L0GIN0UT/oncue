"""``python -m catalog_service reindex [--no-switch | --rollback]``: пересборка поискового индекса.

Запускается там же, где API каталога (те же ``GV_*`` переменные: БД, Redis, OpenSearch).
Не меняет БД; боевой индекс не трогает до атомарного переключения алиаса.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from opensearchpy import AsyncOpenSearch

from catalog_service.application.commands.reindex import RebuildIndex
from catalog_service.infrastructure.container import container_for
from catalog_service.infrastructure.db.tables import TABLES
from catalog_service.infrastructure.search.reindex import OpenSearchMigrator
from catalog_service.settings import Settings
from gv_service import Infrastructure

# Боевой клиент отвечает за секунды; bulk по 500 документов и refresh занимают дольше.
_REINDEX_TIMEOUT_S = 120.0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="catalog reindex", description=__doc__)
    parser.add_argument("--batch", type=int, default=500, help="документов в одном bulk")
    parser.add_argument(
        "--no-switch", action="store_true", help="собрать индекс, алиас не переключать"
    )
    parser.add_argument("--rollback", action="store_true", help="вернуть алиас на прежний индекс")
    return parser


def _say(text: str) -> None:
    sys.stdout.write(text + "\n")


async def _run(args: argparse.Namespace) -> int:
    settings = Settings()
    os_settings = settings.opensearch
    if not os_settings.url:
        _say("OpenSearch не настроен (GV_CATALOG__OPENSEARCH__URL): переиндексировать нечего")
        return 2
    infra = await Infrastructure.open(settings, TABLES)
    client = AsyncOpenSearch(hosts=[os_settings.url], timeout=_REINDEX_TIMEOUT_S)
    try:
        migrator = OpenSearchMigrator(client, os_settings.index)
        container = container_for(infra)
        if args.rollback:
            current, previous = await migrator.rollback()
            # Прежний индекс не получал событий после переключения: догоняем его из БД.
            catch_up = OpenSearchMigrator(client, os_settings.index, target=previous)
            synced = await RebuildIndex(
                container.uow, catch_up, container.cache, batch=args.batch
            ).sync()
            _say(
                f"алиас {os_settings.index}: {current} -> {previous}, догнано документов: {synced}"
            )
            return 0
        rebuild = RebuildIndex(container.uow, migrator, container.cache, batch=args.batch)
        report = await rebuild(switch=not args.no_switch)
        _say(
            f"цель={report.target} единиц={report.seen} записано={report.written} "
            f"переключено={report.switched} уже_боевой={report.already_live} "
            f"прежний={','.join(report.previous) or '-'}"
        )
        return 0
    finally:
        await client.close()
        await infra.close()


def reindex_main(argv: list[str] | None = None) -> None:
    """Точка входа команды."""
    parser = _parser()
    args = parser.parse_args(sys.argv[2:] if argv is None else argv)
    if args.batch < 1:
        parser.error("--batch должен быть положительным")
    if args.rollback and args.no_switch:
        parser.error("--rollback и --no-switch несовместимы")
    sys.exit(asyncio.run(_run(args)))
