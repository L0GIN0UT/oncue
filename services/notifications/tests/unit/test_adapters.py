"""Адаптеры писем и Push: log, фон, SMTP, заглушки."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from notifications_service.application.ports import EmailMessage, PermanentEmailError
from notifications_service.infrastructure import email as email_module
from notifications_service.infrastructure.email import (
    BackgroundEmailSender,
    LogEmailSender,
    NullPushSender,
    SmtpEmailSender,
    UnconfiguredEmailDirectory,
)
from notifications_service.settings import SmtpSettings
from pydantic import SecretStr

from gv_core.errors import DependencyUnavailableError
from gv_core.ids import uuid7

pytestmark = pytest.mark.unit

MESSAGE = EmailMessage(to="player@example.test", subject="Тема", body="Тело письма")


class Recorder:
    def __init__(self, *, fail: bool = False, gate: asyncio.Event | None = None) -> None:
        self.sent: list[EmailMessage] = []
        self.fail = fail
        self.gate = gate

    async def send(self, message: EmailMessage) -> None:
        if self.gate is not None:
            await self.gate.wait()
        if self.fail:
            raise ConnectionError("smtp down")
        self.sent.append(message)


async def test_log_sender_never_logs_address(capsys: pytest.CaptureFixture[str]) -> None:
    await LogEmailSender(include_body=True).send(MESSAGE)
    out = capsys.readouterr().out + capsys.readouterr().err
    assert "player@example.test" not in out


async def test_background_sender_returns_before_delivery_and_drains() -> None:
    gate = asyncio.Event()
    inner = Recorder(gate=gate)
    sender = BackgroundEmailSender(inner)
    await sender.send(MESSAGE)
    assert inner.sent == []  # ответ не ждёт SMTP
    gate.set()
    await sender.drain()
    assert inner.sent == [MESSAGE]


async def test_background_sender_swallows_failures() -> None:
    sender = BackgroundEmailSender(Recorder(fail=True))
    await sender.send(MESSAGE)
    await sender.drain()  # исключение наружу не вышло


async def test_background_sender_is_synchronous_when_queue_is_full() -> None:
    gate = asyncio.Event()
    inner = Recorder(gate=gate)
    sender = BackgroundEmailSender(inner, max_pending=1)
    await sender.send(MESSAGE)  # занял единственное место
    second = asyncio.create_task(sender.send(MESSAGE))  # идёт синхронно и ждёт gate
    await asyncio.sleep(0)
    assert not second.done()
    gate.set()
    await second
    await sender.drain()
    assert len(inner.sent) == 2


async def test_smtp_sender_builds_message_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[Any, dict[str, Any]]] = []

    async def fake_send(message: Any, **kwargs: Any) -> None:
        calls.append((message, kwargs))

    monkeypatch.setattr(email_module.aiosmtplib, "send", fake_send)
    settings = SmtpSettings(
        host="smtp.example.test",
        port=2525,
        sender="Guessverse <no-reply@example.test>",
        username="u",
        password=SecretStr("p"),
    )
    await SmtpEmailSender(settings).send(MESSAGE)

    [(mime, kwargs)] = calls
    assert mime["To"] == "player@example.test"
    assert mime["From"] == "Guessverse <no-reply@example.test>"
    assert mime["Subject"] == "Тема"
    assert "Тело письма" in mime.get_content()
    assert kwargs["hostname"] == "smtp.example.test"
    assert kwargs["password"] == "p"
    assert kwargs["start_tls"] is True


async def test_smtp_sender_sets_list_unsubscribe_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Any] = []

    async def fake_send(message: Any, **kwargs: Any) -> None:
        calls.append(message)

    monkeypatch.setattr(email_module.aiosmtplib, "send", fake_send)
    message = EmailMessage(
        to="player@example.test",
        subject="Тема",
        body="Тело",
        headers={
            "List-Unsubscribe": "<https://guessverse.example/unsubscribe?token=abc>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        },
    )
    await SmtpEmailSender(SmtpSettings()).send(message)
    [mime] = calls
    assert mime["List-Unsubscribe"] == "<https://guessverse.example/unsubscribe?token=abc>"
    assert mime["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"


async def test_smtp_recipient_refusal_is_permanent_other_errors_are_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    errors: list[Exception] = [
        email_module.aiosmtplib.SMTPRecipientRefused(550, "no such user", "player@example.test"),
        email_module.aiosmtplib.SMTPRecipientsRefused([]),
    ]

    async def refusing(message: Any, **kwargs: Any) -> None:
        raise errors.pop(0)

    monkeypatch.setattr(email_module.aiosmtplib, "send", refusing)
    sender = SmtpEmailSender(SmtpSettings())
    for _ in range(2):
        with pytest.raises(PermanentEmailError):
            await sender.send(MESSAGE)

    async def auth_failure(message: Any, **kwargs: Any) -> None:
        raise email_module.aiosmtplib.SMTPAuthenticationError(535, "bad credentials")

    monkeypatch.setattr(email_module.aiosmtplib, "send", auth_failure)
    with pytest.raises(email_module.aiosmtplib.SMTPAuthenticationError):
        await sender.send(MESSAGE)  # проблема нашей стороны: повторяется, а не «окончательно»


async def test_unconfigured_directory_signals_dependency_unavailable() -> None:
    with pytest.raises(DependencyUnavailableError) as raised:
        await UnconfiguredEmailDirectory().resolve([uuid7()])
    assert raised.value.code == "notifications.email_directory_unconfigured"


async def test_null_push_sender_does_nothing() -> None:
    assert await NullPushSender().send(uuid7(), "t", "b") is None
