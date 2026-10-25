"""Отправители писем: лог без секретов вне local/ci, SMTP с кодом (ADR 0041)."""

from __future__ import annotations

from email.message import EmailMessage as MimeMessage
from typing import Any

import pytest
from identity_service.application.ports import EmailMessage
from identity_service.domain import EmailAddress
from identity_service.infrastructure.clients import IdentityEmailSender, render_email
from structlog.testing import capture_logs

from gv_email import BackgroundEmailSender, LogEmailSender, OutgoingEmail, SmtpEmailSender, senders

pytestmark = pytest.mark.integration

ADDRESS = "neo@example.com"
CODE_PURPOSES = ["code_register", "code_login", "code_reset", "code_password_change"]


def _message(purpose: str = "code_login", **kw: Any) -> EmailMessage:
    return EmailMessage(EmailAddress.parse(ADDRESS), purpose, kw.pop("link", None), **kw)


async def test_log_sender_hides_code_without_include_secrets() -> None:
    message = _message(code="123456", link="https://x/?token=SECRET")
    with capture_logs() as logs:
        await IdentityEmailSender(LogEmailSender(), code_ttl_s=600).send(message)
        await IdentityEmailSender(LogEmailSender(include_secrets=False), code_ttl_s=600).send(
            message
        )
    assert len(logs) == 2
    text = str(logs)
    assert "123456" not in text and "SECRET" not in text
    assert ADDRESS not in text
    assert logs[0]["purpose"] == "code_login"


async def test_log_sender_writes_code_when_include_secrets() -> None:
    with capture_logs() as logs:
        await IdentityEmailSender(LogEmailSender(include_secrets=True), code_ttl_s=600).send(
            _message(code="123456")
        )
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


async def test_adapter_renders_code_mail_into_smtp_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Транспорт (SMTP, TLS, фон) покрыт тестами libs/email; здесь - рендер identity до него."""
    cap = _Capture()
    monkeypatch.setattr(senders.aiosmtplib, "send", cap)
    transport = SmtpEmailSender(host="smtp", port=587, sender="no-reply@gv")
    await IdentityEmailSender(transport, code_ttl_s=600).send(_message(code="123456"))
    mime, kwargs = cap.calls[0]
    assert mime["To"] == ADDRESS and mime["From"] == "no-reply@gv"
    assert "123456" in mime.get_content() and "10 мин" in mime.get_content()
    assert mime["Subject"]
    assert (kwargs["start_tls"], kwargs["use_tls"]) == (True, False)


async def test_background_adapter_does_not_leak_code_into_failure_log() -> None:
    class _Failing:
        async def send(self, message: OutgoingEmail) -> None:
            raise RuntimeError("smtp down")

    sender = BackgroundEmailSender(_Failing())
    with capture_logs() as logs:
        await IdentityEmailSender(sender, code_ttl_s=600).send(_message(code="123456"))
        await sender.drain()
    failed = [e for e in logs if e["event"] == "email.send_failed"]
    assert failed and failed[0]["purpose"] == "code_login"
    assert "123456" not in str(logs) and ADDRESS not in str(logs)
