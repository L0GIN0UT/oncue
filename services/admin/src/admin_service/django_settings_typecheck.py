"""Django-настройки для ``mypy_django_plugin`` (статическая проверка, не для запуска).

Плагин импортирует модуль настроек, а прод-умолчания ``Settings`` требуют allowlist IP и
секрет: без окружения импорт упал бы и mypy завершился бы внутренней ошибкой. Здесь окружение
по умолчанию — ``ci`` (как в тестах); явно заданные переменные не перекрываются.
"""

from __future__ import annotations

import os

os.environ.setdefault("GV_ENVIRONMENT", "ci")

from admin_service.django_settings import *  # noqa: F403 — модуль настроек целиком
