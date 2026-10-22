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
