from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import jwt
from fastapi import HTTPException, Request, status
from jwt import PyJWKClient

from rank_rent.security.ssrf import UnsafeURLError, validate_outbound_url
from rank_rent.settings import Settings


class Role(StrEnum):
    admin = "admin"
    operator = "operator"
    reviewer = "reviewer"
    read_only = "read_only"


class Permission(StrEnum):
    view_opportunities = "view_opportunities"
    run_testing_scan = "run_testing_scan"
    run_full_scan = "run_full_scan"
    override_evidence = "override_evidence"
    approve_opportunity = "approve_opportunity"
    change_production_limits = "change_production_limits"
    deploy_property = "deploy_property"
    manage_routing = "manage_routing"
    export_data = "export_data"
    delete_data = "delete_data"


ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.admin: frozenset(Permission),
    Role.operator: frozenset(
        {
            Permission.view_opportunities,
            Permission.run_testing_scan,
            Permission.run_full_scan,
            Permission.deploy_property,
            Permission.manage_routing,
        }
    ),
    Role.reviewer: frozenset(
        {
            Permission.view_opportunities,
            Permission.override_evidence,
            Permission.approve_opportunity,
        }
    ),
    Role.read_only: frozenset({Permission.view_opportunities}),
}


@dataclass(frozen=True)
class Principal:
    user_id: str
    role: Role
    email: str | None = None
    token_id: str | None = None
    auth_method: str = "oidc"

    def permits(self, permission: Permission) -> bool:
        return permission in ROLE_PERMISSIONS[self.role]


class AuthenticationError(ValueError):
    pass


class OIDCVerifier:
    def __init__(self, settings: Settings) -> None:
        issuer = settings.oidc_issuer.rstrip("/")
        jwks_url = settings.oidc_jwks_url
        if not jwks_url:
            raise AuthenticationError("OIDC_JWKS_URL must be configured.")
        try:
            validate_outbound_url(
                jwks_url,
                allowed_hosts=settings.oidc_allowed_jwks_hosts,
                resolve_dns=False,
            )
        except UnsafeURLError as exc:
            raise AuthenticationError(f"Unsafe OIDC JWKS URL: {exc}") from exc
        self.issuer = issuer
        self.audience = settings.oidc_audience
        self.roles_claim = settings.oidc_roles_claim
        self.allowed_algorithms = list(settings.oidc_allowed_algorithms)
        self.jwks = PyJWKClient(
            jwks_url,
            cache_keys=True,
            lifespan=settings.oidc_jwks_cache_seconds,
            timeout=settings.outbound_http_timeout_seconds,
        )

    def verify(self, token: str) -> Principal:
        try:
            signing_key = self.jwks.get_signing_key_from_jwt(token)
            claims: dict[str, Any] = jwt.decode(
                token,
                signing_key.key,
                algorithms=self.allowed_algorithms,
                audience=self.audience,
                issuer=self.issuer,
                options={"require": ["exp", "iat", "iss", "sub"]},
                leeway=30,
            )
        except jwt.PyJWTError as exc:
            raise AuthenticationError("OIDC token validation failed.") from exc
        roles = claims.get(self.roles_claim, [])
        if isinstance(roles, str):
            roles = [roles]
        role = next(
            (candidate for candidate in Role if candidate.value in roles),
            None,
        )
        if role is None:
            raise AuthenticationError("OIDC token does not grant a recognized role.")
        return Principal(
            user_id=str(claims["sub"]),
            role=role,
            email=str(claims["email"]) if claims.get("email") else None,
            token_id=str(claims["jti"]) if claims.get("jti") else None,
        )


def local_principal(request: Request, settings: Settings) -> Principal:
    role_value = request.headers.get("x-local-role", settings.local_auth_default_role)
    user_id = request.headers.get("x-local-user", settings.local_auth_default_user)
    try:
        role = Role(role_value)
    except ValueError as exc:
        raise AuthenticationError("Unknown local development role.") from exc
    return Principal(user_id=user_id, role=role, auth_method="local")


def authenticate_request(
    request: Request,
    settings: Settings,
    oidc_verifier: OIDCVerifier | None,
) -> Principal:
    authorization = request.headers.get("authorization", "")
    if authorization.startswith("Bearer "):
        if oidc_verifier is None:
            raise AuthenticationError("OIDC authentication is not configured.")
        return oidc_verifier.verify(authorization.removeprefix("Bearer ").strip())
    if settings.local_auth_enabled and settings.app_env in {"local", "test", "development"}:
        return local_principal(request, settings)
    raise AuthenticationError("Authentication is required.")


def principal_from_request(request: Request) -> Principal:
    principal = getattr(request.state, "principal", None)
    if not isinstance(principal, Principal):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return principal


def require_permission(request: Request, permission: Permission) -> Principal:
    principal = principal_from_request(request)
    if not principal.permits(permission):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to perform this action.",
        )
    return principal
