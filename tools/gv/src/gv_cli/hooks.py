"""Хуки Claude Code для Guessverse (адаптация идей everything-claude-code).

Каждый хук — функция «вход хука → решение». Claude Code передаёт JSON события в stdin
и смотрит на код выхода:

* ``0`` — продолжить; stdout у ``SessionStart`` добавляется в контекст Claude;
  JSON ``{"systemMessage": ...}`` в stdout показывается пользователю;
* ``2`` — заблокировать действие (PreToolUse) или вернуть Claude задачу на исправление
  (PostToolUse); текст из stderr уходит Claude как объяснение.

Логика вынесена в чистые функции (``decide_*``), чтобы её покрывали обычные unit-тесты,
а не ручная проверка в живой сессии. Хуки обязаны быть быстрыми и никогда не падать:
любая внутренняя ошибка = «пропустить», а не «заблокировать работу».
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from gv_cli import contracts, generate

SESSIONS_DIR = Path(".claude/sessions")
ROADMAP = Path("docs/09-roadmap.md")
COMPACT_FIRST = 50
COMPACT_EVERY = 25

EDIT_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})


@dataclass(frozen=True, slots=True)
class Decision:
    """Результат хука: код выхода и сообщения."""

    exit_code: int = 0
    stderr: str = ""
    stdout: str = ""

    @classmethod
    def allow(cls, stdout: str = "") -> Decision:
        return cls(0, "", stdout)

    @classmethod
    def block(cls, reason: str) -> Decision:
        return cls(2, reason, "")

    @classmethod
    def notify(cls, message: str) -> Decision:
        """Не блокировать, но показать пользователю сообщение."""
        return cls(0, "", json.dumps({"systemMessage": message}, ensure_ascii=False))


# ----------------------------------------------------------------------------- utils
def _relative(root: Path, file_path: str) -> str | None:
    """Путь файла относительно корня репо в POSIX-виде (или None, если вне репо)."""
    try:
        rel = Path(file_path).resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return None
    return PurePosixPath(rel).as_posix()


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=False, timeout=5
    )
    return result.stdout.strip() if result.returncode == 0 else ""


# ----------------------------------------------------------------------------- guard-edit
_GENERATED = {t.path for t in generate.TARGETS}
_CONTRACT_SNAPSHOT_PREFIX = contracts.OPENAPI_DIR.as_posix() + "/"
_ENVELOPE = contracts.ENVELOPE_PATH.as_posix()
_ENV_FILE = re.compile(r"(^|/)\.env(\.[^/]+)?$")


def protected_reason(rel: str) -> str | None:
    """Почему файл нельзя править руками (``None`` — можно)."""
    if rel in _GENERATED:
        return (
            f"{rel} генерируется `gv gen` из infra/services.toml. Измените реестр или шаблон "
            "в tools/gv/src/gv_cli/templates/gen/ и выполните `uv run gv gen`."
        )
    if rel.startswith(_CONTRACT_SNAPSHOT_PREFIX) or rel == _ENVELOPE:
        return (
            f"{rel} — снимок контракта из кода. Измените роутеры/модели и выполните "
            "`uv run gv contracts export`. Схемы доменных событий "
            "(contracts/events/<service>/...) пишутся руками — их править можно."
        )
    if _ENV_FILE.search(rel):
        return (
            f"{rel} содержит секреты и не редактируется ассистентом. Новые переменные "
            "добавляйте через реестр сервисов и шаблон env.example (`gv gen`)."
        )
    if rel == "uv.lock":
        return "uv.lock меняется только через `uv add` / `uv lock`."
    return None


def decide_edit(root: Path, payload: dict[str, Any]) -> Decision:
    """PreToolUse(Edit|Write|...): защищает файлы, которые нельзя править руками."""
    if payload.get("tool_name") not in EDIT_TOOLS:
        return Decision.allow()
    tool_input = payload.get("tool_input") or {}
    raw = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
    rel = _relative(root, raw) if raw else None
    reason = protected_reason(rel) if rel is not None else None
    return Decision.block(reason) if reason else Decision.allow()


# ----------------------------------------------------------------------------- guard-bash
_BASH_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"\bgit\s+push\b.*(\s-f\b|--force\b|--force-with-lease\b)"),
        "Принудительный push запрещён: история main и общих веток неизменяема. "
        "Если это действительно нужно — попросите пользователя сделать это самому.",
    ),
    (
        re.compile(r"\bgit\s+(commit|push)\b.*--no-verify\b"),
        "--no-verify обходит pre-commit проверки. Исправьте замечания вместо обхода.",
    ),
    (
        re.compile(r"\bdocker\s+compose\b.*\bdown\b.*(\s-v\b|--volumes\b)"),
        "`down -v` удаляет тома с данными (Postgres, Redis, MinIO). Остановите без -v "
        "или попросите пользователя подтвердить удаление данных.",
    ),
    (
        re.compile(r"\bgit\s+reset\s+--hard\b|\bgit\s+clean\s+-[a-z]*f"),
        "Команда безвозвратно удаляет незакоммиченную работу. Сначала `git stash` "
        "или попросите пользователя подтвердить.",
    ),
)


def decide_bash(payload: dict[str, Any]) -> Decision:
    """PreToolUse(Bash): блокирует необратимые и обходящие проверки команды."""
    if payload.get("tool_name") != "Bash":
        return Decision.allow()
    command = str((payload.get("tool_input") or {}).get("command", ""))
    for pattern, reason in _BASH_RULES:
        if pattern.search(command):
            return Decision.block(reason)
    return Decision.allow()


# ----------------------------------------------------------------------------- format
def _venv_tool(name: str) -> str:
    candidate = Path(sys.executable).parent / name
    return str(candidate) if candidate.exists() else name


def decide_format(root: Path, payload: dict[str, Any]) -> Decision:
    """PostToolUse(Edit|Write): ruff format + автоисправления; остаток — Claude на доработку."""
    if payload.get("tool_name") not in EDIT_TOOLS:
        return Decision.allow()
    raw = (payload.get("tool_input") or {}).get("file_path") or ""
    rel = _relative(root, raw) if raw else None
    if rel is None or not rel.endswith(".py") or not (root / rel).is_file():
        return Decision.allow()
    ruff = _venv_tool("ruff")
    try:
        # Сначала автоисправления (удалённый импорт оставляет пустые строки), затем формат.
        subprocess.run([ruff, "check", "--fix", "--quiet", rel], cwd=root, check=False, timeout=20)
        subprocess.run([ruff, "format", "--quiet", rel], cwd=root, check=False, timeout=20)
        result = subprocess.run(
            [ruff, "check", "--quiet", "--output-format", "concise", rel],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return Decision.allow()  # нет ruff или завис — не мешаем работе
    if result.returncode != 0 and result.stdout.strip():
        issues = "\n".join(result.stdout.strip().splitlines()[:15])
        return Decision.block(f"ruff нашёл замечания, исправьте их:\n{issues}")
    return Decision.allow()


# ----------------------------------------------------------------------------- stop
_DEBUG_LEFTOVERS = re.compile(r"^\s*(print\(|breakpoint\(\)|import pdb|pdb\.set_trace)")


def decide_stop(root: Path, changed: list[str]) -> Decision:
    """Stop: напомнить об отладочном коде в изменённых исходниках (не в тестах и tools)."""
    findings: list[str] = []
    for rel in changed:
        if not rel.endswith(".py") or "/tests/" in rel or rel.startswith("tools/"):
            continue
        path = root / rel
        if not path.is_file():
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _DEBUG_LEFTOVERS.match(line):
                findings.append(f"{rel}:{number}: {line.strip()}")
    if not findings:
        return Decision.allow()
    return Decision.notify(
        "Отладочный код в изменённых файлах (используйте structlog):\n" + "\n".join(findings[:10])
    )


# ----------------------------------------------------------------------------- compact
def decide_compact(session_id: str, counter_dir: Path) -> Decision:
    """PreToolUse(*): после N вызовов инструментов предложить осознанный /compact."""
    safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", session_id)[:64] or "default"
    counter = counter_dir / f"gv-claude-tools-{safe_id}"
    try:
        count = int(counter.read_text()) + 1 if counter.exists() else 1
        counter.write_text(str(count))
    except (OSError, ValueError):
        return Decision.allow()
    if count == COMPACT_FIRST or (count > COMPACT_FIRST and count % COMPACT_EVERY == 0):
        return Decision.notify(
            f"{count} вызовов инструментов в сессии. Если этап задачи завершён — хороший "
            "момент: /checkpoint, затем /compact (см. скилл strategic-compact)."
        )
    return Decision.allow()


# ----------------------------------------------------------------------------- sessions
def _latest_session(root: Path) -> Path | None:
    files = sorted((root / SESSIONS_DIR).glob("*.md"))
    return files[-1] if files else None


def _section(text: str, title: str) -> str:
    match = re.search(rf"^## {re.escape(title)}\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    return match.group(1).strip() if match else ""


def _current_stage(root: Path) -> str:
    path = root / ROADMAP
    if not path.is_file():
        return ""
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## Этап") and "✅" not in line:
            return line.removeprefix("## ").strip()
    return ""


def session_start_context(root: Path) -> str:
    """SessionStart: короткая сводка в контекст (вместо перечитывания всего репозитория)."""
    lines = ["[Guessverse] Контекст сессии:"]
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    dirty = _git(root, "status", "--porcelain")
    if branch:
        changed = len(dirty.splitlines()) if dirty else 0
        lines.append(f"- ветка: {branch}; незакоммиченных файлов: {changed}")
    stage = _current_stage(root)
    if stage:
        lines.append(f"- текущий этап roadmap: {stage}")
    latest = _latest_session(root)
    if latest is not None:
        notes = _section(latest.read_text(encoding="utf-8"), "Для следующей сессии")
        lines.append(f"- последняя заметка: {latest.relative_to(root).as_posix()}")
        if notes and notes != "-":
            lines.extend(f"  {line}" for line in notes.splitlines()[:10])
    lines.append("- начните с CLAUDE.md; скиллы и агенты по ситуации — docs/10-ai-assistant.md")
    return "\n".join(lines)


SESSION_TEMPLATE = """# Сессия {date}

- Ветка: {branch}
- Последний коммит: {commit}
- Обновлено: {time}

## Сделано
-

## В процессе
-

## Для следующей сессии
-

## Изменённые файлы
{changed}
"""


def session_end(root: Path, now: datetime | None = None) -> Path:
    """SessionEnd/PreCompact: создать или обновить заметку дня (заполняет /checkpoint)."""
    now = now or datetime.now(UTC)
    directory = root / SESSIONS_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{now:%Y-%m-%d}.md"
    dirty = _git(root, "status", "--porcelain")
    changed = "\n".join(f"- `{line[3:]}`" for line in dirty.splitlines()[:30]) or "-"
    if path.exists():
        text = path.read_text(encoding="utf-8")
        text = re.sub(r"^- Обновлено: .*$", f"- Обновлено: {now:%H:%M} UTC", text, flags=re.M)
        text = re.sub(r"(^## Изменённые файлы\n).*", rf"\g<1>{changed}\n", text, flags=re.M | re.S)
        path.write_text(text, encoding="utf-8")
    else:
        path.write_text(
            SESSION_TEMPLATE.format(
                date=f"{now:%Y-%m-%d}",
                branch=_git(root, "rev-parse", "--abbrev-ref", "HEAD") or "-",
                commit=_git(root, "log", "-1", "--format=%h %s") or "-",
                time=f"{now:%H:%M} UTC",
                changed=changed,
            ),
            encoding="utf-8",
        )
    return path


def changed_files(root: Path) -> list[str]:
    """Изменённые и новые файлы рабочей копии."""
    out = _git(root, "status", "--porcelain")
    return [line[3:].split(" -> ")[-1] for line in out.splitlines() if line]


# ----------------------------------------------------------------------------- runner
def read_payload() -> dict[str, Any]:
    """Прочитать JSON события из stdin (пустой/битый вход — пустой словарь)."""
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def emit(decision: Decision) -> int:
    """Вывести результат и вернуть код выхода."""
    if decision.stdout:
        sys.stdout.write(decision.stdout + "\n")
    if decision.stderr:
        sys.stderr.write(decision.stderr + "\n")
    return decision.exit_code


def _compact(_root: Path, payload: dict[str, Any]) -> Decision:
    session = str(payload.get("session_id") or "default")
    return decide_compact(session, Path(tempfile.gettempdir()))


def _record_session(root: Path, _payload: dict[str, Any]) -> Decision:
    session_end(root)
    return Decision.allow()


_HANDLERS: dict[str, Callable[[Path, dict[str, Any]], Decision]] = {
    "guard-edit": decide_edit,
    "guard-bash": lambda _root, payload: decide_bash(payload),
    "format": decide_format,
    "stop": lambda root, _payload: decide_stop(root, changed_files(root)),
    "compact": _compact,
    "session-start": lambda root, _payload: Decision.allow(session_start_context(root)),
    "session-end": _record_session,
    "pre-compact": _record_session,
}


HANDLER_NAMES = tuple(_HANDLERS)


def run(name: str, root: Path) -> int:
    """Выполнить хук по имени. Любая внутренняя ошибка не блокирует работу."""
    handler = _HANDLERS.get(name)
    if handler is None:
        sys.stderr.write(f"неизвестный хук: {name}\n")
        return 1
    try:
        decision = handler(root, read_payload())
    except Exception as exc:
        # Хук не должен ломать работу ассистента: ошибка = пропуск, а не блокировка.
        sys.stderr.write(f"[gv hook {name}] пропущен из-за ошибки: {exc!r}\n")
        return 1
    return emit(decision)
