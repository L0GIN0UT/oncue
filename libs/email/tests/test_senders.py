"""Отправители писем: лог без секретов, SMTP, фоновая отправка.

Перенесено из тестов identity и notifications без ослабления проверок.
"""

from __future__ import annotations

import asyncio
from email.message import EmailMessage as MimeMessage
from typing import Any

import pytest
from structlog.testing import capture_logs

from gv_email import (
    BackgroundEmailSender,
    LogEmailSender,
    OutgoingEmail,
    PermanentEmailError,
    SmtpEmailSender,
)
from gv_email import senders as senders_module

pytestmark = pytest.mark.unit

ADDRESS = "neo@example.com"


def _message(**kw: Any) -> OutgoingEmail:
    defaults: dict[str, Any] = {"to": ADDRESS, "subject": "Тема", "body": "Тело письма"}
    return OutgoingEmail(**{**defaults, **kw})


# ------------------------------------------------------------------------------------- лог


async def test_log_sender_hides_secret_fields_without_include_secrets() -> None:
    message = _message(
        body="код 123456",
        log_fields={"purpose": "code_login"},
        secret_log_fields={"code": "123456", "link": "https://x/?token=SECRET"},
    )
    with capture_logs() as logs:
        await LogEmailSender().send(message)
        await LogEmailSender(include_secrets=False, log_body=True).send(message)
    assert len(logs) == 2
    text = str(logs)
    assert "123456" not in text and "SECRET" not in text
    assert ADDRESS not in text
    assert logs[0]["purpose"] == "code_login"


async def test_log_sender_writes_secret_fields_when_include_secrets() -> None:
    message = _message(secret_log_fields={"code": "123456"})
    with capture_logs() as logs:
        await LogEmailSender(include_secrets=True).send(message)
    assert logs[0]["code"] == "123456"
    assert "body" not in logs[0]
    assert ADDRESS not in str(logs)  # получатель не логируется никогда


async def test_log_sender_writes_body_only_with_include_secrets_and_log_body() -> None:
    with capture_logs() as logs:
        await LogEmailSender(include_secrets=True, log_body=True).send(_message())
        await LogEmailSender(include_secrets=False, log_body=True).send(_message())
    assert logs[0]["body"] == "Тело письма"
    assert "body" not in logs[1]
    assert ADDRESS not in str(logs)


# ------------------------------------------------------------------------------------ SMTP


class _Capture:
    def __init__(self) -> None:
        self.calls: list[tuple[MimeMessage, dict[str, Any]]] = []

    async def __call__(self, mime: MimeMessage, **kwargs: Any) -> None:
        self.calls.append((mime, kwargs))


async def test_smtp_starttls_default(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _Capture()
    monkeypatch.setattr(senders_module.aiosmtplib, "send", cap)
    sender = SmtpEmailSender(host="smtp", port=587, sender="no-reply@gv")
    await sender.send(_message(body="123456"))
    mime, kwargs = cap.calls[0]
    assert (kwargs["start_tls"], kwargs["use_tls"], kwargs["timeout"]) == (True, False, 10.0)
    assert mime["To"] == ADDRESS and mime["From"] == "no-reply@gv"
    assert mime["Subject"] == "Тема"
    assert "123456" in mime.get_content()


async def test_smtp_implicit_tls_and_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _Capture()
    monkeypatch.setattr(senders_module.aiosmtplib, "send", cap)
    sender = SmtpEmailSender(
        host="smtp",
        port=465,
        sender="no-reply@gv",
        username="u",
        password="p",
        start_tls=False,
        use_tls=True,
        timeout_s=3.5,
    )
    await sender.send(_message())
    _, kwargs = cap.calls[0]
    assert (kwargs["start_tls"], kwargs["use_tls"], kwargs["timeout"]) == (False, True, 3.5)
    assert (kwargs["hostname"], kwargs["port"], kwargs["username"]) == ("smtp", 465, "u")
    assert kwargs["password"] == "p"


def test_smtp_tls_modes_are_mutually_exclusive() -> None:
    with pytest.raises(ValueError, match="взаимоисключающи"):
        SmtpEmailSender(host="h", port=465, sender="s", start_tls=True, use_tls=True)


async def test_smtp_sender_sets_extra_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _Capture()
    monkeypatch.setattr(senders_module.aiosmtplib, "send", cap)
    message = _message(
        headers={
            "List-Unsubscribe": "<https://guessverse.example/unsubscribe?token=abc>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        }
    )
    await SmtpEmailSender(host="h", port=587, sender="s").send(message)
    [(mime, _)] = cap.calls
    assert mime["List-Unsubscribe"] == "<https://guessverse.example/unsubscribe?token=abc>"
    assert mime["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"


async def test_smtp_recipient_refusal_is_permanent_other_errors_are_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    errors: list[Exception] = [
        senders_module.aiosmtplib.SMTPRecipientRefused(550, "no such user", ADDRESS),
        senders_module.aiosmtplib.SMTPRecipientsRefused([]),
    ]

    async def refusing(message: Any, **kwargs: Any) -> None:
        raise errors.pop(0)

    monkeypatch.setattr(senders_module.aiosmtplib, "send", refusing)
    sender = SmtpEmailSender(host="h", port=587, sender="s")
    for _ in range(2):
        with pytest.raises(PermanentEmailError):
            await sender.send(_message())

    async def auth_failure(message: Any, **kwargs: Any) -> None:
        raise senders_module.aiosmtplib.SMTPAuthenticationError(535, "bad credentials")

    monkeypatch.setattr(senders_module.aiosmtplib, "send", auth_failure)
    with pytest.raises(senders_module.aiosmtplib.SMTPAuthenticationError):
        await sender.send(_message())  # проблема нашей стороны: повторяется, а не «окончательно»


# ------------------------------------------------------------------------ фоновая отправка


class _Gate:
    """Отправитель, который ждёт сигнала: имитирует медленный SMTP."""

    def __init__(self, *, fail: bool = False) -> None:
        self.release = asyncio.Event()
        self.sent: list[OutgoingEmail] = []
        self.fail = fail

    async def send(self, message: OutgoingEmail) -> None:
        await self.release.wait()
        if self.fail:
            raise RuntimeError("smtp down")
        self.sent.append(message)


async def test_background_sender_returns_before_the_slow_smtp_finishes() -> None:
    gate = _Gate()
    sender = BackgroundEmailSender(gate)
    message = _message(body="123456")

    await sender.send(message)
    assert gate.sent == []  # SMTP ещё «думает», а вызывающий уже свободен

    gate.release.set()
    await sender.drain()
    assert gate.sent == [message]


async def test_background_sender_swallows_and_logs_smtp_failure() -> None:
    gate = _Gate(fail=True)
    gate.release.set()
    sender = BackgroundEmailSender(gate)

    with capture_logs() as logs:
        await sender.send(_message(body="123456", log_fields={"purpose": "code_login"}))
        await sender.drain()

    failed = [e for e in logs if e["event"] == "email.send_failed"]
    assert failed and failed[0]["error"] == "RuntimeError"
    assert failed[0]["purpose"] == "code_login"
    assert "123456" not in str(logs) and ADDRESS not in str(logs)


async def test_background_sender_applies_backpressure_when_queue_is_full() -> None:
    """При max_pending задачах в полёте письмо отправляется синхронно (очередь не растёт)."""
    gate = _Gate()
    sender = BackgroundEmailSender(gate, max_pending=1)
    await sender.send(_message(body="111111"))  # занимает единственный слот

    blocked = asyncio.create_task(sender.send(_message(body="222222")))
    await asyncio.sleep(0.05)
    assert not blocked.done()  # второе письмо ждёт отправки, а не уходит в очередь

    gate.release.set()
    await blocked
    await sender.drain()
    assert sorted(m.body for m in gate.sent) == ["111111", "222222"]
