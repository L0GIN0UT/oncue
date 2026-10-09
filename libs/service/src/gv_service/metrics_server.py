"""Отдельный HTTP-сервер метрик Prometheus на внутреннем порту (``GV_METRICS_PORT``).

Почему не ``/metrics`` на основном порту: основной порт смотрит в gateway, и любая ошибка
маршрутизации открыла бы метрики наружу. Отдельный порт к тому же есть у процессов без
HTTP API (consumer, worker, relay) — их метрики раньше вообще не собирались.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

import uvicorn
from starlette.types import Receive, Scope, Send

from gv_observability.metrics import render_metrics

METRICS_PATH = "/metrics"


async def metrics_asgi_app(scope: Scope, _receive: Receive, send: Send) -> None:
    """Минимальное ASGI-приложение: ``GET /metrics`` → метрики, остальное → 404."""
    if scope["type"] != "http":  # lifespan выключен в конфиге сервера
        return
    if scope["path"] == METRICS_PATH and scope["method"] in {"GET", "HEAD"}:
        body, content_type = render_metrics()
        status = 200
    else:
        status, body, content_type = 404, b"not found\n", "text/plain; charset=utf-8"
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", content_type.encode())],
        }
    )
    await send({"type": "http.response.body", "body": b"" if scope["method"] == "HEAD" else body})


class _QuietServer(uvicorn.Server):
    """uvicorn без собственных обработчиков сигналов: сигналами управляет runner."""

    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield


class MetricsServer:
    """Сервер метрик процесса. Останавливается через ``stop()`` (идемпотентно).

    Args:
        host: Адрес прослушивания.
        port: Порт; ``0`` — свободный порт (тесты), фактический — ``bound_port``.
    """

    def __init__(self, host: str, port: int) -> None:
        config = uvicorn.Config(
            metrics_asgi_app,
            host=host,
            port=port,
            log_config=None,
            access_log=False,
            server_header=False,
            lifespan="off",
        )
        self._server = _QuietServer(config)

    async def serve(self) -> None:
        """Слушать порт до ``stop()``. Занятый порт — исключение (процесс перезапустится)."""
        try:
            await self._server.serve()
        except SystemExit as exc:
            # uvicorn при ошибке bind делает sys.exit(1); превращаем в обычную ошибку, чтобы
            # runner остановил процесс штатно (через _guard), а не обрывал его SystemExit.
            raise RuntimeError("сервер метрик не запустился (порт занят?)") from exc
        if not self._server.started and not self._server.should_exit:
            raise RuntimeError("сервер метрик не запустился")

    def stop(self) -> None:
        """Остановить сервер."""
        self._server.should_exit = True

    @property
    def started(self) -> bool:
        """Сервер принимает соединения."""
        return bool(self._server.started)

    @property
    def bound_port(self) -> int | None:
        """Фактический порт после запуска."""
        for server in getattr(self._server, "servers", []):
            for sock in server.sockets:
                return int(sock.getsockname()[1])
        return None
