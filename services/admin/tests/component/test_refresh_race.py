"""Refresh admin-сессии из двух вкладок: один refresh-токен не уходит в identity дважды.

identity ротирует refresh-токены и повтор старого считает кражей (``auth.refresh_reused``):
без блокировки вторая вкладка выкинула бы модератора из админки.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from admin_service.core.auth import IdentitySession, access_token, store_tokens
from admin_service.core.models import AdminUser
from admin_service.core.permissions import PermissionSync
from django.contrib.sessions.backends.db import SessionStore
from django.db import connections
from django.test import RequestFactory

from gv_core.ids import uuid7
from gv_testkit import TestTokenIssuer

pytestmark = pytest.mark.component

PERMS = ("admin.access",)


def _session(token: str, refresh: str, expires_in: int) -> IdentitySession:
    return IdentitySession(
        access_token=token, refresh_token=refresh, expires_in=expires_in, display_name="M"
    )


def _user() -> AdminUser:
    user_id = uuid7()
    user = AdminUser.objects.create(username=f"identity-{user_id}", identity_user_id=user_id)
    PermissionSync.apply(user, roles=["moderator"], perms=PERMS)
    return user


def _expired_session(issuer: TestTokenIssuer, user: AdminUser) -> str:
    store = SessionStore()
    token = issuer.issue(user_id=user.identity_user_id, perms=PERMS)
    store_tokens(store, _session(token, "r1", expires_in=0))
    store.create()
    assert store.session_key is not None
    return store.session_key


def _request(session_key: str, user: AdminUser) -> Any:
    request = RequestFactory().get("/catalog/")
    request.session = SessionStore(session_key)
    request.session.load()  # снимок сессии на начало запроса, как в SessionMiddleware
    request.user = user
    return request


@pytest.mark.django_db
def test_tab_reuses_tokens_refreshed_by_another_tab(
    identity: Any, token_issuer: TestTokenIssuer, problem: Any
) -> None:
    user = _user()
    key = _expired_session(token_issuer, user)
    request = _request(key, user)  # вкладка A прочитала сессию с истёкшим access
    fresh = token_issuer.issue(user_id=user.identity_user_id, perms=PERMS)
    other = SessionStore(key)  # вкладка B тем временем обновила токены
    other.load()
    store_tokens(other, _session(fresh, "r2", expires_in=900))
    other.save()
    refresh = identity.route("POST", "/internal/v1/admin-sessions/refresh").respond(
        401, json=problem(401, "auth.refresh_reused")
    )

    assert access_token(request) == fresh
    assert not refresh.called


@pytest.fixture
def close_thread_connections() -> Iterator[None]:
    yield
    connections.close_all()


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("close_thread_connections")
def test_concurrent_refresh_sends_refresh_token_once(
    identity: Any, token_issuer: TestTokenIssuer, session_body: Any, problem: Any
) -> None:
    user = _user()
    key = _expired_session(token_issuer, user)
    fresh = token_issuer.issue(user_id=user.identity_user_id, perms=PERMS)
    calls: list[str] = []
    guard = threading.Lock()

    def identity_refresh(request: httpx.Request) -> httpx.Response:
        with guard:
            calls.append(request.content.decode())
            first = len(calls) == 1
        if not first:  # повтор того же refresh-токена — как в identity: отзыв
            return httpx.Response(401, json=problem(401, "auth.refresh_reused"))
        time.sleep(0.3)  # вторая вкладка успевает прийти, пока первая ждёт identity
        body = session_body(fresh, user_id=user.identity_user_id, refresh="r2")
        return httpx.Response(200, json=body)

    identity.route("POST", "/internal/v1/admin-sessions/refresh").mock(side_effect=identity_refresh)
    requests = [_request(key, user), _request(key, user)]  # обе вкладки видят истёкший access
    results: list[object] = [None, None]

    def tab(i: int) -> None:
        try:
            results[i] = access_token(requests[i])
        except Exception as exc:
            results[i] = exc
        finally:
            connections.close_all()

    threads = [threading.Thread(target=tab, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert results == [fresh, fresh]
    assert len(calls) == 1
