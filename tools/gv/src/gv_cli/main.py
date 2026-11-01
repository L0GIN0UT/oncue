"""gv — CLI разработчика Guessverse.

gv gen [--check]                 сгенерировать конфиги из infra/services.toml
gv new-service NAME ...          создать сервис из шаблона и добавить в реестр
gv test [TARGET] [--level L]     тесты модуля/сервиса/всего репо
gv test frontend [--level e2e]   тесты фронтенда (pnpm; e2e — Playwright)
gv test --changed                тесты только затронутых изменениями модулей (и frontend/)
gv lint                          ruff, mypy, import-linter, gen --check
gv dev up|down|ps|logs ...       локальное окружение (docker compose)
gv dev secrets                   сгенерировать .env со случайными паролями
gv contracts export              снимок OpenAPI и схем событий в contracts/
gv contracts check [--base REF]  актуальность снимка и отсутствие ломающих изменений
gv ci matrix                     JSON-матрица изменённых модулей для GitHub Actions
gv hook NAME                     хук Claude Code (событие в stdin), см. .claude/settings.json
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import subprocess
import sys
from pathlib import Path
from typing import Annotated

import typer

from gv_cli import contracts, generate, hooks, scaffold, workspace
from gv_cli.generate import service_secret_env
from gv_cli.preflight import PreflightError, check_env, read_env_file
from gv_cli.registry import NAME_RE, load_services, repo_root

app = typer.Typer(help="CLI разработчика Guessverse", no_args_is_help=True)
dev = typer.Typer(help="Локальное окружение (docker compose)", no_args_is_help=True)
ci = typer.Typer(help="Утилиты для CI", no_args_is_help=True)
contracts_app = typer.Typer(help="Контракты API и событий (contracts/)", no_args_is_help=True)
app.add_typer(dev, name="dev")
app.add_typer(ci, name="ci")
app.add_typer(contracts_app, name="contracts")

LEVELS = ("unit", "integration", "component", "contract", "e2e")
COMPOSE_DIR = Path("infra/compose")
STACKS = {
    "infra": ["compose.infra.yml", "compose.infra.dev.yml"],
    "services": [
        "compose.infra.yml",
        "compose.infra.dev.yml",
        "compose.services.yml",
        "compose.services.dev.yml",
        "compose.gateway.yml",
    ],
    "observability": ["compose.observability.yml"],
}


def _run(cmd: list[str], cwd: Path, env: dict[str, str] | None = None) -> int:
    typer.secho("$ " + " ".join(cmd), fg=typer.colors.BRIGHT_BLACK)
    return subprocess.call(cmd, cwd=cwd, env={**os.environ, **(env or {})})


# ----------------------------------------------------------------------------- gen
@app.command()
def gen(check: Annotated[bool, typer.Option(help="Только проверить актуальность")] = False) -> None:
    """Сгенерировать производные конфиги из реестра сервисов."""
    root = repo_root()
    services = load_services(root)
    if check:
        stale = generate.stale(root, services)
        if stale:
            typer.secho("Устарели (запустите `gv gen`):\n  " + "\n  ".join(stale), fg="red")
            raise typer.Exit(1)
        typer.secho("Сгенерированные файлы актуальны", fg="green")
        return
    changed = generate.write_all(root, services)
    typer.echo("Обновлено:\n  " + "\n  ".join(changed) if changed else "Изменений нет")


# ----------------------------------------------------------------------------- new-service
@app.command("new-service")
def new_service(
    name: Annotated[str, typer.Argument(help="Имя в snake_case, например `recommendations`")],
    description: Annotated[str, typer.Option(help="Назначение сервиса")],
    public: Annotated[
        str, typer.Option(help="Префикс на gateway, пусто — без публичного API")
    ] = "",
    database: Annotated[bool, typer.Option(help="Своя БД")] = True,
) -> None:
    """Создать сервис по шаблону, добавить в реестр и перегенерировать конфиги."""
    root = repo_root()
    if not NAME_RE.match(name):
        raise typer.BadParameter("имя: латиница в нижнем регистре, цифры и _")
    if any(s.name == name for s in load_services(root)):
        raise typer.BadParameter(f"сервис {name} уже есть в реестре")
    registry = root / "infra" / "services.toml"
    entry = scaffold.registry_entry(name, description, public=public, database=database)
    registry.write_text(registry.read_text(encoding="utf-8") + entry, encoding="utf-8")
    service = next(s for s in load_services(root) if s.name == name)
    target = scaffold.create_service(root, service)
    generate.write_all(root, load_services(root))
    typer.secho(f"Создан {target.relative_to(root)}", fg="green")
    typer.echo(
        "Дальше:\n"
        "  1. uv sync\n"
        f"  2. gv test {name}\n"
        "  3. Описать сервис в docs/architecture/services.md и в его CLAUDE.md\n"
        "  4. При необходимости поправить флаги redis/kafka/rabbitmq в infra/services.toml\n"
        "     и выполнить `gv gen`"
    )


# ----------------------------------------------------------------------------- test
def _resolve_target(root: Path, target: str) -> Path:
    for candidate in (root / "services" / target, root / "libs" / target, root / target):
        if candidate.is_dir():
            return candidate.relative_to(root)
    raise typer.BadParameter(f"не найден модуль {target!r} (services/, libs/ или путь)")


FRONTEND = "frontend"
FRONTEND_LEVELS = (None, "unit", "e2e")


def frontend_test_command(root: Path, level: str | None) -> list[str] | None:
    """Команда тестов фронтенда; ``None`` — фронтенда нет (``frontend/package.json``).

    Unit (vitest) — ``pnpm -C frontend test``; ``e2e`` — Playwright из зависимостей
    фронтенда (браузеры — ``PLAYWRIGHT_BROWSERS_PATH``).
    """
    if level not in FRONTEND_LEVELS:
        raise typer.BadParameter("для frontend уровни: unit | e2e")
    if not (root / FRONTEND / "package.json").is_file():
        return None
    if level == "e2e":
        return ["pnpm", "-C", FRONTEND, "exec", "playwright", "test"]
    return ["pnpm", "-C", FRONTEND, "test"]


def _run_frontend(root: Path, level: str | None) -> int:
    cmd = frontend_test_command(root, level)
    if cmd is None:
        typer.echo("frontend/ отсутствует (нет frontend/package.json) — тесты фронтенда пропущены")
        return 0
    return _run(cmd, root)


def _pytest(root: Path, paths: list[str], level: str | None, *, cov: bool) -> int:
    cmd = ["uv", "run", "pytest", *paths]
    if level:
        cmd += ["-m", level]
    if cov:
        cmd += ["--cov", "--cov-report=term-missing", "--cov-report=xml"]
    return _run(cmd, root)


def _test_changed(root: Path, base: str, level: str | None, *, cov: bool) -> int:
    """Python-модули из diff (+ зависимые) и фронтенд, если затронут ``frontend/**``."""
    files = workspace.changed_files(root, base)
    members = workspace.load_members(root)
    names = workspace.changed_members(root, base, files)
    paths = [
        str(members[n].path) for n in sorted(names) if (root / members[n].path / "tests").is_dir()
    ]
    codes: list[int] = []
    # У фронтенда есть только unit (vitest) и e2e (Playwright).
    if workspace.frontend_changed(files) and level in FRONTEND_LEVELS:
        codes.append(_run_frontend(root, level))
    # Python-e2e нет: сценарии на полном стеке — во frontend и CI (job e2e-smoke).
    if paths and level != "e2e":
        codes.append(_pytest(root, paths, level, cov=cov))
    elif not paths:
        typer.echo("Нет затронутых Python-модулей с тестами")
    return next((c for c in codes if c), 0)


@app.command()
def test(
    target: Annotated[
        str | None, typer.Argument(help="Сервис, библиотека, путь или frontend")
    ] = None,
    level: Annotated[str | None, typer.Option(help="|".join(LEVELS))] = None,
    changed: Annotated[bool, typer.Option(help="Только затронутые изменениями модули")] = False,
    base: Annotated[str, typer.Option(help="База сравнения для --changed")] = "origin/main",
    cov: Annotated[bool, typer.Option(help="Отчёт покрытия")] = False,
) -> None:
    """Запустить тесты модуля (другие сервисы не нужны — они заменяются заглушками)."""
    root = repo_root()
    if level is not None and level not in LEVELS:
        raise typer.BadParameter(f"уровень: {', '.join(LEVELS)}")
    if changed:
        raise typer.Exit(_test_changed(root, base, level, cov=cov))
    if target == FRONTEND:
        raise typer.Exit(_run_frontend(root, level))
    paths = [str(_resolve_target(root, target))] if target else ["libs", "services", "tools"]
    raise typer.Exit(_pytest(root, paths, level, cov=cov))


# ----------------------------------------------------------------------------- lint
def _src_dirs(root: Path) -> list[str]:
    """Каталоги исходников для mypy (тесты проверяются pytest, а не mypy)."""
    dirs = [*root.glob("libs/*/src"), *root.glob("services/*/src"), root / "tools/gv/src"]
    return [str(d.relative_to(root)) for d in sorted(dirs)]


@app.command()
def lint() -> None:
    """Все статические проверки, как в CI."""
    root = repo_root()
    steps = [
        ["uv", "run", "ruff", "check", "."],
        ["uv", "run", "ruff", "format", "--check", "."],
        ["uv", "run", "mypy", *_src_dirs(root)],
        ["uv", "run", "lint-imports"],
        ["uv", "run", "gv", "gen", "--check"],
        ["uv", "run", "gv", "contracts", "check"],
    ]
    failed = [" ".join(s) for s in steps if _run(s, root) != 0]
    if failed:
        typer.secho("Провалено:\n  " + "\n  ".join(failed), fg="red")
        raise typer.Exit(1)
    typer.secho("Все проверки пройдены", fg="green")


@app.command()
def preflight(
    env_file: Annotated[Path, typer.Option(help="Файл окружения")] = COMPOSE_DIR / ".env",
) -> None:
    """Проверить окружение до выкладки (секреты Centrifugo заданы и различны).

    Для конвейеров выкладки и k8s: `gv dev up` вызывает ту же проверку сам. Значения секретов
    не выводятся.
    """
    try:
        check_env(env_file if env_file.is_absolute() else repo_root() / env_file, os.environ)
    except PreflightError as exc:
        typer.secho(f"preflight: {exc}", fg="red")
        raise typer.Exit(1) from exc
    typer.secho("preflight: окружение в порядке", fg="green")


# ----------------------------------------------------------------------------- dev
def _compose(root: Path, stack: str, args: list[str]) -> int:
    files = STACKS.get(stack)
    if files is None:
        raise typer.BadParameter(f"стек: {', '.join(STACKS)}")
    cmd = ["docker", "compose", "--env-file", ".env"]
    for f in files:
        cmd += ["-f", f]
    return _run(cmd + args, root / COMPOSE_DIR)


@dev.command()
def up(
    stack: Annotated[str, typer.Argument(help="infra | services | observability")] = "infra",
    services: Annotated[list[str] | None, typer.Argument(help="Только эти сервисы")] = None,
) -> None:
    """Поднять стек (по умолчанию — только инфраструктура)."""
    root = repo_root()
    if stack != "observability":  # Centrifugo в наблюдаемости не участвует
        try:
            check_env(root / COMPOSE_DIR / ".env", os.environ)
        except PreflightError as exc:
            typer.secho(f"preflight: {exc}", fg="red")
            raise typer.Exit(1) from exc
    raise typer.Exit(
        _compose(repo_root(), stack, ["up", "-d", "--build", "--wait", *(services or [])])
    )


@dev.command()
def down(stack: Annotated[str, typer.Argument()] = "services") -> None:
    """Остановить стек (данные в томах сохраняются)."""
    raise typer.Exit(_compose(repo_root(), stack, ["down"]))


@dev.command()
def ps(stack: Annotated[str, typer.Argument()] = "services") -> None:
    """Статус контейнеров."""
    raise typer.Exit(_compose(repo_root(), stack, ["ps"]))


@dev.command("db-roles")
def db_roles() -> None:
    """Создать рантайм-роли Postgres (`<имя>_app`) на существующем томе БД (ADR 0060).

    Скрипт инициализации Postgres работает только на пустом томе, поэтому стенд, поднятый до
    разделения ролей, роли не имеет, а `migrate` сервиса с `app_role` падает. Команда
    идемпотентна: создаёт роль или обновляет пароль из `.env`; права выдаст `migrate`.
    """
    root = repo_root()
    env = {**read_env_file(root / COMPOSE_DIR / ".env"), **os.environ}
    gexec = chr(92) + "gexec"  # метакоманда psql: выполнить результат запроса как SQL
    sql = chr(10).join(
        [
            "SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'name', :'password') "  # noqa: S608 — psql-переменные :'x' экранирует сам psql, gexec — константа
            f"WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'name') {gexec}",
            f"SELECT format('ALTER ROLE %I PASSWORD %L', :'name', :'password') {gexec}",
            f"SELECT format('GRANT CONNECT ON DATABASE %I TO %I', :'owner', :'name') {gexec}",
            "",
        ]
    )
    for service in (s for s in load_services(root) if s.app_role):
        password = env.get(service.db_app_password_env, "")
        if not password:
            typer.secho(f"не задан {service.db_app_password_env}", fg="red")
            raise typer.Exit(1)
        cmd = [
            "docker", "compose", "--env-file", ".env", "-f", "compose.infra.yml", "exec", "-T",
            "-e", f"APP_PASSWORD={password}", "postgres", "sh", "-c",
            'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres '
            f'-v owner={service.name} -v name={service.db_app_user} -v password="$APP_PASSWORD"',
        ]  # fmt: skip
        code = subprocess.run(
            cmd, cwd=root / COMPOSE_DIR, input=sql, text=True, check=False
        ).returncode
        if code != 0:
            raise typer.Exit(code)
        typer.secho(
            f"роль {service.db_app_user} готова; повторите `gv dev up services`", fg="green"
        )


@dev.command()
def logs(
    service: Annotated[str, typer.Argument(help="Имя контейнера, например game")],
    stack: Annotated[str, typer.Option()] = "services",
) -> None:
    """Логи сервиса."""
    raise typer.Exit(_compose(repo_root(), stack, ["logs", "-f", "--tail=200", service]))


@dev.command("run")
def run_local(
    service: Annotated[str, typer.Argument(help="Сервис для запуска локально с hot-reload")],
    command: Annotated[str, typer.Argument()] = "api",
) -> None:
    """Запустить сервис из исходников (инфраструктура — из `gv dev up infra`)."""
    root = repo_root()
    found = next((s for s in load_services(root) if s.name == service), None)
    if found is None:
        raise typer.BadParameter(f"нет сервиса {service}")
    port = os.environ.get("GV_HTTP_PORT", str(found.dev_port))
    typer.echo(f"Переменные GV_* берутся из окружения; API на http://127.0.0.1:{port}")
    env = {
        "GV_HTTP_PORT": port,
        "GV_HTTP_HOST": "127.0.0.1",
        # Порт метрик тоже уникален на сервис: иначе два локальных сервиса делят 9100.
        "GV_METRICS_PORT": os.environ.get("GV_METRICS_PORT", str(found.dev_port + 1000)),
        "GV_LOG_JSON": "false",
        "GV_ENVIRONMENT": os.environ.get("GV_ENVIRONMENT", "local"),
    }
    cmd = ["uv", "run", "--package", found.distribution, found.name, command]
    raise typer.Exit(_run(cmd, root, env))


@dev.command("seed")
def dev_seed(
    *,
    identity_url: Annotated[
        str, typer.Option(envvar="GV_SEED__IDENTITY_URL", help="Базовый адрес identity")
    ] = "http://127.0.0.1:8100",
    catalog_url: Annotated[
        str, typer.Option(envvar="GV_SEED__CATALOG_URL", help="Базовый адрес catalog")
    ] = "http://127.0.0.1:8101",
    media_url: Annotated[
        str, typer.Option(envvar="GV_SEED__MEDIA_URL", help="Базовый адрес media-pipeline")
    ] = "http://127.0.0.1:8102",
    game_url: Annotated[
        str, typer.Option(envvar="GV_SEED__GAME_URL", help="Базовый адрес game")
    ] = "http://127.0.0.1:8103",
    collections_url: Annotated[
        str, typer.Option(envvar="GV_SEED__COLLECTIONS_URL", help="Базовый адрес collections")
    ] = "http://127.0.0.1:8106",
    progression_url: Annotated[
        str, typer.Option(envvar="GV_SEED__PROGRESSION_URL", help="Базовый адрес progression")
    ] = "http://127.0.0.1:8109",
    social_url: Annotated[
        str, typer.Option(envvar="GV_SEED__SOCIAL_URL", help="Базовый адрес social")
    ] = "http://127.0.0.1:8111",
    service_client_id: Annotated[
        str, typer.Option(envvar="GV_SEED__SERVICE_CLIENT_ID", help="Сервисный клиент")
    ] = "admin",
    date_: Annotated[
        str | None,
        typer.Option("--date", help="День плановых наборов YYYY-MM-DD (по умолчанию — сегодня)"),
    ] = None,
) -> None:
    """Наполнить запущенный стек для e2e (дейлики, режимы 3a, кураторские сборники 3b,
    онлайн-шаблоны и сборник лобби 4a; только local/ci, идемпотентно).

    Учётные данные администратора — из окружения ``GV_BOOTSTRAP_ADMIN__LOGIN`` и
    ``GV_BOOTSTRAP_ADMIN__PASSWORD``; секрет сервисного клиента — из
    ``GV_SERVICE_SECRET_<CLIENT>`` (для ``admin`` — ``GV_SERVICE_SECRET_ADMIN``).
    """
    from datetime import date as date_type

    from gv_cli import seed as seed_module

    root = repo_root()
    login = os.environ.get("GV_BOOTSTRAP_ADMIN__LOGIN")
    password = os.environ.get("GV_BOOTSTRAP_ADMIN__PASSWORD")
    secret_env = service_secret_env(service_client_id)
    secret = os.environ.get(secret_env)
    missing = [
        name
        for name, value in (
            ("GV_BOOTSTRAP_ADMIN__LOGIN", login),
            ("GV_BOOTSTRAP_ADMIN__PASSWORD", password),
            (secret_env, secret),
        )
        if not value
    ]
    if missing:
        raise typer.BadParameter(f"нет переменных окружения: {', '.join(missing)}")
    config = seed_module.SeedConfig(
        identity_url=identity_url,
        catalog_url=catalog_url,
        media_url=media_url,
        game_url=game_url,
        collections_url=collections_url,
        progression_url=progression_url,
        social_url=social_url,
        admin_login=login or "",
        admin_password=password or "",
        service_client_id=service_client_id,
        service_secret=secret or "",
        repo_root=root,
        day=date_type.fromisoformat(date_) if date_ else None,
    )
    try:
        seed_module.run_seed(config, echo=lambda msg: typer.secho(msg, fg=typer.colors.CYAN))
    except seed_module.SeedError as exc:
        typer.secho(str(exc), fg="red")
        raise typer.Exit(1) from exc


@dev.command("seed-real")
def dev_seed_real(
    *,
    real_dir: Annotated[
        Path,
        typer.Option("--real-dir", envvar="GV_REAL_MEDIA_DIR", help="Выгрузки tmdb/ и deezer/"),
    ] = Path("C:/gv-media"),
    import_dir: Annotated[
        Path | None,
        typer.Option(
            "--import-dir",
            help="Куда писать real-*.jsonl: каталог, который видит catalog",
        ),
    ] = None,
    ffmpeg: Annotated[
        str | None, typer.Option("--ffmpeg", envvar="GV_FFMPEG", help="Путь к ffmpeg")
    ] = None,
    min_collection_size: Annotated[
        int,
        typer.Option(
            "--min-collection-size",
            min=1,
            help="Минимум единиц в сборнике жанра, категории или десятилетия",
        ),
    ] = 20,
    collection_size: Annotated[
        int, typer.Option("--collection-size", min=1, help="Размер сборника (самые популярные)")
    ] = 50,
    tracks_limit: Annotated[int | None, typer.Option("--tracks-limit")] = None,
    package_units: Annotated[
        int, typer.Option("--package-units", min=1, help="Единиц в одном пакете-архиве")
    ] = 100,
    ffmpeg_jobs: Annotated[
        int,
        typer.Option("--ffmpeg-jobs", min=1, help="Потоков перекодирования клипов"),
    ] = min(8, os.cpu_count() or 4),
    stall_timeout_min: Annotated[
        float,
        typer.Option(
            "--stall-timeout-min",
            min=0.1,
            help="Сколько минут ждать наборы ассетов без единого нового готового набора",
        ),
    ] = 30.0,
    date_: Annotated[str | None, typer.Option("--date", help="День плановых наборов")] = None,
    without_synthetic: Annotated[
        bool,
        typer.Option(
            "--without-synthetic",
            help="Чистый стенд только на реальных данных, без `gv dev seed`: плюс предметы, "
            "события, social и featured-сборники; режимы без материала пропускаются",
        ),
    ] = False,
) -> None:
    """Дополнительный засев реальными открытыми медиа (только local; после ``gv dev seed``).

    С ``--without-synthetic`` команда самодостаточна: синтетика не нужна.
    """
    from datetime import date as date_type

    from gv_cli import seed as seed_module
    from gv_cli import seed_real

    env = os.environ
    secret_env = service_secret_env("admin")
    missing = [
        n
        for n in ("GV_BOOTSTRAP_ADMIN__LOGIN", "GV_BOOTSTRAP_ADMIN__PASSWORD", secret_env)
        if not env.get(n)
    ]
    if missing:
        raise typer.BadParameter(f"нет переменных окружения: {', '.join(missing)}")

    def url(name: str, default: str) -> str:
        return env.get(f"GV_SEED__{name}_URL", default)

    root = repo_root()
    config = seed_module.SeedConfig(
        identity_url=url("IDENTITY", "http://127.0.0.1:8100"),
        catalog_url=url("CATALOG", "http://127.0.0.1:8101"),
        media_url=url("MEDIA", "http://127.0.0.1:8102"),
        game_url=url("GAME", "http://127.0.0.1:8103"),
        collections_url=url("COLLECTIONS", "http://127.0.0.1:8106"),
        progression_url=url("PROGRESSION", "http://127.0.0.1:8109"),
        social_url=url("SOCIAL", "http://127.0.0.1:8111"),
        admin_login=env["GV_BOOTSTRAP_ADMIN__LOGIN"],
        admin_password=env["GV_BOOTSTRAP_ADMIN__PASSWORD"],
        service_secret=env[secret_env],
        repo_root=root,
        day=date_type.fromisoformat(date_) if date_ else None,
        catalog_dir=import_dir,
        import_timeout_s=3000.0,
        assets_timeout_s=900.0,
    )
    try:
        seed_real.run_seed_real(
            config,
            real_dir=real_dir,
            ffmpeg=ffmpeg or seed_real.default_ffmpeg(),
            min_collection_size=min_collection_size,
            collection_size=collection_size,
            tracks_limit=tracks_limit,
            package_units=package_units,
            ffmpeg_jobs=ffmpeg_jobs,
            stall_timeout_s=stall_timeout_min * 60.0,
            without_synthetic=without_synthetic,
            echo=lambda msg: typer.secho(msg, fg=typer.colors.CYAN),
        )
    except seed_module.SeedError as exc:
        typer.secho(str(exc), fg="red")
        raise typer.Exit(1) from exc


def catalog_reindex_args(*, no_switch: bool, rollback: bool, batch: int) -> list[str]:
    """Аргументы ``docker compose`` для ``catalog reindex`` внутри контейнера catalog."""
    if no_switch and rollback:
        raise typer.BadParameter("--no-switch и --rollback несовместимы")
    args = ["exec", "-T", "catalog", "service-entry", "reindex", "--batch", str(batch)]
    if no_switch:
        args.append("--no-switch")
    if rollback:
        args.append("--rollback")
    return args


@dev.command("catalog-reindex")
def dev_catalog_reindex(
    *,
    no_switch: Annotated[
        bool, typer.Option("--no-switch", help="Собрать новый индекс, алиас не переключать")
    ] = False,
    rollback: Annotated[
        bool, typer.Option("--rollback", help="Вернуть алиас на предыдущий индекс")
    ] = False,
    batch: Annotated[int, typer.Option("--batch", min=1, help="Документов в одном bulk")] = 500,
) -> None:
    """Пересобрать поисковый индекс catalog (OpenSearch) и атомарно переключить алиас.

    Идемпотентно и возобновляемо: после обрыва запустите снова. Старый индекс остаётся,
    откат — ``--rollback``. Требует запущенный контейнер catalog.
    """
    args = catalog_reindex_args(no_switch=no_switch, rollback=rollback, batch=batch)
    raise typer.Exit(_compose(repo_root(), "services", args))


@dev.command("replan-assets")
def dev_replan_assets(
    *,
    clue_type: Annotated[
        str, typer.Option("--clue-type", help="Тип загадки, чей профиль правили")
    ] = "frame_progressive",
    batch: Annotated[int, typer.Option("--batch", min=1, help="Единиц за вызов")] = 200,
    no_wait_modes: Annotated[
        bool,
        typer.Option("--no-wait-modes", help="Не ждать новые версии режимов в game"),
    ] = False,
) -> None:
    """Пересоздать наборы ассетов после правки профиля (только local; повтор безопасен).

    Досоздаёт наборы статических профилей типа загадки под новые параметры у уже загруженных
    единиц, ждёт их готовности и новых версий режимов game (сид -> версия при готовом пуле).
    Старые наборы и версии остаются: начатые сессии доигрываются.
    """
    from gv_cli import replan as replan_module
    from gv_cli import seed as seed_module

    env = os.environ
    secret_env = service_secret_env("admin")
    missing = [
        n
        for n in ("GV_BOOTSTRAP_ADMIN__LOGIN", "GV_BOOTSTRAP_ADMIN__PASSWORD", secret_env)
        if not env.get(n)
    ]
    if missing:
        raise typer.BadParameter(f"нет переменных окружения: {', '.join(missing)}")

    def url(name: str, default: str) -> str:
        return env.get(f"GV_SEED__{name}_URL", default)

    config = seed_module.SeedConfig(
        identity_url=url("IDENTITY", "http://127.0.0.1:8100"),
        catalog_url=url("CATALOG", "http://127.0.0.1:8101"),
        media_url=url("MEDIA", "http://127.0.0.1:8102"),
        game_url=url("GAME", "http://127.0.0.1:8103"),
        collections_url=url("COLLECTIONS", "http://127.0.0.1:8106"),
        progression_url=url("PROGRESSION", "http://127.0.0.1:8109"),
        social_url=url("SOCIAL", "http://127.0.0.1:8111"),
        admin_login=env["GV_BOOTSTRAP_ADMIN__LOGIN"],
        admin_password=env["GV_BOOTSTRAP_ADMIN__PASSWORD"],
        service_secret=env[secret_env],
        repo_root=repo_root(),
        assets_timeout_s=900.0,
    )
    try:
        replan_module.run_replan(
            config,
            clue_type=clue_type,
            batch=batch,
            wait_modes=not no_wait_modes,
            echo=lambda msg: typer.secho(msg, fg=typer.colors.CYAN),
        )
    except seed_module.SeedError as exc:
        typer.secho(str(exc), fg="red")
        raise typer.Exit(1) from exc


@dev.command("secrets")
def make_secrets() -> None:
    """Вывести содержимое .env со случайными паролями (сохранить в infra/compose/.env)."""
    root = repo_root()
    template = (root / COMPOSE_DIR / ".env.example").read_text(encoding="utf-8")
    sys.stdout.write(fill_secrets(template))


# Переменные, которые обязаны совпадать: одно значение читают разные процессы.
_SHARED_SECRETS = {"GV_MEDIA__SIGNING_SECRET": "GV_MEDIA_SIGNING_SECRET"}
_SERVICE_CLIENTS_ENV = "GV_SERVICE_CLIENTS"
_JWT_KEYS_ENV = "GV_JWT__SIGNING_KEYS"
# PKCS#8-обёртка закрытого ключа Ed25519 (RFC 8410): фиксированный префикс + 32 байта seed.
_ED25519_PKCS8_PREFIX = bytes.fromhex("302e020100300506032b657004220420")


def jwt_signing_keys() -> str:
    """``GV_JWT__SIGNING_KEYS`` с одним новым Ed25519-ключом (без зависимости от cryptography).

    Ключ стабилен между перезапусками identity — в отличие от временного ключа local/ci,
    после рестарта которого все выданные токены перестают проверяться.
    """
    der = _ED25519_PKCS8_PREFIX + secrets.token_bytes(32)
    body = base64.b64encode(der).decode()
    pem = f"-----BEGIN PRIVATE KEY-----\n{body}\n-----END PRIVATE KEY-----\n"
    key = {"kid": f"local-{secrets.token_hex(4)}", "private_pem": pem, "active": True}
    return json.dumps([key], separators=(",", ":"))


_MANUAL_SECRETS = frozenset({"GV_SMTP__PASSWORD"})
"""Секреты внешних систем: случайная строка вместо настоящего пароля SMTP сломала бы отправку."""


def fill_secrets(template: str) -> str:
    """Заполнить пустые секреты .env.example случайными значениями.

    Секрет — пустая переменная, в имени которой есть PASSWORD/SECRET/TOKEN/KEY, кроме
    ``_MANUAL_SECRETS`` (их задаёт оператор).
    Переменные из ``_SHARED_SECRETS`` получают значение своей пары (секрет подписи
    ссылок нужен и gateway, и media-pipeline — ADR 0012). В ``GV_SERVICE_CLIENTS``
    пустые ``secret_sha256`` заменяются на sha256 сгенерированных
    ``GV_SERVICE_SECRET_<CLIENT>`` (D13: identity хранит только хэши).
    ``GV_JWT__SIGNING_KEYS`` получает настоящий Ed25519-ключ, а не случайную строку.
    """
    secret_keys = ("PASSWORD", "SECRET", "TOKEN", "KEY")
    generated: dict[str, str] = {}
    lines = []
    for line in template.splitlines():
        key, sep, value = line.partition("=")
        needs_secret = sep and not value and not key.startswith("#")
        if needs_secret and key == _JWT_KEYS_ENV:
            lines.append(f"{key}={jwt_signing_keys()}")
        elif needs_secret and key not in _MANUAL_SECRETS and any(k in key for k in secret_keys):
            twin = _SHARED_SECRETS.get(key)
            shared = generated.get(twin) if twin else None
            generated[key] = shared or secrets.token_urlsafe(32)
            lines.append(f"{key}={generated[key]}")
        else:
            lines.append(line)
    return "\n".join(_fill_service_clients(line, generated) for line in lines) + "\n"


def _fill_service_clients(line: str, generated: dict[str, str]) -> str:
    key, sep, value = line.partition("=")
    if key != _SERVICE_CLIENTS_ENV or not sep or not value:
        return line
    clients = json.loads(value)
    for client in clients:
        if client.get("secret_sha256"):
            continue
        secret = generated.get(service_secret_env(client["client_id"]))
        if secret is None:
            raise typer.BadParameter(
                f"нет {service_secret_env(client['client_id'])} для клиента {client['client_id']}"
            )
        client["secret_sha256"] = hashlib.sha256(secret.encode()).hexdigest()
    return f"{key}={json.dumps(clients, separators=(',', ':'))}"


# ----------------------------------------------------------------------------- ci
@ci.command("matrix")
def ci_matrix(base: Annotated[str, typer.Option()] = "origin/main") -> None:
    """JSON со списком затронутых модулей: [{"name", "path", "service"}]."""
    root = repo_root()
    members = workspace.load_members(root)
    names = workspace.changed_members(root, base)
    service_names = {s.distribution: s.name for s in load_services(root)}
    include = [
        {"name": n, "path": str(members[n].path), "service": service_names.get(n, "")}
        for n in sorted(names)
        if (root / members[n].path / "tests").is_dir()
    ]
    sys.stdout.write(json.dumps({"include": include}, ensure_ascii=False))


# ----------------------------------------------------------------------------- contracts
@contracts_app.command("export")
def contracts_export() -> None:
    """Перегенерировать снимок контрактов из кода сервисов."""
    root = repo_root()
    changed = contracts.write_all(root, load_services(root))
    if changed:
        typer.echo("Обновлено:\n  " + "\n  ".join(map(str, changed)))
    else:
        typer.echo("Контракты актуальны")


@contracts_app.command("check")
def contracts_check(
    base: Annotated[
        str | None, typer.Option(help="Ревизия для проверки совместимости (CI: origin/main)")
    ] = None,
) -> None:
    """Снимок совпадает с кодом и (если задана база) нет ломающих изменений."""
    root = repo_root()
    failed = False
    outdated = contracts.stale(root, load_services(root))
    if outdated:
        failed = True
        typer.secho(
            "Снимок контрактов устарел — выполните `gv contracts export` и закоммитьте:\n  "
            + "\n  ".join(map(str, outdated)),
            fg="red",
        )
    if base is not None:
        problems = contracts.compare_with_base(root, base, load_services(root))
        if problems:
            failed = True
            typer.secho(
                f"Ломающие изменения относительно {base} (нужна новая версия контракта):\n  "
                + "\n  ".join(map(str, problems)),
                fg="red",
            )
    if failed:
        raise typer.Exit(1)
    typer.secho("Контракты в порядке", fg="green")


# ----------------------------------------------------------------------------- hooks
HOOK_NAMES = (
    "guard-edit",
    "guard-bash",
    "format",
    "stop",
    "compact",
    "session-start",
    "session-end",
    "pre-compact",
)


@app.command("hook", hidden=True)
def hook(name: Annotated[str, typer.Argument(help=" | ".join(HOOK_NAMES))]) -> None:
    """Хук Claude Code: событие — JSON в stdin, решение — код выхода (2 = блокировать)."""
    raise typer.Exit(hooks.run(name, repo_root()))
