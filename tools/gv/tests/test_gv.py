from __future__ import annotations

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


def test_fill_secrets_hashes_service_client_secrets() -> None:
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
    assert set(clients) == {"game", "admin"}
    assert clients["game"]["audiences"] == {"media_pipeline": ["assets.link"]}
    assert clients["admin"]["audiences"] == {"identity": ["admin_sessions"]}
    secrets_seen = set()
    for client_id, client in clients.items():
        secret = values[service_secret_env(client_id)]
        assert len(secret) >= 32
        assert client["secret_sha256"] == hashlib.sha256(secret.encode()).hexdigest()
        assert secret not in values["GV_SERVICE_CLIENTS"]
        secrets_seen.add(secret)
    assert len(secrets_seen) == 2


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
    links = dev["identity"]["environment"]
    assert links["GV_EMAIL__VERIFY_LINK"] == "https://localhost/verify-email?token={token}"
    assert links["GV_EMAIL__RESET_LINK"] == "https://localhost/reset-password?token={token}"
