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
