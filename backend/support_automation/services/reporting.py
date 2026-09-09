"""Read-only reporting calculations for the dashboard."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import timedelta
from typing import Any

from ..models import Message, Ticket, utcnow


def _window_start(days: int) -> object:
    return utcnow() - timedelta(days=max(1, min(days, 365)))


def overview(days: int = 30) -> dict[str, Any]:
    start = _window_start(days)
    tickets = Ticket.query.filter(Ticket.created_at >= start).all()
    all_open = Ticket.query.filter(Ticket.status.notin_(["resolved", "closed"])).all()
    overdue = [ticket for ticket in all_open if ticket.is_overdue]
    inbound_count = Message.query.filter(Message.direction == "inbound", Message.created_at >= start).count()
    auto_sent = Message.query.filter(
        Message.direction == "outbound",
        Message.delivery_status == "sent",
        Message.automation_source.isnot(None),
        Message.created_at >= start,
    ).count()
    response_minutes = [
        (ticket.first_response_at - ticket.created_at).total_seconds() / 60
        for ticket in tickets
        if ticket.first_response_at and ticket.created_at
    ]
    category_counts = Counter((ticket.category.name if ticket.category else "Uncategorized") for ticket in tickets)
    priority_counts = Counter(ticket.priority for ticket in tickets)
    status_counts = Counter(ticket.status for ticket in all_open)
    return {
        "periodDays": days,
        "metrics": {
            "ticketsReceived": len(tickets),
            "openBacklog": len(all_open),
            "overdueTickets": len(overdue),
            "automationRate": round((auto_sent / inbound_count * 100), 1) if inbound_count else 0,
            "averageFirstResponseMinutes": round(sum(response_minutes) / len(response_minutes), 1) if response_minutes else None,
            "firstResponsesMeasured": len(response_minutes),
        },
        "byCategory": [{"name": name, "value": value} for name, value in category_counts.most_common()],
        "byPriority": [{"name": name, "value": value} for name, value in priority_counts.most_common()],
        "openByStatus": [{"name": name, "value": value} for name, value in status_counts.most_common()],
    }


def ticket_volume(days: int = 30) -> list[dict[str, Any]]:
    start = _window_start(days)
    buckets: dict[str, int] = defaultdict(int)
    for ticket in Ticket.query.filter(Ticket.created_at >= start).order_by(Ticket.created_at.asc()).all():
        buckets[ticket.created_at.date().isoformat()] += 1
    return [{"date": date, "count": count} for date, count in sorted(buckets.items())]


def agent_workload() -> list[dict[str, Any]]:
    assignments: dict[str, dict[str, Any]] = {}
    tickets = Ticket.query.filter(Ticket.status.notin_(["resolved", "closed"])).all()
    for ticket in tickets:
        key = ticket.assignee.full_name if ticket.assignee else "Unassigned"
        bucket = assignments.setdefault(key, {"agent": key, "open": 0, "overdue": 0, "urgent": 0})
        bucket["open"] += 1
        bucket["overdue"] += int(ticket.is_overdue)
        bucket["urgent"] += int(ticket.priority == "urgent")
    return sorted(assignments.values(), key=lambda item: (-item["overdue"], -item["urgent"], item["agent"]))
