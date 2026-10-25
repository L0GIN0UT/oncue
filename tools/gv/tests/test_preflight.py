"""Preflight окружения: совпадающие секреты Centrifugo — ошибка, значения не раскрываются."""

from pathlib import Path

import pytest

from gv_cli.preflight import PreflightError, check_centrifugo_secrets, check_env, read_env_file

GOOD = {
    "CENTRIFUGO_TOKEN_HMAC_SECRET": "aaa-connection",
    "CENTRIFUGO_SUBSCRIPTION_HMAC_SECRET": "bbb-subscription",
    "CENTRIFUGO_API_KEY": "ccc-api",
}


def test_distinct_secrets_pass() -> None:
    check_centrifugo_secrets(GOOD)


def test_equal_hmac_secrets_fail_without_leaking_values() -> None:
    env = {**GOOD, "CENTRIFUGO_SUBSCRIPTION_HMAC_SECRET": GOOD["CENTRIFUGO_TOKEN_HMAC_SECRET"]}
    with pytest.raises(PreflightError) as exc:
        check_centrifugo_secrets(env)
    text = str(exc.value)
    assert "CENTRIFUGO_TOKEN_HMAC_SECRET = CENTRIFUGO_SUBSCRIPTION_HMAC_SECRET" in text
    assert "aaa-connection" not in text


def test_api_key_equal_to_hmac_fails() -> None:
    env = {**GOOD, "CENTRIFUGO_API_KEY": GOOD["CENTRIFUGO_TOKEN_HMAC_SECRET"]}
    with pytest.raises(PreflightError, match="совпадают"):
        check_centrifugo_secrets(env)


def test_empty_secret_fails() -> None:
    with pytest.raises(PreflightError, match="CENTRIFUGO_API_KEY"):
        check_centrifugo_secrets({**GOOD, "CENTRIFUGO_API_KEY": ""})


def test_env_file_and_process_env_merge(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# комментарий\nCENTRIFUGO_TOKEN_HMAC_SECRET=same\n"
        "CENTRIFUGO_SUBSCRIPTION_HMAC_SECRET='same'\nCENTRIFUGO_API_KEY=k\n",
        encoding="utf-8",
    )
    assert read_env_file(env_file)["CENTRIFUGO_SUBSCRIPTION_HMAC_SECRET"] == "same"
    with pytest.raises(PreflightError):
        check_env(env_file, {})
    # Переменная процесса перекрывает .env (как у docker compose).
    check_env(env_file, {"CENTRIFUGO_SUBSCRIPTION_HMAC_SECRET": "other"})


def test_missing_env_file_means_empty(tmp_path: Path) -> None:
    with pytest.raises(PreflightError, match="не заданы"):
        check_env(tmp_path / "nope.env", {})
