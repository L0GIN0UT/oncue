"""Admin API (/admin/v1): очередь модерации, конфиг и санкции (5c; ADR 0051-0053).

* очередь - `cases.py`; конфиг (ADR 0053) - `config.py`; игрок для модератора и санкции
  (`users/*`, `sanctions/*`) - `sanctions.py`;
* права: очередь - `admin.access` + `social.moderate`; санкции `ranked|chat|online` -
  `social.sanctions.manage`, `account` - дополнительно `social.sanctions.account` (проверка в
  application-слое: 403 `social.account_sanction_forbidden`); конфиг - `social.config.manage`
  (черновик) и `social.config.publish` (публикация, откат);
* мутации: обязательные `Idempotency-Key` и `reason` (в аудит), оптимистичная блокировка по
  `version` кейса (409 `social.case_version_conflict`);
* модератор не выдаёт и не снимает санкции себе (403 `social.self_sanction_forbidden`);
* успешные ответы - `Cache-Control: no-store`; текст жалоб не покидает админ-API.
"""

from __future__ import annotations

from fastapi import APIRouter

from social_service.api.admin.cases import register_case_routes
from social_service.api.admin.config import register_config_routes
from social_service.api.admin.sanctions import build_sanctions_router
from social_service.api.admin.services import AdminServices
from social_service.application.sanctions import SanctionService


def build_admin_router(services: AdminServices, sanctions: SanctionService) -> APIRouter:
    """Ручки админки social."""
    router = APIRouter(prefix="/admin/v1", tags=["social-admin"])
    register_case_routes(router, services)
    register_config_routes(router, services)
    router.include_router(build_sanctions_router(sanctions))
    return router
