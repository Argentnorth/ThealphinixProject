"""Shared Flask extension instances."""

from flask_jwt_extended import JWTManager
from flask_sqlalchemy import SQLAlchemy

# expire_on_commit=False keeps response serialization stable after service commits.
db = SQLAlchemy(session_options={"expire_on_commit": False})
jwt = JWTManager()
