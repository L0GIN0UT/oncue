"""gv_auth — проверка JWT и проверки прав в API."""

from gv_auth.fastapi import ACCESS_COOKIE, current_principal, optional_principal, require_perm
from gv_auth.principal import Principal, Sanction
from gv_auth.verifier import ALLOWED_ALGORITHMS, JwtVerifier, http_jwks_loader

__all__ = [
    "ACCESS_COOKIE",
    "ALLOWED_ALGORITHMS",
    "JwtVerifier",
    "Principal",
    "Sanction",
    "current_principal",
    "http_jwks_loader",
    "optional_principal",
    "require_perm",
]
