"""Read-only operational metrics endpoints for support leaders."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from ..security import roles_required
from ..services.reporting import agent_workload, overview, ticket_volume

reports_bp = Blueprint("reports", __name__, url_prefix="/api/v1/reports")


def _days() -> int:
    try:
        value = int(request.args.get("days", "30"))
    except ValueError:
        value = 30
    return min(365, max(1, value))


@reports_bp.get("/overview")
@roles_required("admin")
def get_overview():
    return jsonify(overview(_days()))


@reports_bp.get("/volume")
@roles_required("admin")
def get_ticket_volume():
    days = _days()
    return jsonify(periodDays=days, items=ticket_volume(days))


@reports_bp.get("/workload")
@roles_required("admin")
def get_agent_workload():
    return jsonify(items=agent_workload())
