# libs/email (`gv_email`)

Отправка писем для identity и notifications ([ADR 0066](../../docs/adr/0066-notification-type-switches-and-email-lib.md)): `OutgoingEmail`, `SmtpEmailSender`, `LogEmailSender`, `BackgroundEmailSender`, `PermanentEmailError`.

## Особенности
- Библиотека не знает ни доменных писем, ни настроек сервисов: SMTP-параметры — аргументы конструктора; адаптеры портов (рендер, настройки `GV_*`) остаются в сервисах.
- Адрес получателя не логируется никогда; `secret_log_fields` (код, ссылка) и тело (`log_body`) пишет только `LogEmailSender(include_secrets=True)` (local/ci).
- Отказ получателя SMTP -> `PermanentEmailError` (повтор бесполезен); прочие ошибки aiosmtplib пробрасываются как есть (повторяемые).
