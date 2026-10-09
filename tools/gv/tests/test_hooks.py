"""Тесты хуков Claude Code: что блокируется, что пропускается, что пишется в заметки."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from gv_cli import hooks

ROOT = Path(__file__).resolve().parents[3]

pytestmark = pytest.mark.unit


def _edit(path: str, tool: str = "Edit") -> dict[str, Any]:
    return {"tool_name": tool, "tool_input": {"file_path": str(ROOT / path)}}


class TestGuardEdit:
    @pytest.mark.parametrize(
        "path",
        [
            "infra/nginx/generated/routes.conf",
            "infra/compose/compose.services.yml",
            ".importlinter",
            "contracts/openapi/game.json",
            "contracts/events/envelope.v1.json",
            "infra/compose/.env",
            "infra/compose/.env.example",  # тоже генерируется из реестра
            "uv.lock",
        ],
    )
    def test_protected_files_are_blocked(self, path: str) -> None:
        decision = hooks.decide_edit(ROOT, _edit(path, "Write"))
        assert decision.exit_code == 2
        assert decision.stderr  # Claude получает объяснение, что делать вместо этого

    @pytest.mark.parametrize(
        "path",
        [
            "services/game/src/game_service/main.py",
            "contracts/events/game/RoundScored.v1.json",  # схемы событий пишутся руками
            "infra/compose/compose.infra.yml",
            "infra/services.toml",
            "docs/README.md",
        ],
    )
    def test_regular_files_are_allowed(self, path: str) -> None:
        assert hooks.decide_edit(ROOT, _edit(path)).exit_code == 0

    def test_files_outside_repo_and_other_tools_are_ignored(self) -> None:
        assert hooks.decide_edit(ROOT, _edit("/tmp/x/uv.lock")).exit_code == 0  # noqa: S108
        read = {"tool_name": "Read", "tool_input": {"file_path": str(ROOT / "uv.lock")}}
        assert hooks.decide_edit(ROOT, read).exit_code == 0


class TestGuardBash:
    @pytest.mark.parametrize(
        "command",
        [
            "git push --force origin main",
            "git push -f",
            "git push --force-with-lease origin feat/x",
            "git commit -m x --no-verify",
            "docker compose -f a.yml down -v",
            "docker compose down --volumes",
            "git reset --hard HEAD~1",
            "git clean -fd",
        ],
    )
    def test_dangerous_commands_are_blocked(self, command: str) -> None:
        payload = {"tool_name": "Bash", "tool_input": {"command": command}}
        assert hooks.decide_bash(payload).exit_code == 2

    @pytest.mark.parametrize(
        "command",
        ["git push -u origin feat/x", "docker compose down", "uv run gv test game", "git status"],
    )
    def test_regular_commands_are_allowed(self, command: str) -> None:
        payload = {"tool_name": "Bash", "tool_input": {"command": command}}
        assert hooks.decide_bash(payload).exit_code == 0


class TestFormat:
    def test_non_python_and_missing_files_are_skipped(self) -> None:
        assert hooks.decide_format(ROOT, _edit("docs/README.md")).exit_code == 0
        assert hooks.decide_format(ROOT, _edit("services/nope.py")).exit_code == 0

    def test_formats_and_reports_unfixable_issues(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text('[tool.ruff.lint]\nselect = ["F"]\n')
        module = tmp_path / "m.py"
        module.write_text("import os\nx=1\nprint(undefined_name)\n")
        decision = hooks.decide_format(
            tmp_path, {"tool_name": "Write", "tool_input": {"file_path": str(module)}}
        )
        assert module.read_text().startswith("x = 1")  # формат + автоудаление импорта
        assert decision.exit_code == 2
        assert "undefined_name" in decision.stderr  # неисправимое — Claude на доработку


class TestStop:
    def test_reports_debug_leftovers_only_in_sources(self, tmp_path: Path) -> None:
        src = tmp_path / "services/game/src/game_service"
        src.mkdir(parents=True)
        (src / "a.py").write_text("def f():\n    print('x')\n    breakpoint()\n")
        tests = tmp_path / "services/game/tests"
        tests.mkdir(parents=True)
        (tests / "t.py").write_text("print('ok in tests')\n")
        decision = hooks.decide_stop(
            tmp_path,
            ["services/game/src/game_service/a.py", "services/game/tests/t.py", "gone.py"],
        )
        message = json.loads(decision.stdout)["systemMessage"]
        assert "a.py:2" in message and "a.py:3" in message
        assert "t.py" not in message
        assert hooks.decide_stop(tmp_path, []).stdout == ""


class TestCompact:
    def test_suggests_at_threshold_and_then_periodically(self, tmp_path: Path) -> None:
        notices = [
            n
            for n in range(1, 101)
            if hooks.decide_compact("s/1", tmp_path).stdout  # id с «/» безопасно экранируется
        ]
        assert notices == [hooks.COMPACT_FIRST, 75, 100]


class TestSessions:
    @staticmethod
    def _repo(tmp_path: Path) -> Path:
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs/09-roadmap.md").write_text(
            "# Roadmap\n## Этап 0. Фундамент — ✅\n## Этап 1. Аккаунт\n"
        )
        return tmp_path

    def test_session_end_creates_then_updates_note(self, tmp_path: Path) -> None:
        root = self._repo(tmp_path)
        first = hooks.session_end(root, datetime(2026, 10, 9, 10, 0, tzinfo=UTC))
        text = first.read_text()
        assert "## Для следующей сессии" in text and "10:00 UTC" in text
        first.write_text(text.replace("## Для следующей сессии\n-", "## Для следующей сессии\n- X"))
        again = hooks.session_end(root, datetime(2026, 10, 9, 18, 30, tzinfo=UTC))
        assert again == first
        assert "18:30 UTC" in again.read_text() and "- X" in again.read_text()

    def test_session_start_shows_stage_and_notes(self, tmp_path: Path) -> None:
        root = self._repo(tmp_path)
        note = hooks.session_end(root, datetime(2026, 10, 9, tzinfo=UTC))
        note.write_text(
            note.read_text().replace(
                "## Для следующей сессии\n-", "## Для следующей сессии\n- доделать JWKS"
            )
        )
        context = hooks.session_start_context(root)
        assert "Этап 1. Аккаунт" in context
        assert "доделать JWKS" in context


def test_run_never_crashes_on_garbage(monkeypatch: pytest.MonkeyPatch) -> None:
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    assert hooks.run("guard-bash", ROOT) == 0
    monkeypatch.setattr("sys.stdin", io.StringIO("{}"))
    assert hooks.run("unknown-hook", ROOT) == 1
