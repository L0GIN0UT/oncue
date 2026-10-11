"""Гостевой Principal, require_user и интерфейсы сервисных токенов (план этапа 2, §2.3)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI

from gv_auth import (
    Principal,
    ServiceAuthenticator,
    ServiceTokenProvider,
    SubjectKind,
    require_perm,
    require_user,
)
from gv_core import UnauthenticatedError
from gv_service.problems import register_problem_handlers
from gv_testkit import assert_problem, service_client
from gv_testkit.auth import TestTokenIssuer

pytestmark = pytest.mark.unit


def _app(issuer: TestTokenIssuer) -> FastAPI:
    app = FastAPI()
    app.state.jwt_verifier = issuer.verifier()
    register_problem_handlers(app)

    @app.get("/me")
    async def me(p: Annotated[Principal, Depends(require_user)]) -> dict[str, str]:
        return {"kind": p.subject_kind}

    @app.get("/admin")
    async def admin(
        p: Annotated[Principal, Depends(require_perm("admin.access"))],
    ) -> dict[str, str]:
        return {"kind": p.subject_kind}

    return app


async def test_guest_token_gives_guest_principal(token_issuer: TestTokenIssuer) -> None:
    principal = await token_issuer.verifier().verify(token_issuer.issue_guest())
    assert principal.is_guest
    assert principal.subject_kind is SubjectKind.GUEST
    assert principal.roles == frozenset({"guest"})
    assert principal.perms == frozenset()


@pytest.mark.parametrize("gst", [False, "true", 1, None])
async def test_only_literal_true_marks_guest(token_issuer: TestTokenIssuer, gst: object) -> None:
    principal = await token_issuer.verifier().verify(token_issuer.issue(extra={"gst": gst}))
    assert not principal.is_guest
    assert principal.subject_kind is SubjectKind.USER


async def test_require_user(token_issuer: TestTokenIssuer) -> None:
    async with service_client(_app(token_issuer)) as client:
        user = {"Authorization": f"Bearer {token_issuer.issue()}"}
        guest = {"Authorization": f"Bearer {token_issuer.issue_guest()}"}
        assert (await client.get("/me", headers=user)).json() == {"kind": "user"}
        assert_problem(await client.get("/me", headers=guest), 403, "auth.guest_forbidden")
        assert_problem(await client.get("/me"), 401, "auth.unauthenticated")


async def test_guest_never_passes_perm_check(token_issuer: TestTokenIssuer) -> None:
    """Даже если гостевой токен по ошибке несёт права, admin-ручки ему закрыты."""
    forged = token_issuer.issue(roles=["guest"], perms=["admin.access"], extra={"gst": True})
    async with service_client(_app(token_issuer)) as client:
        response = await client.get("/admin", headers={"Authorization": f"Bearer {forged}"})
        assert_problem(response, 403, "auth.forbidden")


async def test_service_token_is_rejected_by_user_verifier(
    token_issuer: TestTokenIssuer, service_token: Callable[..., str]
) -> None:
    token = service_token("game", "media_pipeline", ["assets.link"])
    with pytest.raises(UnauthenticatedError):
        await token_issuer.verifier().verify(token)


def test_require_service_needs_scope(token_issuer: TestTokenIssuer) -> None:
    auth = ServiceAuthenticator(token_issuer.verifier(audience="media_pipeline"))
    with pytest.raises(ValueError, match="scope"):
        auth.require_service()
    assert callable(auth.require_service("assets.link"))


def test_token_provider_hides_secret_and_validates_ratio() -> None:
    provider = ServiceTokenProvider("http://identity/t", "game", "s3cr3t", "media_pipeline")
    assert "s3cr3t" not in repr(provider)
    assert provider.audience == "media_pipeline"
    with pytest.raises(ValueError, match="refresh_ratio"):
        ServiceTokenProvider("http://i/t", "game", "x", "m", refresh_ratio=1.0)
