from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from gv_cli import generate, scaffold, workspace
from gv_cli.registry import load_services, repo_root

pytestmark = pytest.mark.unit

ROOT = repo_root(Path(__file__))


def _registry(tmp_path: Path, body: str) -> Path:
    (tmp_path / "infra").mkdir()
    (tmp_path / "infra" / "services.toml").write_text(body, encoding="utf-8")
    return tmp_path


class TestRegistry:
    def test_real_registry_is_valid(self) -> None:
        services = load_services(ROOT)
        assert len(services) == 15
        names = {s.name for s in services}
        assert {"identity", "game", "match", "payments", "admin", "bff_web"} <= names
        ports = [s.dev_port for s in services]
        assert len(set(ports)) == len(ports)

    def test_derived_names(self) -> None:
        mp = next(s for s in load_services(ROOT) if s.name == "media_pipeline")
        assert mp.package == "media_pipeline_service"
        assert mp.dns == "media-pipeline"
        assert mp.distribution == "guessverse-media-pipeline"
        assert mp.db_password_env == "PG_PASSWORD_MEDIA_PIPELINE"

    @pytest.mark.parametrize(
        ("body", "error"),
        [
            ('[services.Bad]\ndescription="x"\n', "недопустимое имя"),
            ('[services.a]\ndescription="x"\npublic="A B"\n', "public-префикс"),
            (
                '[services.a]\ndescription="x"\npublic="p"\n[services.b]\ndescription="y"\npublic="p"\n',
                "уже занят",
            ),
        ],
    )
    def test_invalid_registry(self, tmp_path: Path, body: str, error: str) -> None:
        with pytest.raises(SystemExit, match=error):
            load_services(_registry(tmp_path, body))


class TestGenerate:
    def test_generated_files_are_up_to_date(self) -> None:
        assert generate.stale(ROOT, load_services(ROOT)) == []

    def test_compose_is_valid_yaml_with_all_services(self) -> None:
        rendered = generate.render_all(load_services(ROOT))
        compose = yaml.safe_load(rendered["infra/compose/compose.services.yml"])
        services = compose["services"]
        assert "media-pipeline" in services and "media-pipeline-migrate" in services
        assert "bff-web-migrate" not in services  # у bff_web нет БД
        game = services["game"]
        assert game["command"] == ["all"]
        assert game["depends_on"]["game-migrate"] == {"condition": "service_completed_successfully"}
        assert "PG_PASSWORD_GAME" in game["environment"]["GV_DATABASE_URL"]

    def test_nginx_blocks_internal_paths_and_skips_private_services(self) -> None:
        rendered = generate.render_all(load_services(ROOT))
        routes = rendered["infra/nginx/generated/routes.conf"]
        upstreams = rendered["infra/nginx/generated/upstreams.conf"]
        assert "location /api/game/" in routes
        assert "(v[0-9]+/)?(internal|admin|health|metrics" in routes
        assert "upstream" not in routes  # upstream допустим только на уровне http {}
        assert "location" not in upstreams
        assert "server game:8000 resolve" in upstreams  # рантайм-резолв: gateway не падает
        for conf in (routes, upstreams):
            assert "media_pipeline" not in conf  # не публикуется
            assert "gv_admin" not in conf

    def test_databases_script_uses_psql_variables(self) -> None:
        script = generate.render_all(load_services(ROOT))[
            "infra/compose/postgres/10-service-databases.sh"
        ]
        assert "CREATE ROLE :\"name\" LOGIN PASSWORD :'password';" in script
        assert 'create_service_db game "${PG_PASSWORD_GAME:?' in script
        assert "create_service_db bff_web" not in script


class TestScaffold:
    def test_service_without_database_has_no_db_layer(self) -> None:
        bff = next(s for s in load_services(ROOT) if s.name == "bff_web")
        files = scaffold.render_service(bff)
        assert "src/bff_web_service/main.py" in files
        assert not any("infrastructure/db" in f for f in files)
        assert not any(f.startswith("tests/integration") for f in files)
        assert "TABLES" not in files["src/bff_web_service/main.py"]

    def test_service_with_database_has_migrations(self) -> None:
        game = next(s for s in load_services(ROOT) if s.name == "game")
        files = scaffold.render_service(game)
        assert (
            "src/game_service/infrastructure/db/migrations/versions/0001_service_tables.py" in files
        )
        assert "src/game_service/infrastructure/db/migrations/script.py.mako" in files
        assert 'game = "game_service.main:run"' in files["pyproject.toml"]


class TestWorkspace:
    def test_dependents_are_transitive(self) -> None:
        members = workspace.load_members(ROOT)
        affected = workspace.dependents(members, {"gv-core"})
        assert {"gv-core", "gv-db", "gv-service", "guessverse-game"} <= affected
        assert "gv-cli" not in affected

    def test_leaf_change_affects_only_itself(self) -> None:
        members = workspace.load_members(ROOT)
        assert workspace.dependents(members, {"guessverse-game"}) == {"guessverse-game"}


def test_fill_secrets_shares_media_signing_secret() -> None:
    """gateway и media-pipeline должны получить один и тот же секрет подписи (ADR 0012)."""
    from gv_cli.main import fill_secrets

    template = "\n".join(
        ["GV_MEDIA_SIGNING_SECRET=", "GV_MEDIA__SIGNING_SECRET=", "REDIS_PASSWORD=", "GV_TAG=local"]
    )
    out = fill_secrets(template)
    values = dict(line.split("=", 1) for line in out.splitlines() if not line.startswith("#"))
    assert values["GV_MEDIA_SIGNING_SECRET"] == values["GV_MEDIA__SIGNING_SECRET"]
    assert len(values["GV_MEDIA_SIGNING_SECRET"]) >= 32
    assert values["REDIS_PASSWORD"] not in ("", values["GV_MEDIA_SIGNING_SECRET"])
    assert values["GV_TAG"] == "local"


class TestFrontend:
    """`gv test frontend` (план этапа 2, §2.1): pnpm внутри frontend/, без него — пропуск."""

    def test_missing_frontend_is_reported_and_exits_zero(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from typer.testing import CliRunner

        from gv_cli import main

        (tmp_path / "infra").mkdir()
        (tmp_path / "infra" / "services.toml").write_text("", encoding="utf-8")
        monkeypatch.chdir(tmp_path)  # корень репо ищется от текущего каталога
        result = CliRunner().invoke(main.app, ["test", "frontend"])
        assert main.frontend_test_command(tmp_path, None) is None
        assert result.exit_code == 0, result.output
        assert "frontend/ отсутствует" in result.output

    def test_commands_when_frontend_exists(self, tmp_path: Path) -> None:
        from gv_cli import main

        (tmp_path / "frontend").mkdir()
        (tmp_path / "frontend" / "package.json").write_text("{}", encoding="utf-8")
        assert main.frontend_test_command(tmp_path, None) == ["pnpm", "-C", "frontend", "test"]
        assert main.frontend_test_command(tmp_path, "unit") == ["pnpm", "-C", "frontend", "test"]
        assert main.frontend_test_command(tmp_path, "e2e") == [
            "pnpm",
            "-C",
            "frontend",
            "exec",
            "playwright",
            "test",
        ]

    def test_python_levels_are_rejected_for_frontend(self, tmp_path: Path) -> None:
        import typer

        from gv_cli import main

        with pytest.raises(typer.BadParameter):
            main.frontend_test_command(tmp_path, "integration")

    def test_changed_detects_frontend_files(self) -> None:
        assert workspace.frontend_changed(["frontend/src/app/page.tsx"])
        assert not workspace.frontend_changed(["services/game/x.py", "frontend.md"])


def test_fill_secrets_generates_email_code_key_but_not_smtp_credentials() -> None:
    """ADR 0041: ключ HMAC кодов генерируется, пароль/логин SMTP остаются пустыми."""
    from gv_cli.main import fill_secrets

    template = "\n".join(
        ["GV_EMAIL_CODE__HMAC_KEY=", "GV_SMTP__USERNAME=", "GV_SMTP__PASSWORD=", "REDIS_PASSWORD="]
    )
    out = fill_secrets(template)
    values = dict(line.split("=", 1) for line in out.splitlines())
    assert len(values["GV_EMAIL_CODE__HMAC_KEY"]) >= 32
    assert values["GV_SMTP__USERNAME"] == ""
    assert values["GV_SMTP__PASSWORD"] == ""
    assert values["REDIS_PASSWORD"] != ""


def test_generated_identity_compose_has_email_code_settings() -> None:
    """compose identity получает настройки кодов и SMTP; ссылок с токенами нет."""
    from gv_cli.generate import render_all
    from gv_cli.registry import load_services

    rendered = render_all(load_services(ROOT))
    compose = rendered["infra/compose/compose.services.yml"]
    assert "GV_EMAIL_CODE__HMAC_KEY: ${GV_EMAIL_CODE__HMAC_KEY:?" in compose
    assert "GV_EMAIL__BACKEND: ${GV_EMAIL__BACKEND:-smtp}" in compose
    assert "GV_SMTP__PASSWORD: ${GV_SMTP__PASSWORD:-}" in compose
    dev = rendered["infra/compose/compose.services.dev.yml"]
    assert "GV_EMAIL__BACKEND: ${GV_EMAIL__BACKEND:-log}" in dev
    assert "VERIFY_LINK" not in dev
    assert "GV_SMTP__PASSWORD=\n" in rendered["infra/compose/.env.example"]

    """D13: секреты клиентов — случайные, в GV_SERVICE_CLIENTS identity — только их sha256."""
    import hashlib
    import json

    from gv_cli.generate import SERVICE_CLIENTS, service_clients_json, service_secret_env
    from gv_cli.main import fill_secrets

    template = "\n".join(
        [*(f"{service_secret_env(c)}=" for c in SERVICE_CLIENTS),
         f"GV_SERVICE_CLIENTS={service_clients_json()}"]
    )  # fmt: skip
    out = fill_secrets(template)
    values = dict(line.split("=", 1) for line in out.splitlines())
    clients = {c["client_id"]: c for c in json.loads(values["GV_SERVICE_CLIENTS"])}
    assert set(clients) == {"game", "admin", "match"}
    assert clients["game"]["audiences"] == {"media_pipeline": ["assets.link"]}
    assert clients["admin"]["audiences"] == {"identity": ["admin_sessions"]}
    # Этап 4a §2.7: план раундов у game (D5) и ссылки ассетов у media_pipeline.
    assert clients["match"]["audiences"] == {
        "game": ["rounds.plan"],
        "media_pipeline": ["assets.link"],
    }
    secrets_seen = set()
    for client_id, client in clients.items():
        secret = values[service_secret_env(client_id)]
        assert len(secret) >= 32
        assert client["secret_sha256"] == hashlib.sha256(secret.encode()).hexdigest()
        assert secret not in values["GV_SERVICE_CLIENTS"]
        secrets_seen.add(secret)
    assert len(secrets_seen) == 3


def test_generated_compose_passes_service_credentials() -> None:
    from gv_cli.generate import render_all
    from gv_cli.registry import load_services

    rendered = render_all(load_services(ROOT))
    env = rendered["infra/compose/.env.example"]
    assert "GV_SERVICE_SECRET_GAME=\n" in env
    assert "GV_SERVICE_SECRET_ADMIN=\n" in env
    assert 'GV_SERVICE_CLIENTS=[{"client_id":"game","secret_sha256":""' in env
    compose = rendered["infra/compose/compose.services.yml"]
    assert "GV_SERVICE_CLIENTS: ${GV_SERVICE_CLIENTS:-[]}" in compose
    assert "GV_SERVICE_AUTH__CLIENT_SECRET: ${GV_SERVICE_SECRET_GAME:?" in compose
    assert "GV_SERVICE_AUTH__CLIENT_SECRET: ${GV_SERVICE_SECRET_ADMIN:?" in compose


def test_fill_secrets_generates_loadable_jwt_key_and_daily_secret() -> None:
    """GV_JWT__SIGNING_KEYS — настоящий Ed25519-ключ в формате identity, а не случайная строка."""
    import json

    from cryptography.hazmat.primitives.asymmetric import ed25519
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    from gv_cli.main import fill_secrets

    out = fill_secrets("GV_JWT__SIGNING_KEYS=\nGV_GAME__DAILY_SEED_SECRET=\n")
    values = dict(line.split("=", 1) for line in out.splitlines())
    keys = json.loads(values["GV_JWT__SIGNING_KEYS"])
    assert len(keys) == 1 and keys[0]["active"] is True and keys[0]["kid"].startswith("local-")
    private = load_pem_private_key(keys[0]["private_pem"].encode(), password=None)
    assert isinstance(private, ed25519.Ed25519PrivateKey)
    assert "\n" not in values["GV_JWT__SIGNING_KEYS"]  # одна строка .env
    assert len(values["GV_GAME__DAILY_SEED_SECRET"]) >= 32


def test_generated_compose_passes_stage2_settings() -> None:
    from gv_cli.generate import render_all
    from gv_cli.registry import load_services

    rendered = render_all(load_services(ROOT))
    env = rendered["infra/compose/.env.example"]
    assert "GV_GAME__DAILY_SEED_SECRET=\n" in env
    assert "GV_JWT__SIGNING_KEYS=\n" in env

    services = yaml.safe_load(rendered["infra/compose/compose.services.yml"])["services"]
    game = services["game"]["environment"]
    assert game["GV_GAME__DAILY_SEED_SECRET"].startswith("${GV_GAME__DAILY_SEED_SECRET:?")
    assert game["GV_GAME__MEDIA_URL"] == "http://media-pipeline:8000"
    media = services["media-pipeline"]["environment"]
    assert media["GV_MEDIA__SIGNING_SECRET"].startswith("${GV_MEDIA__SIGNING_SECRET:?")
    identity = services["identity"]["environment"]
    assert identity["GV_JWT__SIGNING_KEYS"] == "${GV_JWT__SIGNING_KEYS:-}"
    assert services["bff-web"]["environment"]["GV_JWKS_URL"].endswith("/.well-known/jwks.json")
    admin = services["admin"]["environment"]
    assert admin["GV_SERVICE_AUTH__CLIENT_ID"] == "admin"

    dev = yaml.safe_load(rendered["infra/compose/compose.services.dev.yml"])["services"]
    assert dev["game"]["environment"]["GV_GAME__BOOTSTRAP_MODES_DIR"] == "/seeds/game/modes"
    assert dev["game"]["volumes"] == ["../../seeds/game/modes:/seeds/game/modes:ro"]
    assert (ROOT / "infra/compose" / "../../seeds/game/modes").resolve().is_dir()
    assert dev["rating"]["environment"]["GV_RATING__BOOTSTRAP_LADDERS_DIR"] == (
        "/seeds/rating/ladders"
    )
    assert dev["rating"]["volumes"] == ["../../seeds/rating/ladders:/seeds/rating/ladders:ro"]
    assert (ROOT / "seeds/rating/ladders").is_dir()
    assert dev["progression"]["environment"]["GV_PROGRESSION__BOOTSTRAP_CONFIG_DIR"] == (
        "/seeds/progression"
    )
    assert dev["progression"]["volumes"] == ["../../seeds/progression:/seeds/progression:ro"]
    assert (ROOT / "seeds/progression/config.yaml").is_file()
    mail = dev["identity"]["environment"]
    assert mail["GV_EMAIL__BACKEND"] == "${GV_EMAIL__BACKEND:-log}"
    assert not any("LINK" in key for key in mail)  # ссылок с токенами нет (ADR 0041)


def test_public_origins_for_cookie_mutation_services() -> None:
    import json

    from gv_cli.generate import render_all
    from gv_cli.main import fill_secrets
    from gv_cli.registry import load_services

    rendered = render_all(load_services(ROOT))
    services = yaml.safe_load(rendered["infra/compose/compose.services.yml"])["services"]
    # match (этап 4a): команды комнат из браузера — мутации по cookie; без списка Origin
    # CSRF-проверка отклоняла бы их все (создание комнаты не работало на стеке).
    for name in (
        "identity",
        "game",
        "collections",
        "library",
        "match",
        "progression",
        "notifications",
    ):
        value = services[name]["environment"]["GV_PUBLIC_ORIGINS"]
        assert value.startswith("${GV_PUBLIC_ORIGINS:?"), name
    assert "GV_PUBLIC_ORIGINS" not in services["bff-web"]["environment"]  # только GET

    # `gv dev secrets` по настоящему .env.example: origin остаётся, секрет дейлика генерируется.
    values = dict(
        line.split("=", 1)
        for line in fill_secrets(rendered["infra/compose/.env.example"]).splitlines()
        if line and not line.startswith("#")
    )
    assert json.loads(values["GV_PUBLIC_ORIGINS"]) == ["https://localhost"]
    assert len(values["GV_GAME__DAILY_SEED_SECRET"]) >= 32
    assert json.loads(values["GV_JWT__SIGNING_KEYS"])[0]["active"] is True


class TestRealtime:
    """Поле реестра ``realtime`` и env Centrifugo (план этапа 4, §2.1, D9, §12 п.5)."""

    def test_registry_flags(self) -> None:
        realtime = {s.name for s in load_services(ROOT) if s.realtime}
        assert realtime == {"identity", "match"}  # rating — в 4b

    def test_flag_defaults_to_false(self, tmp_path: Path) -> None:
        (service,) = load_services(_registry(tmp_path, '[services.a]\ndescription="x"\n'))
        assert service.realtime is False

    def test_compose_env_only_for_realtime_services_with_separate_secrets(self) -> None:
        rendered = generate.render_all(load_services(ROOT))
        services = yaml.safe_load(rendered["infra/compose/compose.services.yml"])["services"]
        identity = services["identity"]["environment"]
        match = services["match"]["environment"]
        assert match["GV_REALTIME__API_URL"] == "http://centrifugo:8000/api"
        assert match["GV_REALTIME__API_KEY"].startswith("${CENTRIFUGO_API_KEY:?")
        # identity только подписывает токены — ключа server API у него нет (минимум прав).
        assert "GV_REALTIME__API_KEY" not in identity
        assert "GV_REALTIME__API_URL" not in identity
        # D9: connection-токены — только identity, subscription — только match.
        assert identity["GV_REALTIME__CONNECTION_TOKEN_SECRET"].startswith(
            "${CENTRIFUGO_TOKEN_HMAC_SECRET:?"
        )
        assert "GV_REALTIME__SUBSCRIPTION_TOKEN_SECRET" not in identity
        assert match["GV_REALTIME__SUBSCRIPTION_TOKEN_SECRET"].startswith(
            "${CENTRIFUGO_SUBSCRIPTION_HMAC_SECRET:?"
        )
        assert "GV_REALTIME__CONNECTION_TOKEN_SECRET" not in match
        # API-ключ — только у сервисов с realtime = true.
        for name, svc in services.items():
            if name not in {"identity", "match"}:
                env = svc.get("environment", {})
                assert not any(k.startswith("GV_REALTIME__") for k in env), name

    def test_secret_for_service_without_realtime_is_rejected(self) -> None:
        services = [
            s if s.name != "match" else replace(s, realtime=False) for s in load_services(ROOT)
        ]
        with pytest.raises(SystemExit, match="match"):
            generate.render_all(services)

    def test_env_example_secrets_are_generated_and_differ(self) -> None:
        from gv_cli.main import fill_secrets

        rendered = generate.render_all(load_services(ROOT))["infra/compose/.env.example"]
        values = dict(
            line.split("=", 1)
            for line in fill_secrets(rendered).splitlines()
            if line and not line.startswith("#")
        )
        conn = values["CENTRIFUGO_TOKEN_HMAC_SECRET"]
        sub = values["CENTRIFUGO_SUBSCRIPTION_HMAC_SECRET"]
        assert len(conn) >= 32 and len(sub) >= 32 and conn != sub
        assert values["CENTRIFUGO_ALLOWED_ORIGINS"] == "https://localhost"

    def test_centrifugo_config(self) -> None:
        """Конфиг v6: без анонимов, без клиентских публикаций, отдельный секрет подписок."""
        import json

        config = json.loads((ROOT / "infra/centrifugo/config.json").read_text(encoding="utf-8"))
        text = json.dumps(config)
        assert "hmac_secret_key" not in text and "api_key" not in text  # секреты — из env
        client = config["client"]
        assert client["allow_anonymous_connect_without_token"] is False
        assert client["user_connection_limit"] > 0
        assert client["subscription_token"]["enabled"] is True
        assert config["http_stream"]["enabled"] is True
        namespaces = {ns["name"]: ns for ns in config["channel"]["namespaces"]}
        assert set(namespaces) == {"room", "personal"}
        for ns in namespaces.values():
            for key in ("publish", "subscribe", "presence", "history"):
                assert ns.get(f"allow_{key}_for_client", False) is False
            assert ns.get("allow_subscribe_for_anonymous", False) is False
            assert ns.get("allow_publish_for_subscriber", False) is False
        room = namespaces["room"]
        assert room["history_size"] > 0 and room["history_ttl"] and room["force_recovery"]

    def test_centrifugo_mounted_with_env_secrets(self) -> None:
        compose = yaml.safe_load(
            (ROOT / "infra/compose/compose.infra.yml").read_text(encoding="utf-8")
        )
        centrifugo = compose["services"]["centrifugo"]
        assert "../centrifugo/config.json:/centrifugo/config.json:ro" in centrifugo["volumes"]
        assert "/centrifugo/config.json" in centrifugo["command"]
        env = centrifugo["environment"]
        assert env["CENTRIFUGO_CLIENT_SUBSCRIPTION_TOKEN_HMAC_SECRET_KEY"].startswith(
            "${CENTRIFUGO_SUBSCRIPTION_HMAC_SECRET:?"
        )
        assert env["CENTRIFUGO_CLIENT_TOKEN_HMAC_SECRET_KEY"].startswith(
            "${CENTRIFUGO_TOKEN_HMAC_SECRET:?"
        )
