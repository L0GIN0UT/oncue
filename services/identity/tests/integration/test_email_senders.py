"""Отправители писем: лог без секретов вне local/ci, SMTP с кодом (ADR 0041)."""

from __future__ import annotations

from email.message import EmailMessage as MimeMessage
from typing import Any

import pytest
from identity_service.application.ports import EmailMessage
from identity_service.domain import EmailAddress
from identity_service.infrastructure import clients
from identity_service.infrastructure.clients import LogEmailSender, SmtpEmailSender, render_email
from structlog.testing import capture_logs

pytestmark = pytest.mark.integration

ADDRESS = "neo@example.com"
CODE_PURPOSES = ["code_register", "code_login", "code_reset", "code_password_change"]


def _message(purpose: str = "code_login", **kw: Any) -> EmailMessage:
    return EmailMessage(EmailAddress.parse(ADDRESS), purpose, kw.pop("link", None), **kw)


async def test_log_sender_hides_code_without_include_secrets() -> None:
    message = _message(code="123456", link="https://x/?token=SECRET")
    with capture_logs() as logs:
        await LogEmailSender().send(message)
        await LogEmailSender(include_secrets=False).send(message)
    assert len(logs) == 2
    text = str(logs)
    assert "123456" not in text and "SECRET" not in text
    assert ADDRESS not in text
    assert logs[0]["purpose"] == "code_login"


async def test_log_sender_writes_code_when_include_secrets() -> None:
    with capture_logs() as logs:
        await LogEmailSender(include_secrets=True).send(_message(code="123456"))
    assert logs[0]["code"] == "123456"
    assert ADDRESS not in str(logs)  # получатель не логируется никогда


@pytest.mark.parametrize("purpose", CODE_PURPOSES)
def test_code_mail_has_code_ttl_and_no_links(purpose: str) -> None:
    subject, body = render_email(_message(purpose, code="042917"), code_ttl_s=600)
    assert "042917" in body
    assert "10 мин" in body
    assert "проигнорируйте" in body
    assert "http" not in body and "://" not in body
    assert subject


def test_code_ttl_is_not_hardcoded() -> None:
    _, body = render_email(_message(code="000001"), code_ttl_s=900)
    assert "15 мин" in body and "10 мин" not in body


@pytest.mark.parametrize(
    "purpose", ["already_registered", "provider_linked", "provider_unlinked", "password_changed"]
)
def test_notification_mails_are_kept(purpose: str) -> None:
    subject, body = render_email(_message(purpose, providers=("yandex",)), code_ttl_s=600)
    assert subject and body


def test_link_purposes_are_gone() -> None:
    for purpose in ("verify", "reset"):
        with pytest.raises(KeyError):
            render_email(_message(purpose, link="https://x"), code_ttl_s=600)


class _Capture:
    def __init__(self) -> None:
        self.calls: list[tuple[MimeMessage, dict[str, Any]]] = []

    async def __call__(self, mime: MimeMessage, **kwargs: Any) -> None:
        self.calls.append((mime, kwargs))


async def test_smtp_starttls_default(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _Capture()
    monkeypatch.setattr(clients.aiosmtplib, "send", cap)
    sender = SmtpEmailSender(host="smtp", port=587, sender="no-reply@gv", code_ttl_s=600)
    await sender.send(_message(code="123456"))
    mime, kwargs = cap.calls[0]
    assert (kwargs["start_tls"], kwargs["use_tls"], kwargs["timeout"]) == (True, False, 10.0)
    assert mime["To"] == ADDRESS and mime["From"] == "no-reply@gv"
    assert "123456" in mime.get_content()


async def test_smtp_implicit_tls_and_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _Capture()
    monkeypatch.setattr(clients.aiosmtplib, "send", cap)
    sender = SmtpEmailSender(
        host="smtp",
        port=465,
        sender="no-reply@gv",
        code_ttl_s=600,
        username="u",
        password="p",
        start_tls=False,
        use_tls=True,
        timeout_s=3.5,
    )
    await sender.send(_message(code="123456"))
    _, kwargs = cap.calls[0]
    assert (kwargs["start_tls"], kwargs["use_tls"], kwargs["timeout"]) == (False, True, 3.5)
    assert (kwargs["hostname"], kwargs["port"], kwargs["username"]) == ("smtp", 465, "u")


def test_smtp_tls_modes_are_mutually_exclusive() -> None:
    with pytest.raises(ValueError, match="взаимоисключающи"):
        SmtpEmailSender(
            host="h", port=465, sender="s", code_ttl_s=600, start_tls=True, use_tls=True
        )


# ----------------------------------------------------------------------------- фоновая отправка


class _Gate:
    """Отправитель, который ждёт сигнала: имитирует медленный SMTP."""

    def __init__(self, *, fail: bool = False) -> None:
        import asyncio

        self.release = asyncio.Event()
        self.sent: list[EmailMessage] = []
        self.fail = fail

    async def send(self, message: EmailMessage) -> None:
        await self.release.wait()
        if self.fail:
            raise RuntimeError("smtp down")
        self.sent.append(message)


async def test_background_sender_returns_before_the_slow_smtp_finishes() -> None:
    """Время ответа не зависит от SMTP: send() возвращается, пока письмо ещё не ушло."""
    from identity_service.infrastructure.clients import BackgroundEmailSender

    gate = _Gate()
    sender = BackgroundEmailSender(gate)

    await sender.send(_message(code="123456"))
    assert gate.sent == []  # SMTP ещё «думает», а вызывающий уже свободен

    gate.release.set()
    await sender.drain()
    assert [m.code for m in gate.sent] == ["123456"]


async def test_background_sender_swallows_and_logs_smtp_failure() -> None:
    from identity_service.infrastructure.clients import BackgroundEmailSender

    gate = _Gate(fail=True)
    gate.release.set()
    sender = BackgroundEmailSender(gate)

    with capture_logs() as logs:
        await sender.send(_message(code="123456"))
        await sender.drain()

    failed = [e for e in logs if e["event"] == "email.send_failed"]
    assert failed and failed[0]["error"] == "RuntimeError"
    assert "123456" not in str(logs)


async def test_background_sender_applies_backpressure_when_queue_is_full() -> None:
    """При max_pending задачах в полёте письмо отправляется синхронно (очередь не растёт)."""
    import asyncio

    from identity_service.infrastructure.clients import BackgroundEmailSender

    gate = _Gate()
    sender = BackgroundEmailSender(gate, max_pending=1)
    await sender.send(_message(code="111111"))  # занимает единственный слот

    blocked = asyncio.create_task(sender.send(_message(code="222222")))
    await asyncio.sleep(0.05)
    assert not blocked.done()  # второе письмо ждёт отправки, а не уходит в очередь

    gate.release.set()
    await blocked
    await sender.drain()
    assert sorted(m.code or "" for m in gate.sent) == ["111111", "222222"]
