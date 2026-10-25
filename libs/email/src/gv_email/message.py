"""Готовое к отправке письмо и ошибка окончательного отказа."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field


class PermanentEmailError(Exception):
    """Адрес отвергнут окончательно (получатель отклонён SMTP-сервером): повтор бесполезен."""


@dataclass(frozen=True, slots=True)
class OutgoingEmail:
    """Письмо после рендера: транспорт ничего не знает о назначении письма.

    Attributes:
        to: Адрес получателя (в лог не попадает никогда - ПДн).
        subject: Тема.
        body: Текст.
        headers: Дополнительные заголовки (``List-Unsubscribe`` и др.); значения без CR/LF.
        log_fields: Несекретные поля для лога (назначение письма и т. п.); без адреса и кодов.
        secret_log_fields: Поля, которые пишутся в лог только при ``include_secrets``
            (local/ci: e2e читает код из лога).
    """

    to: str
    subject: str
    body: str
    headers: Mapping[str, str] = field(default_factory=dict)
    log_fields: Mapping[str, object] = field(default_factory=dict)
    secret_log_fields: Mapping[str, object] = field(default_factory=dict)
