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
    require_user,
)
from gv_auth.principal import Principal, Sanction, SubjectKind
from gv_auth.revocation import (
    REVOKED_SID_PREFIX,
    RedisRevocationChecker,
    RevocationChecker,
    revoked_sid_key,
)
from gv_auth.service import (
    SERVICE_SUBJECT_PREFIX,
    ServiceAuthenticator,
    ServicePrincipal,
    ServiceTokenProvider,
    SyncServiceTokenProvider,
    parse_scope,
    service_principal_from_claims,
)
from gv_auth.verifier import ALLOWED_ALGORITHMS, DEFAULT_LEEWAY_S, JwtVerifier, http_jwks_loader

__all__ = [
    "ACCESS_COOKIE",
    "ALLOWED_ALGORITHMS",
    "CSRF_COOKIE",
    "CSRF_HEADER",
    "DEFAULT_LEEWAY_S",
    "REVOKED_SID_PREFIX",
    "SERVICE_SUBJECT_PREFIX",
    "Authenticator",
    "CsrfError",
    "JwtVerifier",
    "Principal",
    "RedisRevocationChecker",
    "RevocationChecker",
    "Sanction",
    "ServiceAuthenticator",
    "ServicePrincipal",
    "ServiceTokenProvider",
    "SubjectKind",
    "SyncServiceTokenProvider",
    "TokenSource",
    "current_principal",
    "enforce_csrf",
    "extract_token",
    "http_jwks_loader",
    "new_csrf_token",
    "optional_principal",
    "parse_scope",
    "require_csrf",
    "require_perm",
    "require_same_origin",
    "require_user",
    "revoked_sid_key",
    "service_principal_from_claims",
]
