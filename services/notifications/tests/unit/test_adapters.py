"""Адаптеры писем и Push: log, фон, SMTP, заглушки."""

from __future__ import annotations

from typing import Any

import pytest
from notifications_service.application.ports import EmailMessage, PermanentEmailError
from notifications_service.infrastructure.email import (
    GvEmailSender,
    NullPushSender,
    UnconfiguredEmailDirectory,
    smtp_transport,
)
from notifications_service.settings import SmtpSettings
from pydantic import SecretStr

from gv_core.errors import DependencyUnavailableError
from gv_core.ids import uuid7
from gv_email import LogEmailSender, senders

pytestmark = pytest.mark.unit

MESSAGE = EmailMessage(to="player@example.test", subject="Тема", body="Тело письма")


async def test_sender_adapter_passes_message_and_headers_to_smtp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Транспорт покрыт тестами libs/email; здесь - перенос полей письма и настроек SMTP."""
    calls: list[tuple[Any, dict[str, Any]]] = []

    async def fake_send(message: Any, **kwargs: Any) -> None:
        calls.append((message, kwargs))

    monkeypatch.setattr(senders.aiosmtplib, "send", fake_send)
    settings = SmtpSettings(
        host="smtp.example.test",
        port=2525,
        sender="Guessverse <no-reply@example.test>",
        username="u",
        password=SecretStr("p"),
    )
    message = EmailMessage(
        to="player@example.test",
        subject="Тема",
        body="Тело письма",
        headers={
            "List-Unsubscribe": "<https://guessverse.example/unsubscribe?token=abc>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        },
    )
    await GvEmailSender(smtp_transport(settings)).send(message)

    [(mime, kwargs)] = calls
    assert mime["To"] == "player@example.test"
    assert mime["From"] == "Guessverse <no-reply@example.test>"
    assert mime["Subject"] == "Тема"
    assert "Тело письма" in mime.get_content()
    assert mime["List-Unsubscribe"] == "<https://guessverse.example/unsubscribe?token=abc>"
    assert mime["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert kwargs["hostname"] == "smtp.example.test"
    assert kwargs["password"] == "p"
    assert kwargs["start_tls"] is True


async def test_smtp_recipient_refusal_reaches_the_use_case_as_permanent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def refusing(message: Any, **kwargs: Any) -> None:
        raise senders.aiosmtplib.SMTPRecipientRefused(550, "no such user", "player@example.test")

    monkeypatch.setattr(senders.aiosmtplib, "send", refusing)
    with pytest.raises(PermanentEmailError):  # тот же класс, что ловит рассылка (ports)
        await GvEmailSender(smtp_transport(SmtpSettings())).send(MESSAGE)


async def test_log_transport_never_logs_address(capsys: pytest.CaptureFixture[str]) -> None:
    await GvEmailSender(LogEmailSender(include_secrets=True, log_body=True)).send(MESSAGE)
    out = capsys.readouterr().out + capsys.readouterr().err
    assert "player@example.test" not in out


async def test_unconfigured_directory_signals_dependency_unavailable() -> None:
    with pytest.raises(DependencyUnavailableError) as raised:
        await UnconfiguredEmailDirectory().resolve([uuid7()])
    assert raised.value.code == "notifications.email_directory_unconfigured"


async def test_null_push_sender_does_nothing() -> None:
    assert await NullPushSender().send(uuid7(), "t", "b") is None
