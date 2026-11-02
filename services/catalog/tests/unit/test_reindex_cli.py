"""CLI ``reindex``: разбор аргументов и поведение без OpenSearch; ``prepare_resources``."""

from __future__ import annotations

import pytest
from catalog_service.main import prepare_resources
from catalog_service.reindex_cli import EXIT_NOT_CONFIGURED, _run, parse_args
from catalog_service.settings import OpenSearchSettings, Settings

pytestmark = pytest.mark.unit

USAGE_EXIT = 2  # код выхода argparse при ошибке аргументов


def test_defaults_take_batch_from_settings() -> None:
    args = parse_args([])
    assert args.batch is None
    assert not (args.no_switch or args.recreate or args.rollback)


def test_flags_are_parsed() -> None:
    args = parse_args(["--batch", "50", "--recreate", "--no-switch"])
    assert (args.batch, args.recreate, args.no_switch) == (50, True, True)


@pytest.mark.parametrize(
    "argv",
    [["--batch", "0"], ["--rollback", "--no-switch"], ["--rollback", "--recreate"]],
)
def test_invalid_combinations_exit_with_usage(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        parse_args(argv)
    assert exc.value.code == USAGE_EXIT


def test_reindex_limits_come_from_settings() -> None:
    cfg = OpenSearchSettings()
    assert (cfg.reindex_batch, cfg.reindex_timeout_s) == (500, 120.0)


async def test_run_without_opensearch_url_reports_and_exits_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("GV_CATALOG__OPENSEARCH__URL", raising=False)
    assert await _run(parse_args([])) == EXIT_NOT_CONFIGURED
    assert "OpenSearch не настроен" in capsys.readouterr().out


async def test_prepare_resources_is_noop_without_opensearch() -> None:
    settings = Settings()
    settings.opensearch.url = None
    await prepare_resources(settings)  # не падает и никуда не ходит
