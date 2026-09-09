"""Authentication endpoints using short-lived access and renewable refresh JWTs."""

from __future__ import annotations

from datetime import datetime, timezone

from flask import Blueprint, jsonify, request
from flask_jwt_extended import (
    create_access_token,
    create_refresh_token,
    get_jwt,
    get_jwt_identity,
    jwt_required,
)

from ..extensions import db
from ..models import RevokedToken, User, utcnow
from ..security import current_user, get_authenticated_user, roles_required
from ..services.audit import record_audit

auth_bp = Blueprint("auth", __name__, url_prefix="/api/v1/auth")


def _tokens(user: User) -> dict[str, str]:
    claims = {"role": user.role, "email": user.email}
    return {
        "accessToken": create_access_token(identity=str(user.id), additional_claims=claims),
        "refreshToken": create_refresh_token(identity=str(user.id), additional_claims=claims),
    }


@auth_bp.post("/login")
def login():
    payload = request.get_json(silent=True) or {}
    email = str(payload.get("email", "")).strip().lower()
    password = str(payload.get("password", ""))
    if not email or not password:
        return jsonify(error={"code": "validation_error", "message": "email and password are required."}), 400
    user = User.query.filter_by(email=email).first()
    if not user or not user.is_active or not user.check_password(password):
        return jsonify(error={"code": "invalid_credentials", "message": "Email or password is incorrect."}), 401
    user.last_login_at = utcnow()
    record_audit("user_logged_in", actor=user, details={"remoteAddress": request.remote_addr})
    db.session.commit()
    return jsonify(user=user.to_dict(), **_tokens(user))


@auth_bp.post("/refresh")
@jwt_required(refresh=True)
def refresh():
    user = get_authenticated_user()
    if user is None or not user.is_active:
        return jsonify(error={"code": "unauthorized", "message": "Active user required."}), 401
    return jsonify(user=user.to_dict(), **_tokens(user))


@auth_bp.post("/logout")
@jwt_required(verify_type=False)
def logout():
    claims = get_jwt()
    identity = get_jwt_identity()
    try:
        user_id = int(identity)
        expires_at = datetime.fromtimestamp(claims["exp"], tz=timezone.utc).replace(tzinfo=None)
    except (KeyError, TypeError, ValueError, OSError):
        return jsonify(error={"code": "invalid_token", "message": "Unable to revoke this token."}), 400
    if not RevokedToken.query.filter_by(jti=claims["jti"]).first():
        db.session.add(
            RevokedToken(
                jti=claims["jti"],
                token_type=claims.get("type", "access"),
                user_id=user_id,
                expires_at=expires_at,
            )
        )
        db.session.commit()
    return "", 204


@auth_bp.get("/me")
@roles_required("admin", "agent")
def me():
    return jsonify(user=current_user().to_dict())


@auth_bp.patch("/me/password")
@roles_required("admin", "agent")
def change_password():
    payload = request.get_json(silent=True) or {}
    old_password = str(payload.get("oldPassword", ""))
    new_password = str(payload.get("newPassword", ""))
    user = current_user()
    if not user.check_password(old_password):
        return jsonify(error={"code": "invalid_credentials", "message": "Current password is incorrect."}), 400
    if len(new_password) < 10:
        return jsonify(error={"code": "validation_error", "message": "New password must have at least 10 characters."}), 400
    user.set_password(new_password)
    record_audit("password_changed", actor=user)
    db.session.commit()
    return "", 204
