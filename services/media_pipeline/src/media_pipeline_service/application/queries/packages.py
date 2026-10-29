"""Статус пакета пакетной загрузки (``GET /admin/v1/packages/{id}``)."""

from __future__ import annotations

from uuid import UUID

from media_pipeline_service.application.ports import UnitOfWorkFactory
from media_pipeline_service.domain.errors import PackageNotFoundError
from media_pipeline_service.domain.packages import ImportPackage


class GetPackage:
    """Пакет со счётчиками и ошибками по файлам (без обращения к S3)."""

    def __init__(self, uow: UnitOfWorkFactory) -> None:
        self._uow = uow

    async def execute(self, package_id: UUID) -> ImportPackage:
        """Прочитать пакет.

        Raises:
            PackageNotFoundError: пакета нет.
        """
        async with self._uow() as uow:
            package = await uow.packages.get(package_id)
        if package is None:
            raise PackageNotFoundError("пакет не найден")
        return package
