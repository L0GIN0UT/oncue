"""Домен identity: чистый Python без фреймворков и IO."""

from identity_service.domain.auth_identity import AuthIdentity, Consent
from identity_service.domain.challenge import ChallengePurpose, EmailChallenge, TrustedDevice
from identity_service.domain.events import (
    ExternalAccountLinked,
    ExternalAccountUnlinked,
    UserDeleted,
    UserProfileBasicsChanged,
    UserRegistered,
    UserRolesChanged,
)
from identity_service.domain.policies import (
    ConsentRequirement,
    PasswordPolicy,
    missing_consents,
    parse_consent_requirements,
)
from identity_service.domain.roles import DEFAULT_ROLE, RoleMatrix
from identity_service.domain.session import (
    RefreshToken,
    ReuseDetected,
    Rotated,
    Session,
    SessionKind,
    rotate_refresh_token,
)
from identity_service.domain.user import User, UserStatus
from identity_service.domain.values import DisplayName, EmailAddress, Nickname, Provider

__all__ = [
    "DEFAULT_ROLE",
    "AuthIdentity",
    "ChallengePurpose",
    "Consent",
    "ConsentRequirement",
    "DisplayName",
    "EmailAddress",
    "EmailChallenge",
    "ExternalAccountLinked",
    "ExternalAccountUnlinked",
    "Nickname",
    "PasswordPolicy",
    "Provider",
    "RefreshToken",
    "ReuseDetected",
    "RoleMatrix",
    "Rotated",
    "Session",
    "SessionKind",
    "TrustedDevice",
    "User",
    "UserDeleted",
    "UserProfileBasicsChanged",
    "UserRegistered",
    "UserRolesChanged",
    "UserStatus",
    "missing_consents",
    "parse_consent_requirements",
    "rotate_refresh_token",
]
