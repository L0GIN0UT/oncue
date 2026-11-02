"""``python -m catalog_service reindex [--no-switch | --recreate | --rollback]``.

Пересборка поискового индекса. Запускается там же, где API каталога (те же ``GV_*``
переменные: БД, Redis, OpenSearch). Не меняет БД; боевой индекс не трогает до атомарного
переключения алиаса. Размер пачки и таймаут: ``GV_CATALOG__OPENSEARCH__REINDEX_*``.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from opensearchpy import AsyncOpenSearch

from catalog_service.application.commands.reindex import RebuildIndex
from catalog_service.infrastructure.container import CatalogContainer, container_for
from catalog_service.infrastructure.db.tables import TABLES
from catalog_service.infrastructure.search.reindex import OpenSearchMigrator
from catalog_service.settings import Settings
from gv_core.errors import AppError
from gv_observability.logging import configure_logging
from gv_service import Infrastructure

EXIT_FAILED = 1
EXIT_NOT_CONFIGURED = 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="catalog reindex", description=__doc__)
    parser.add_argument(
        "--batch", type=int, default=None, help="документов в одном bulk (по умолчанию из настроек)"
    )
    parser.add_argument(
        "--no-switch", action="store_true", help="собрать индекс, алиас не переключать"
    )
    parser.add_argument(
        "--recreate",
        action="store_true",
        help="удалить целевой (не боевой) индекс и собрать с нуля",
    )
    parser.add_argument("--rollback", action="store_true", help="вернуть алиас на прежний индекс")
    return parser


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Разобрать и проверить аргументы (ошибка: usage и код 2)."""
    parser = _parser()
    args = parser.parse_args(argv)
    if args.batch is not None and args.batch < 1:
        parser.error("--batch должен быть положительным")
    if args.rollback and (args.no_switch or args.recreate):
        parser.error("--rollback несовместим с --no-switch и --recreate")
    return args


def _say(text: str) -> None:
    sys.stdout.write(text + "\n")


async def _rollback(
    client: AsyncOpenSearch, alias: str, container: CatalogContainer, batch: int
) -> int:
    migrator = OpenSearchMigrator(client, alias)
    current, previous = await migrator.rollback()
    # Кэш подсказок сбрасываем сразу: он собран по индексу, с которого ушли.
    if container.cache is not None:
        await container.cache.invalidate()
    _say(f"алиас {alias}: {current} -> {previous}")
    try:
        # Прежний индекс не получал событий после переключения: догоняем его из БД.
        catch_up = OpenSearchMigrator(client, alias, target=previous)
        synced = await RebuildIndex(container.uow, catch_up, container.cache, batch=batch).sync()
    except AppError as exc:
        _say(
            f"ВНИМАНИЕ: алиас уже переключён на {previous}, но догнать его не удалось "
            f"({exc.code}). Повторите откат или запустите republish."
        )
        return EXIT_FAILED
    _say(f"догнано документов: {synced}")
    return 0


async def _run(args: argparse.Namespace) -> int:
    settings = Settings()
    configure_logging(
        service=settings.service_name,
        env=settings.environment,
        level=settings.log_level,
        json=settings.log_json,
    )
    os_settings = settings.opensearch
    if not os_settings.url:
        _say("OpenSearch не настроен (GV_CATALOG__OPENSEARCH__URL): переиндексировать нечего")
        return EXIT_NOT_CONFIGURED
    batch = args.batch or os_settings.reindex_batch
    infra = await Infrastructure.open(settings, TABLES)
    client = AsyncOpenSearch(hosts=[os_settings.url], timeout=os_settings.reindex_timeout_s)
    try:
        container = container_for(infra)
        if args.rollback:
            return await _rollback(client, os_settings.index, container, batch)
        migrator = OpenSearchMigrator(client, os_settings.index)
        rebuild = RebuildIndex(container.uow, migrator, container.cache, batch=batch)
        report = await rebuild(switch=not args.no_switch, recreate=args.recreate)
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
    args = parse_args(sys.argv[2:] if argv is None else argv)
    sys.exit(asyncio.run(_run(args)))
