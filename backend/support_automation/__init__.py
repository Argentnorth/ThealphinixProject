"""Customer Email Support Automation Flask application factory."""

from __future__ import annotations

from pathlib import Path

import click
from flask import Flask, jsonify
from flask_cors import CORS
from sqlalchemy.engine import make_url

from .config import Config
from .extensions import db, jwt
from .jobs import start_scheduler
from .models import RevokedToken


def _normalise_sqlite_uri(database_uri: str, project_root: str | Path) -> str:
    """Resolve relative SQLite locations against the project, not Flask's instance path.

    Flask-SQLAlchemy rewrites relative SQLite URIs below ``app.instance_path``.
    Making the path absolute here keeps ``DATABASE_URL=sqlite:///instance/...``
    intuitive and guarantees the directory we create is the one SQLite opens.
    """

    url = make_url(database_uri)
    if not url.drivername.startswith("sqlite") or not url.database or url.database == ":memory:":
        return database_uri
    path = Path(url.database).expanduser()
    if not path.is_absolute():
        path = (Path(project_root) / path).resolve()
    return url.set(database=str(path)).render_as_string(hide_password=False)


def _ensure_sqlite_parent(database_uri: str) -> None:
    """Create a configured SQLite database directory before SQLAlchemy opens it."""

    url = make_url(database_uri)
    if url.drivername.startswith("sqlite") and url.database and url.database != ":memory:":
        Path(url.database).expanduser().parent.mkdir(parents=True, exist_ok=True)


def create_app(config_object: type[Config] = Config) -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config_object)
    app.config["SQLALCHEMY_DATABASE_URI"] = _normalise_sqlite_uri(
        app.config["SQLALCHEMY_DATABASE_URI"], app.config["PROJECT_ROOT"]
    )
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    _ensure_sqlite_parent(app.config["SQLALCHEMY_DATABASE_URI"])
    Path(app.config["ATTACHMENT_DIR"]).mkdir(parents=True, exist_ok=True)

    db.init_app(app)
    jwt.init_app(app)
    CORS(app, resources={r"/api/*": {"origins": app.config["CORS_ORIGINS"]}})

    from .api.admin import admin_bp
    from .api.auth import auth_bp
    from .api.reports import reports_bp
    from .api.templates import templates_bp
    from .api.tickets import attachments_bp, tickets_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(tickets_bp)
    app.register_blueprint(attachments_bp)
    app.register_blueprint(templates_bp)
    app.register_blueprint(reports_bp)
    app.register_blueprint(admin_bp)

    @app.get("/api/v1/health")
    def health():
        return jsonify(status="ok", service="customer-email-support-automation")

    @app.errorhandler(404)
    def not_found(_: Exception):
        return jsonify(error={"code": "not_found", "message": "Resource not found."}), 404

    @app.errorhandler(413)
    def payload_too_large(_: Exception):
        return jsonify(error={"code": "payload_too_large", "message": "Request payload exceeds the upload limit."}), 413

    @app.errorhandler(500)
    def internal_error(_: Exception):
        db.session.rollback()
        return jsonify(error={"code": "internal_error", "message": "An unexpected server error occurred."}), 500

    @jwt.token_in_blocklist_loader
    def token_is_revoked(_: dict, payload: dict) -> bool:
        return RevokedToken.query.filter_by(jti=payload["jti"]).first() is not None

    @jwt.unauthorized_loader
    def missing_token(reason: str):
        return jsonify(error={"code": "unauthorized", "message": reason}), 401

    @jwt.invalid_token_loader
    def invalid_token(reason: str):
        return jsonify(error={"code": "invalid_token", "message": reason}), 422

    @jwt.revoked_token_loader
    def revoked_token(_: dict, __: dict):
        return jsonify(error={"code": "revoked_token", "message": "This session token has been revoked."}), 401

    @app.cli.command("init-db")
    def init_db_command() -> None:
        """Create database tables for local development or a fresh deployment."""
        db.create_all()
        click.echo("Database tables created.")

    @app.cli.command("seed-reference")
    def seed_reference_command() -> None:
        """Install category and approved-template reference policy data."""
        from .services.seed import seed_reference_data

        db.create_all()
        click.echo(seed_reference_data())

    @app.cli.command("seed-demo")
    def seed_demo_command() -> None:
        """Install an explicitly requested local demo account and ticket set."""
        from .services.seed import seed_demo_data

        db.create_all()
        click.echo(seed_demo_data())

    @app.cli.command("poll-mailbox")
    def poll_mailbox_command() -> None:
        from .services.ingestion import poll_mailbox

        click.echo(poll_mailbox())

    @app.cli.command("deliver-outbox")
    def deliver_outbox_command() -> None:
        from .services.mail import deliver_pending

        click.echo(deliver_pending())

    @app.cli.command("check-sla")
    def check_sla_command() -> None:
        from .services.mail import escalate_overdue_tickets

        click.echo(escalate_overdue_tickets())

    with app.app_context():
        if app.config["AUTO_CREATE_SCHEMA"]:
            db.create_all()

    if app.config["SCHEDULER_ENABLED"]:
        start_scheduler(app)
    return app
