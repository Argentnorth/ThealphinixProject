"""Authentication helpers and role-based authorization decorators."""

from __future__ import annotations

from functools import wraps
from typing import Callable, ParamSpec, TypeVar

from flask import g, jsonify
from flask_jwt_extended import get_jwt_identity, verify_jwt_in_request

from .extensions import db
from .models import User

P = ParamSpec("P")
R = TypeVar("R")


def get_authenticated_user() -> User | None:
    """Resolve the user represented by the current access token."""

    identity = get_jwt_identity()
    if identity is None:
        return None
    try:
        return db.session.get(User, int(identity))
    except (TypeError, ValueError):
        return None


def roles_required(*allowed_roles: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Require a valid token and one of the provided application roles."""

    def decorator(view: Callable[P, R]) -> Callable[P, R]:
        @wraps(view)
        def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
            verify_jwt_in_request()
            user = get_authenticated_user()
            if user is None or not user.is_active:
                return jsonify(error={"code": "unauthorized", "message": "Active user required."}), 401  # type: ignore[return-value]
            if allowed_roles and user.role not in allowed_roles:
                return jsonify(error={"code": "forbidden", "message": "Insufficient role for this action."}), 403  # type: ignore[return-value]
            g.current_user = user
            return view(*args, **kwargs)

        return wrapped

    return decorator


def current_user() -> User:
    """Return the user set by ``roles_required`` for the current request."""

    return g.current_user
