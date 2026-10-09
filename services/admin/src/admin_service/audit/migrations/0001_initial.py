"""Аудит админки: таблица и запрет UPDATE/DELETE на уровне БД (append-only)."""

# ruff: noqa: RUF012 — код сгенерирован makemigrations

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

# Append-only даже в обход ORM (ручной SQL, ошибка в коде): строку можно только вставить.
APPEND_ONLY_SQL = """
CREATE OR REPLACE FUNCTION audit_auditentry_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_auditentry is append-only';
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER audit_auditentry_append_only
    BEFORE UPDATE OR DELETE ON audit_auditentry
    FOR EACH ROW EXECUTE FUNCTION audit_auditentry_append_only();
"""
DROP_APPEND_ONLY_SQL = """
DROP TRIGGER IF EXISTS audit_auditentry_append_only ON audit_auditentry;
DROP FUNCTION IF EXISTS audit_auditentry_append_only();
"""


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AuditEntry",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("actor_identity_id", models.UUIDField(blank=True, null=True)),
                ("action", models.CharField(max_length=64)),
                ("service", models.CharField(max_length=64)),
                ("target_type", models.CharField(max_length=64)),
                ("target_id", models.CharField(max_length=128)),
                ("reason", models.TextField()),
                ("before", models.JSONField(blank=True, null=True)),
                ("after", models.JSONField(blank=True, null=True)),
                ("request_id", models.CharField(blank=True, default="", max_length=64)),
                (
                    "idempotency_key",
                    models.CharField(blank=True, default="", max_length=64),
                ),
                (
                    "outcome",
                    models.CharField(
                        choices=[("succeeded", "успех"), ("failed", "отказ")],
                        max_length=16,
                    ),
                ),
                (
                    "error_code",
                    models.CharField(blank=True, default="", max_length=128),
                ),
                (
                    "actor",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="audit_entries",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "запись аудита",
                "verbose_name_plural": "аудит",
                "ordering": ["-created_at", "-id"],
                "indexes": [
                    models.Index(
                        fields=["target_type", "target_id"],
                        name="audit_audit_target__e2abcf_idx",
                    )
                ],
            },
        ),
        migrations.RunSQL(APPEND_ONLY_SQL, DROP_APPEND_ONLY_SQL),
    ]
