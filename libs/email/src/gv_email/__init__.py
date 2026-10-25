"""Отправка писем: общий транспорт для identity и notifications (ADR 0066)."""

from gv_email.message import OutgoingEmail, PermanentEmailError
from gv_email.senders import (
    BackgroundEmailSender,
    EmailTransport,
    LogEmailSender,
    SmtpEmailSender,
)

__all__ = [
    "BackgroundEmailSender",
    "EmailTransport",
    "LogEmailSender",
    "OutgoingEmail",
    "PermanentEmailError",
    "SmtpEmailSender",
]
