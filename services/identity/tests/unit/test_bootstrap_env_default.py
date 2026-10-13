"""Дефолтный bootstrap-логин из ``infra/compose/.env.example`` обязан проходить тот же
парсер e-mail, что identity вызывает на старте (``migrate`` → bootstrap-админ).

Регресс: домен ``.local`` синтаксически похож на e-mail, но ``email_validator`` его
отклоняет как спец-имя — identity-migrate падал на валидации, и стек не поднимался.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from identity_service.domain import EmailAddress

pytestmark = pytest.mark.unit


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "infra" / "compose" / ".env.example").is_file():
            return parent
    raise AssertionError("не найден infra/compose/.env.example")


def test_bootstrap_default_login_is_valid_email() -> None:
    """Логин bootstrap-админа из .env.example парсится как корректный e-mail."""
    env = (_repo_root() / "infra" / "compose" / ".env.example").read_text(encoding="utf-8")
    line = next(
        (ln for ln in env.splitlines() if ln.startswith("GV_BOOTSTRAP_ADMIN__LOGIN=")), None
    )
    assert line is not None, "в .env.example нет GV_BOOTSTRAP_ADMIN__LOGIN"
    login = line.split("=", 1)[1].strip()
    assert login, "GV_BOOTSTRAP_ADMIN__LOGIN в .env.example пуст"
    # Не должно бросать InvalidInputError — иначе identity-migrate упадёт на старте.
    EmailAddress.parse(login)
