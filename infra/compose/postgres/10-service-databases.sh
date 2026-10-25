#!/bin/sh
# СГЕНЕРИРОВАНО `gv gen` из infra/services.toml — не редактировать вручную.
# Выполняется контейнером postgres один раз при инициализации пустого тома.
# Каждому сервису — своя БД и свой пользователь без доступа к чужим базам.
set -eu

create_service_db() {
  # Имя и пароль передаются переменными psql (:"name", :'password') — без SQL-инъекций
  # и проблем с кавычками в пароле.
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
    -v name="$1" -v password="$2" <<'SQL'
CREATE ROLE :"name" LOGIN PASSWORD :'password';
CREATE DATABASE :"name" OWNER :"name";
REVOKE ALL ON DATABASE :"name" FROM PUBLIC;
SQL
  # Расширения создаёт суперпользователь: у сервисной роли таких прав нет (и не должно быть).
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$1" \
    -c 'CREATE EXTENSION IF NOT EXISTS pg_trgm' -c 'CREATE EXTENSION IF NOT EXISTS citext'
}

# Рантайм-роль сервиса с журналами (ADR 0060): права на таблицы выдаёт миграция сервиса
# (она выполняется владельцем схемы), здесь — только роль и доступ к базе.
create_app_role() {
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres     -v owner="$1" -v name="$1_app" -v password="$2" <<'SQL'
CREATE ROLE :"name" LOGIN PASSWORD :'password';
GRANT CONNECT ON DATABASE :"owner" TO :"name";
SQL
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$1" -v name="$1_app" <<'SQL'
GRANT USAGE ON SCHEMA public TO :"name";
SQL
}

create_service_db identity "${PG_PASSWORD_IDENTITY:?задайте PG_PASSWORD_IDENTITY}"
create_service_db catalog "${PG_PASSWORD_CATALOG:?задайте PG_PASSWORD_CATALOG}"
create_service_db media_pipeline "${PG_PASSWORD_MEDIA_PIPELINE:?задайте PG_PASSWORD_MEDIA_PIPELINE}"
create_service_db game "${PG_PASSWORD_GAME:?задайте PG_PASSWORD_GAME}"
create_service_db match "${PG_PASSWORD_MATCH:?задайте PG_PASSWORD_MATCH}"
create_service_db rating "${PG_PASSWORD_RATING:?задайте PG_PASSWORD_RATING}"
create_service_db collections "${PG_PASSWORD_COLLECTIONS:?задайте PG_PASSWORD_COLLECTIONS}"
create_service_db library "${PG_PASSWORD_LIBRARY:?задайте PG_PASSWORD_LIBRARY}"
create_service_db integrations "${PG_PASSWORD_INTEGRATIONS:?задайте PG_PASSWORD_INTEGRATIONS}"
create_service_db progression "${PG_PASSWORD_PROGRESSION:?задайте PG_PASSWORD_PROGRESSION}"
create_app_role progression "${PG_PASSWORD_PROGRESSION_APP:?задайте PG_PASSWORD_PROGRESSION_APP}"
create_service_db payments "${PG_PASSWORD_PAYMENTS:?задайте PG_PASSWORD_PAYMENTS}"
create_service_db social "${PG_PASSWORD_SOCIAL:?задайте PG_PASSWORD_SOCIAL}"
create_service_db notifications "${PG_PASSWORD_NOTIFICATIONS:?задайте PG_PASSWORD_NOTIFICATIONS}"
create_service_db admin "${PG_PASSWORD_ADMIN:?задайте PG_PASSWORD_ADMIN}"
create_service_db unleash "${PG_PASSWORD_UNLEASH:?задайте PG_PASSWORD_UNLEASH}"
