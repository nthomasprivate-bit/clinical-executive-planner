"""Calendar CRUD, recurrence expansion, and conflict checks.

Datetimes are validated Melbourne local wall times. Read ranges are inclusive
dates, events use half-open intervals [start, end), and the Western Health
``availability`` window is an envelope rather than a blocking appointment.
Write functions flush for immediate validation but leave commit to the caller.
"""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import EVENT_STATUSES, KINDS, Event, RecurringCommitment, Task
from .security import audit
from .timeutils import local_datetime


@dataclass(frozen=True)
class Occurrence:
    id: str
    title: str
    start: datetime
    end: datetime
    source: str
    status: str
    kind: str
    location: str = ""
    task_id: int | None = None
    recurring_id: int | None = None
    event_id: int | None = None


SOURCE_COLORS = {
    "Western Health": "#2563EB",
    "Sunshine Private Hospital": "#7C3AED",
    "Keilor East": "#059669",
    "Gmail": "#DC2626",
    "Academic": "#D97706",
    "Planner": "#0891B2",
    "Manual": "#64748B",
    "Transitions": "#94A3B8",
}


def source_color(source: str) -> str:
    """Stable colours for named sources; unknown sources receive slate."""
    return SOURCE_COLORS.get(source, "#64748B")


def _text(value: object, name: str, maximum: int, *, required: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be text.")
    result = value.strip()
    if required and not result:
        raise ValueError(f"{name} is required.")
    if len(result) > maximum:
        raise ValueError(f"{name} must be at most {maximum} characters.")
    return result


def validated_event_fields(fields: dict) -> dict:
    """Validate a complete event without touching a Session (also used by CSV)."""
    allowed = {"title", "start", "end", "source", "external_id", "status", "kind", "location", "description", "task_id"}
    if unknown := set(fields) - allowed:
        raise ValueError(f"Unknown event fields: {', '.join(sorted(unknown))}.")
    result = {
        "source": "Manual", "external_id": None, "status": "Confirmed", "kind": "meeting",
        "location": "", "description": "", "task_id": None, **fields,
    }
    for name, maximum in (("title", 200), ("source", 80), ("location", 200), ("description", 20000)):
        result[name] = _text(result.get(name), name, maximum, required=name in {"title", "source"})
    for name in ("start", "end"):
        if not isinstance(result.get(name), datetime):
            raise ValueError(f"{name} requires a full date and time.")
        result[name] = local_datetime(result[name])
    if result["end"] <= result["start"]:
        raise ValueError("Event end must be after its start.")
    if result["status"] not in EVENT_STATUSES:
        raise ValueError(f"status must be one of {', '.join(EVENT_STATUSES)}.")
    if result["kind"] not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}.")
    if result["external_id"] is not None:
        result["external_id"] = _text(result["external_id"], "external_id", 200, required=True)
    if result["task_id"] is not None and (type(result["task_id"]) is not int or result["task_id"] < 1):
        raise ValueError("task_id must be a positive integer.")
    return result


def save_event(session: Session, event_id: int | None = None, **fields) -> Event:
    """Create or partially update a validated event; never commit."""
    event = session.get(Event, event_id) if event_id is not None else None
    if event_id is not None and event is None:
        raise ValueError("Event does not exist.")
    current = {name: getattr(event, name) for name in (
        "title", "start", "end", "source", "external_id", "status", "kind", "location", "description", "task_id"
    )} if event is not None else {}
    values = validated_event_fields({**current, **fields})
    if values["task_id"] is not None and session.get(Task, values["task_id"]) is None:
        raise ValueError("Linked task does not exist.")
    if values["external_id"] is not None:
        duplicate = session.scalar(select(Event).where(Event.source == values["source"], Event.external_id == values["external_id"]))
        if duplicate is not None and duplicate is not event:
            raise ValueError("This source and external ID already exist; use calendar import to update it.")
    if event is None:
        event = Event(**values)
        session.add(event)
    else:
        for name, value in values.items():
            setattr(event, name, value)
    session.flush()
    audit(session, "event.updated" if event_id is not None else "event.created", f"event:{event.id}")
    return event


def save_recurring(session: Session, recurring_id: int | None = None, **fields) -> RecurringCommitment:
    """Create or update a weekly recurrence; weekday is Monday=0 to Sunday=6."""
    names = ("title", "weekday", "start_time", "end_time", "source", "status", "kind", "location", "active", "effective_from", "effective_until")
    if unknown := set(fields) - set(names):
        raise ValueError(f"Unknown recurrence fields: {', '.join(sorted(unknown))}.")
    row = session.get(RecurringCommitment, recurring_id) if recurring_id is not None else None
    if recurring_id is not None and row is None:
        raise ValueError("Recurring commitment does not exist.")
    values = {"status": "Confirmed", "kind": "commitment", "location": "", "active": True,
              "effective_from": None, "effective_until": None}
    if row is not None:
        values.update({name: getattr(row, name) for name in names})
    values.update(fields)
    for name, maximum in (("title", 200), ("source", 80), ("location", 200)):
        values[name] = _text(values.get(name), name, maximum, required=name != "location")
    if type(values.get("weekday")) is not int or not 0 <= values["weekday"] <= 6:
        raise ValueError("weekday must be an integer from 0 (Monday) to 6 (Sunday).")
    for name in ("start_time", "end_time"):
        if not isinstance(values.get(name), time) or values[name].tzinfo is not None:
            raise ValueError(f"{name} must be a local time without a timezone.")
    if values["end_time"] <= values["start_time"]:
        raise ValueError("Recurring end time must be after start time on the same day.")
    if values["status"] not in EVENT_STATUSES or values["kind"] not in KINDS:
        raise ValueError("Invalid recurring status or kind.")
    if type(values["active"]) is not bool:
        raise ValueError("active must be true or false.")
    for name in ("effective_from", "effective_until"):
        if values[name] is not None and type(values[name]) is not date:
            raise ValueError(f"{name} must be a date.")
    if values["effective_from"] and values["effective_until"] and values["effective_until"] < values["effective_from"]:
        raise ValueError("Recurrence effective end cannot precede its start.")
    if row is None:
        row = RecurringCommitment(**values)
        session.add(row)
    else:
        for name, value in values.items():
            setattr(row, name, value)
    session.flush()
    audit(session, "recurring.updated" if recurring_id is not None else "recurring.created", f"recurring:{row.id}")
    return row


def delete_event(session: Session, event_id: int) -> None:
    event = session.get(Event, event_id)
    if event is None:
        raise ValueError("Event does not exist.")
    session.delete(event)
    session.flush()
    audit(session, "event.deleted", f"event:{event_id}")


def get_occurrences(session: Session, start_date: date, end_date: date) -> list[Occurrence]:
    """Expand active recurrences and stored events over an inclusive date range."""
    if type(start_date) is not date or type(end_date) is not date or end_date < start_date:
        raise ValueError("A valid inclusive date range is required.")
    if (end_date - start_date).days > 3660:
        raise ValueError("Calendar date ranges are limited to ten years.")
    lower = datetime.combine(start_date, time.min)
    upper = datetime.combine(end_date + timedelta(days=1), time.min)
    result = [Occurrence(
        id=f"event:{row.id}", title=row.title, start=row.start, end=row.end,
        source=row.source, status=row.status, kind=row.kind, location=row.location,
        task_id=row.task_id, event_id=row.id,
    ) for row in session.scalars(select(Event).where(Event.start < upper, Event.end > lower))]
    recurring = list(session.scalars(select(RecurringCommitment).where(RecurringCommitment.active.is_(True))))
    day = start_date
    while day <= end_date:
        for row in recurring:
            if row.weekday != day.weekday() or (row.effective_from and day < row.effective_from) or (row.effective_until and day > row.effective_until):
                continue
            result.append(Occurrence(
                id=f"recurring:{row.id}:{day.isoformat()}", title=row.title,
                start=local_datetime(datetime.combine(day, row.start_time)),
                end=local_datetime(datetime.combine(day, row.end_time)),
                source=row.source, status=row.status, kind=row.kind, location=row.location,
                recurring_id=row.id,
            ))
        day += timedelta(days=1)
    return sorted(result, key=lambda item: (item.start, item.end, item.id))


def detect_conflicts(occurrences: list[Occurrence]) -> list[tuple[Occurrence, Occurrence]]:
    """Return overlapping blocking pairs. Tentative appointments still block."""
    blocking = sorted((item for item in occurrences if item.kind != "availability"), key=lambda item: item.start)
    active: list[Occurrence] = []
    conflicts = []
    for item in blocking:
        active = [other for other in active if other.end > item.start]
        conflicts.extend((other, item) for other in active if other.id != item.id and other.start < item.end)
        active.append(item)
    return conflicts
