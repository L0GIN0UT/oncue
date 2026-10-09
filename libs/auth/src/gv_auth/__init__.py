"""gv_auth — проверка JWT, CSRF и проверки прав в API."""

from gv_auth.csrf import (
    CSRF_COOKIE,
    CSRF_HEADER,
    CsrfError,
    enforce_csrf,
    new_csrf_token,
    require_csrf,
    require_same_origin,
)
from gv_auth.fastapi import (
    ACCESS_COOKIE,
    Authenticator,
    TokenSource,
    current_principal,
    extract_token,
    optional_principal,
    require_perm,
)
from gv_auth.principal import Principal, Sanction
from gv_auth.revocation import (
    REVOKED_SID_PREFIX,
    RedisRevocationChecker,
    RevocationChecker,
    revoked_sid_key,
)
from gv_auth.verifier import ALLOWED_ALGORITHMS, JwtVerifier, http_jwks_loader

__all__ = [
    "ACCESS_COOKIE",
    "ALLOWED_ALGORITHMS",
    "CSRF_COOKIE",
    "CSRF_HEADER",
    "REVOKED_SID_PREFIX",
    "Authenticator",
    "CsrfError",
    "JwtVerifier",
    "Principal",
    "RedisRevocationChecker",
    "RevocationChecker",
    "Sanction",
    "TokenSource",
    "current_principal",
    "enforce_csrf",
    "extract_token",
    "http_jwks_loader",
    "new_csrf_token",
    "optional_principal",
    "require_csrf",
    "require_perm",
    "require_same_origin",
    "revoked_sid_key",
]
