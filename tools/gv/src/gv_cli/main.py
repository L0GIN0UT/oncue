"""gv — CLI разработчика Guessverse.

gv gen [--check]                 сгенерировать конфиги из infra/services.toml
gv new-service NAME ...          создать сервис из шаблона и добавить в реестр
gv test [TARGET] [--level L]     тесты модуля/сервиса/всего репо
gv test --changed                тесты только затронутых изменениями модулей
gv lint                          ruff, mypy, import-linter, gen --check
gv dev up|down|ps|logs ...       локальное окружение (docker compose)
gv dev secrets                   сгенерировать .env со случайными паролями
gv contracts export              снимок OpenAPI и схем событий в contracts/
gv contracts check [--base REF]  актуальность снимка и отсутствие ломающих изменений
gv ci matrix                     JSON-матрица изменённых модулей для GitHub Actions
gv hook NAME                     хук Claude Code (событие в stdin), см. .claude/settings.json
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
from pathlib import Path
from typing import Annotated

import typer

from gv_cli import contracts, generate, hooks, scaffold, workspace
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


@app.command()
def test(
    target: Annotated[str | None, typer.Argument(help="Сервис, библиотека или путь")] = None,
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
        members = workspace.load_members(root)
        names = workspace.changed_members(root, base)
        paths = [
            str(members[n].path)
            for n in sorted(names)
            if (root / members[n].path / "tests").is_dir()
        ]
        if not paths:
            typer.echo("Нет затронутых модулей с тестами")
            return
    elif target:
        paths = [str(_resolve_target(root, target))]
    else:
        paths = ["libs", "services", "tools"]
    cmd = ["uv", "run", "pytest", *paths]
    if level:
        cmd += ["-m", level]
    if cov:
        cmd += ["--cov", "--cov-report=term-missing", "--cov-report=xml"]
    raise typer.Exit(_run(cmd, root))


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
        "GV_LOG_JSON": "false",
        "GV_ENVIRONMENT": os.environ.get("GV_ENVIRONMENT", "local"),
    }
    cmd = ["uv", "run", "--package", found.distribution, found.name, command]
    raise typer.Exit(_run(cmd, root, env))


@dev.command("secrets")
def make_secrets() -> None:
    """Вывести содержимое .env со случайными паролями (сохранить в infra/compose/.env)."""
    root = repo_root()
    template = (root / COMPOSE_DIR / ".env.example").read_text(encoding="utf-8")
    secret_keys = ("PASSWORD", "SECRET", "TOKEN", "KEY")
    lines = []
    for line in template.splitlines():
        key, sep, value = line.partition("=")
        needs_secret = sep and not value and not key.startswith("#")
        if needs_secret and any(k in key for k in secret_keys):
            lines.append(f"{key}={secrets.token_urlsafe(24)}")
        else:
            lines.append(line)
    sys.stdout.write("\n".join(lines) + "\n")


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
        problems = contracts.compare_with_base(root, base)
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
