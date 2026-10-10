"""Набор use-cases identity, который composition root передаёт в API."""

from __future__ import annotations

from dataclasses import dataclass

from identity_service.application.commands.account import DeleteAccount, GrantConsents
from identity_service.application.commands.auth import (
    AdminLogin,
    LoginWithPassword,
    Logout,
    LogoutAll,
    RefreshSession,
    RegisterWithEmail,
    RequestPasswordReset,
    ResetPassword,
    VerifyEmail,
)
from identity_service.application.commands.oauth import (
    CompleteOAuth,
    StartOAuth,
    UnlinkExternalAccount,
)
from identity_service.application.commands.roles import AssignRoles, UpdateRoleMatrix
from identity_service.application.commands.service_tokens import IssueServiceToken
from identity_service.application.config import AuthConfig
from identity_service.application.queries import (
    GetJwks,
    GetRoleMatrix,
    GetUser,
    ListLinkedAccounts,
    ListUsers,
)


@dataclass(frozen=True)
class IdentityUseCases:
    """Все use-cases сервиса (DI без фреймворка, ADR 0011)."""

    config: AuthConfig
    register: RegisterWithEmail
    verify_email: VerifyEmail
    login: LoginWithPassword
    admin_login: AdminLogin
    refresh: RefreshSession
    logout: Logout
    logout_all: LogoutAll
    request_password_reset: RequestPasswordReset
    reset_password: ResetPassword
    start_oauth: StartOAuth
    complete_oauth: CompleteOAuth
    unlink: UnlinkExternalAccount
    grant_consents: GrantConsents
    delete_account: DeleteAccount
    assign_roles: AssignRoles
    update_role_matrix: UpdateRoleMatrix
    get_user: GetUser
    list_users: ListUsers
    list_linked: ListLinkedAccounts
    get_role_matrix: GetRoleMatrix
    get_jwks: GetJwks
    issue_service_token: IssueServiceToken
