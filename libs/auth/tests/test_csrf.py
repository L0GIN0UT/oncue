"""CSRF и Origin для мутаций с cookie-аутентификацией (docs/plans/stage-1.md §4, §8 п.3)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from httpx import AsyncClient

from gv_auth import (
    ACCESS_COOKIE,
    CSRF_COOKIE,
    CSRF_HEADER,
    Authenticator,
    Principal,
    current_principal,
    new_csrf_token,
    require_same_origin,
)
from gv_core import AppError
from gv_service.problems import register_problem_handlers
from gv_testkit import assert_problem, service_client
from gv_testkit.auth import TestTokenIssuer

pytestmark = pytest.mark.unit

ORIGIN = "https://guessverse.test"


def _app(issuer: TestTokenIssuer, *, origins: tuple[str, ...] = (ORIGIN,)) -> FastAPI:
    app = FastAPI()
    app.state.jwt_verifier = issuer.verifier()
    app.state.csrf_origins = frozenset(origins)
    register_problem_handlers(app)

    @app.get("/me")
    async def me(p: Annotated[Principal, Depends(current_principal)]) -> dict[str, str]:
        return {"user_id": str(p.user_id), "sid": p.session_id or ""}

    @app.post("/mutate")
    async def mutate(p: Annotated[Principal, Depends(current_principal)]) -> dict[str, str]:
        return {"user_id": str(p.user_id)}

    @app.post("/login", dependencies=[Depends(require_same_origin)])
    async def login() -> dict[str, bool]:
        return {"ok": True}

    return app


@pytest.fixture
def issuer() -> TestTokenIssuer:
    return TestTokenIssuer()


@pytest.fixture
async def client(issuer: TestTokenIssuer) -> AsyncIterator[AsyncClient]:
    async with service_client(_app(issuer)) as c:
        yield c


def _cookie_login(client: AsyncClient, issuer: TestTokenIssuer) -> str:
    csrf = new_csrf_token()
    client.cookies.set(ACCESS_COOKIE, issuer.issue())
    client.cookies.set(CSRF_COOKIE, csrf)
    return csrf


async def test_cookie_get_needs_no_csrf(client: AsyncClient, issuer: TestTokenIssuer) -> None:
    _cookie_login(client, issuer)
    assert (await client.get("/me")).status_code == 200


async def test_cookie_mutation_with_origin_and_token_passes(
    client: AsyncClient, issuer: TestTokenIssuer
) -> None:
    csrf = _cookie_login(client, issuer)
    response = await client.post("/mutate", headers={"Origin": ORIGIN, CSRF_HEADER: csrf})
    assert response.status_code == 200


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": ORIGIN},  # нет X-CSRF-Token
        {"Origin": ORIGIN, CSRF_HEADER: "wrong"},  # токен не совпадает с cookie
        {"Origin": "https://evil.test", CSRF_HEADER: "{csrf}"},  # чужой Origin
        {"Origin": "null", CSRF_HEADER: "{csrf}"},  # sandbox iframe
        {CSRF_HEADER: "{csrf}"},  # нет ни Origin, ни Referer
        {"Referer": "https://evil.test/page", CSRF_HEADER: "{csrf}"},
    ],
)
async def test_cookie_mutation_rejected(
    client: AsyncClient, issuer: TestTokenIssuer, headers: dict[str, str]
) -> None:
    csrf = _cookie_login(client, issuer)
    resolved = {k: v.replace("{csrf}", csrf) for k, v in headers.items()}
    assert_problem(await client.post("/mutate", headers=resolved), 403, "auth.csrf_failed")


async def test_referer_fallback_accepted(client: AsyncClient, issuer: TestTokenIssuer) -> None:
    csrf = _cookie_login(client, issuer)
    headers = {"Referer": f"{ORIGIN}/profile?x=1", CSRF_HEADER: csrf}
    assert (await client.post("/mutate", headers=headers)).status_code == 200


async def test_missing_csrf_cookie_rejected(client: AsyncClient, issuer: TestTokenIssuer) -> None:
    client.cookies.set(ACCESS_COOKIE, issuer.issue())
    response = await client.post("/mutate", headers={"Origin": ORIGIN, CSRF_HEADER: ""})
    assert_problem(response, 403, "auth.csrf_failed")


async def test_bearer_mutation_needs_no_csrf(client: AsyncClient, issuer: TestTokenIssuer) -> None:
    response = await client.post(
        "/mutate", headers={"Authorization": f"Bearer {issuer.issue()}", "Origin": "https://x"}
    )
    assert response.status_code == 200


async def test_empty_allowlist_fails_closed(issuer: TestTokenIssuer) -> None:
    async with service_client(_app(issuer, origins=())) as client:
        csrf = _cookie_login(client, issuer)
        response = await client.post("/mutate", headers={"Origin": ORIGIN, CSRF_HEADER: csrf})
        assert_problem(response, 403, "auth.csrf_failed")


@pytest.mark.parametrize(
    ("headers", "status"),
    [
        ({"Origin": ORIGIN}, 200),
        ({"Origin": "HTTPS://GUESSVERSE.TEST/"}, 200),  # регистр и завершающий слэш
        ({"Origin": "https://evil.test"}, 403),
        ({"Origin": "null"}, 403),
        ({"Referer": "https://evil.test/x"}, 403),
        ({}, 200),  # не браузер: без Origin login-CSRF невозможен
    ],
)
async def test_require_same_origin(
    client: AsyncClient, headers: dict[str, str], status: int
) -> None:
    response = await client.post("/login", headers=headers)
    assert response.status_code == status


# На уровне модуля: аннотации с `from __future__ import annotations` FastAPI разрешает
# через глобальные имена модуля, локальную переменную он бы не увидел.
_EXPLICIT_ISSUER = TestTokenIssuer("explicit")
_EXPLICIT_AUTH = Authenticator(_EXPLICIT_ISSUER.verifier())


async def test_authenticator_with_explicit_verifier() -> None:
    app = FastAPI()  # без app.state.jwt_verifier
    register_problem_handlers(app)

    @app.get("/who")
    async def who(
        p: Annotated[Principal, Depends(_EXPLICIT_AUTH.require_perm("x.read"))],
    ) -> str:
        return str(p.user_id)

    issuer, user = _EXPLICIT_ISSUER, uuid.uuid4()
    async with service_client(app) as client:
        good = issuer.issue(user_id=user, perms=["x.read"])
        ok = await client.get("/who", headers={"Authorization": f"Bearer {good}"})
        assert ok.json() == str(user)
        weak = issuer.issue(perms=[])
        denied = await client.get("/who", headers={"Authorization": f"Bearer {weak}"})
        assert_problem(denied, 403, "auth.forbidden")
        assert_problem(await client.get("/who"), 401, "auth.unauthenticated")


def test_csrf_tokens_are_random_and_long() -> None:
    tokens = {new_csrf_token() for _ in range(50)}
    assert len(tokens) == 50
    assert all(len(t) >= 40 for t in tokens)


def test_app_error_is_base() -> None:
    # Ошибка CSRF — обычная AppError с кодом, её мапит gv_service.problems.
    from gv_auth.csrf import CsrfError

    assert issubclass(CsrfError, AppError)
