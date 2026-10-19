"""Deterministic proposals and explicit, transactional approval of planner blocks."""
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta
import hashlib
import json
from typing import Iterable
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from .models import Event, Setting, Task
from .priority import PriorityConfig, rank_tasks
from .timeutils import local_datetime


@dataclass(frozen=True)
class PlannerConfig:
    patient_minutes: int = 120
    academic_minutes: int = 60
    lunch_minutes: int = 30
    lunch_start: str = "12:30"
    transition_minutes: int = 5
    min_block_minutes: int = 15

    def __post_init__(self):
        for name in ("patient_minutes", "academic_minutes", "lunch_minutes", "transition_minutes"):
            value = getattr(self, name)
            if not isinstance(value, int) or not 0 <= value <= 1440:
                raise ValueError(f"{name} must be an integer between 0 and 1440.")
        if not isinstance(self.min_block_minutes, int) or not 1 <= self.min_block_minutes <= 390:
            raise ValueError("Minimum block duration must be between 1 and 390 minutes.")
        try:
            parsed = time.fromisoformat(self.lunch_start)
        except (ValueError, TypeError) as exc:
            raise ValueError("Lunch start must be a local HH:MM time.") from exc
        if parsed.tzinfo or parsed.second or parsed.microsecond:
            raise ValueError("Lunch start must be a local HH:MM time.")


@dataclass(frozen=True)
class PlanBlock:
    start: datetime
    end: datetime
    title: str
    kind: str
    task_id: int | None = None

    @property
    def minutes(self) -> int:
        return _minutes(self.start, self.end)


@dataclass
class Plan:
    day: date
    blocks: list[PlanBlock]
    unmet_minutes: dict[str, int]
    suggestions: list[str]
    paperwork_minutes: int
    patient_minutes: int
    academic_minutes: int
    free_minutes: int
    config: PlannerConfig
    priority_config: PriorityConfig
    basis: str
    id: str = field(default_factory=lambda: uuid4().hex)


def _minutes(start: datetime, end: datetime) -> int:
    return max(0, int((end - start).total_seconds() // 60))


def _merge(intervals: Iterable[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    result = []
    for start, end in sorted(intervals):
        if start >= end:
            continue
        if result and start <= result[-1][1]:
            result[-1] = (result[-1][0], max(end, result[-1][1]))
        else:
            result.append((start, end))
    return result


def _subtract(intervals, start, end):
    result = []
    for left, right in intervals:
        if start >= right or end <= left:
            result.append((left, right))
        else:
            if left < start:
                result.append((left, start))
            if right > end:
                result.append((end, right))
    return result


def _intersections(intervals, start, end):
    return [(max(left, start), min(right, end)) for left, right in intervals
            if left < end and start < right]


def _normal_events(occurrences):
    """Keep stable event attributes and discard duplicate supplied occurrences."""
    events = {}
    for occurrence in occurrences:
        start, end = local_datetime(occurrence.start), local_datetime(occurrence.end)
        if end <= start:
            raise ValueError("Calendar events must end after they start.")
        values = (start, end, getattr(occurrence, "title", ""),
                  getattr(occurrence, "kind", "meeting"), getattr(occurrence, "source", "Manual"),
                  getattr(occurrence, "status", "Confirmed"), getattr(occurrence, "task_id", None))
        events[values] = values
    return sorted(events.values(), key=lambda row: (row[0], row[1], row[2], str(row[-1])))


def _basis(day, events, tasks, config, priority_config):
    fields = ("id", "title", "description", "category", "source", "created_date", "due_date",
              "estimated_minutes", "status", "dependencies", "last_verified_date", "safety_critical",
              "consequences", "importance", "dependency_impact", "completed_at", "deferred_to")
    snapshot = {"day": day, "events": events,
                "tasks": sorted([tuple(getattr(task, key, None) for key in fields) for task in tasks], key=lambda row: row[0] or 0),
                "config": asdict(config), "priority": asdict(priority_config)}
    return hashlib.sha256(json.dumps(snapshot, default=str, sort_keys=True).encode()).hexdigest()


def propose_plan(day: date, occurrences: Iterable, tasks: Iterable,
                 config: PlannerConfig | None = None,
                 priority_config: PriorityConfig | None = None) -> Plan:
    """Propose only inside Western Health availability; never mutate inputs.

    Pass ``planning_occurrences`` for persisted data so existing task allocations
    on every date reduce outstanding estimated work. Tentative events also block
    time, while only confirmed care/reading blocks satisfy the daily targets.
    Dependencies must already be Completed before a dependent task is eligible.
    """
    config, priority_config = config or PlannerConfig(), priority_config or PriorityConfig()
    tasks = list(tasks)
    events = _normal_events(occurrences)
    basis = _basis(day, events, tasks, config, priority_config)
    day_start, day_end = datetime.combine(day, time.min), datetime.combine(day + timedelta(days=1), time.min)
    wh_start, wh_end = datetime.combine(day, time(9)), datetime.combine(day, time(15, 30))
    availability = _merge((max(start, wh_start), min(end, wh_end))
                          for start, end, _, kind, source, _, _ in events
                          if kind == "availability" and source == "Western Health"
                          and start < wh_end and end > wh_start)
    fixed = [row for row in events if row[3] != "availability" and row[0] < day_end and row[1] > day_start]
    free = list(availability)
    blocks: list[PlanBlock] = []
    suggestions = []
    transition = timedelta(minutes=config.transition_minutes)

    for start, end, *_ in fixed:
        free = _subtract(free, start, end)

    def transition_reserve(start, end):
        nonlocal free
        if start >= end:
            return
        pieces = _intersections(free, start, end)
        for left, right in pieces:
            if _minutes(left, right):
                blocks.append(PlanBlock(left, right, "Transition and preparation", "transition"))
        free = _subtract(free, start, end)

    # Existing appointments need preparation and recovery time too.
    if config.transition_minutes:
        for start, end, _, kind, *_ in fixed:
            if kind != "transition":
                transition_reserve(start - transition, start)
                transition_reserve(end, end + transition)

    def reserve(start, end, title, kind, task_id=None):
        nonlocal free
        free = _subtract(free, start, end)
        blocks.append(PlanBlock(start, end, title, kind, task_id))
        if config.transition_minutes:
            transition_reserve(start - transition, start)
            transition_reserve(end, end + transition)

    def existing_minutes(kind):
        segments = [piece for start, end, _, event_kind, _, status, _ in fixed
                    if event_kind == kind and status == "Confirmed"
                    for piece in _intersections(availability, start, end)]
        return sum(_minutes(start, end) for start, end in _merge(segments))

    existing_patient = existing_minutes("patient_care")
    existing_academic = existing_minutes("academic_reading")
    existing_lunch = existing_minutes("lunch")
    lunch_needed = max(0, config.lunch_minutes - existing_lunch)
    lunch_scheduled = 0
    if lunch_needed:
        preferred = datetime.combine(day, time.fromisoformat(config.lunch_start))
        duration = timedelta(minutes=lunch_needed)
        candidates = []
        for left, right in free:
            if _minutes(left, right) >= lunch_needed:
                start = min(max(preferred, left), right - duration)
                candidates.append((abs((start - preferred).total_seconds()), start))
        if candidates:
            _, start = min(candidates)
            reserve(start, start + duration, "Lunch break", "lunch")
            lunch_scheduled = lunch_needed
        else:
            suggestions.append(f"A continuous {lunch_needed}-minute lunch break does not fit. Review the break time or fixed appointments explicitly.")

    def allocate(needed, title, kind, task_id=None):
        scheduled = 0
        while needed > 0:
            minimum = min(config.min_block_minutes, needed)
            gap = next(((left, right) for left, right in free if _minutes(left, right) >= minimum), None)
            if gap is None:
                break
            left, right = gap
            duration = min(needed, _minutes(left, right))
            reserve(left, left + timedelta(minutes=duration), title, kind, task_id)
            needed -= duration
            scheduled += duration
        return scheduled

    new_patient = allocate(max(0, config.patient_minutes - existing_patient), "Western Health patient care", "patient_care")
    new_academic = allocate(max(0, config.academic_minutes - existing_academic), "Academic reading", "academic_reading")
    allocated_task_minutes = {}
    for start, end, _, _, _, _, task_id in events:
        if task_id is not None:
            allocated_task_minutes[task_id] = allocated_task_minutes.get(task_id, 0) + _minutes(start, end)
    by_id = {task.id: task for task in tasks}
    blocked_dependencies = []
    task_minutes = 0
    unscheduled_tasks = []
    for task in rank_tasks(tasks, day, priority_config):
        status = getattr(task, "status", "Inbox")
        deferred_to = getattr(task, "deferred_to", None)
        if status not in ("Ready", "Scheduled", "In Progress") and not (status == "Deferred" and deferred_to is not None and deferred_to <= day):
            continue
        dependencies = getattr(task, "dependencies", None) or []
        if any(dependency not in by_id or by_id[dependency].status != "Completed" for dependency in dependencies):
            blocked_dependencies.append(task.id)
            continue
        remaining = max(0, int(getattr(task, "estimated_minutes", 0) or 0) - allocated_task_minutes.get(task.id, 0))
        if remaining:
            scheduled = allocate(remaining, task.title, "paperwork", task.id)
            task_minutes += scheduled
            if scheduled < remaining:
                unscheduled_tasks.append((task.id, remaining - scheduled))

    unmet = {"patient_care": max(0, config.patient_minutes - existing_patient - new_patient),
             "academic_reading": max(0, config.academic_minutes - existing_academic - new_academic),
             "lunch": max(0, lunch_needed - lunch_scheduled)}
    if not availability:
        suggestions.append("No Western Health working availability exists for this date. Add or review the working window before planning.")
    if unmet["patient_care"] or unmet["academic_reading"]:
        suggestions.append(f"Targets do not fit: patient care {unmet['patient_care']} minutes unmet; academic reading {unmet['academic_reading']} minutes unmet. Consider another date, a different lunch time, or explicitly reviewing a meeting; fixed commitments have not been moved.")
    if blocked_dependencies:
        suggestions.append(f"Tasks {', '.join(map(str, blocked_dependencies))} await completion of their dependencies.")
    if unscheduled_tasks:
        suggestions.append("Paperwork remaining: " + "; ".join(f"task {task_id}: {minutes} minutes" for task_id, minutes in unscheduled_tasks) + ". Review another date or change estimates explicitly.")
    available_paperwork = sum(_minutes(left, right) for left, right in free if _minutes(left, right) >= config.min_block_minutes)
    return Plan(day, sorted(blocks, key=lambda block: (block.start, block.end)), unmet, suggestions,
                existing_minutes("paperwork") + task_minutes + available_paperwork,
                existing_patient + new_patient, existing_academic + new_academic,
                sum(_minutes(left, right) for left, right in free), config, priority_config, basis)


def planning_occurrences(session, day: date) -> list:
    """Read this day plus task allocations elsewhere; no writes."""
    from .calendar_service import get_occurrences
    occurrences = list(get_occurrences(session, day, day))
    start, end = datetime.combine(day, time.min), datetime.combine(day + timedelta(days=1), time.min)
    elsewhere = session.scalars(select(Event).where(Event.task_id.is_not(None))).all()
    occurrences.extend(event for event in elsewhere if event.end <= start or event.start >= end)
    return occurrences


def approve_plan(session, plan: Plan, approved: bool = False) -> list[Event]:
    """Save an explicitly approved, unchanged proposal atomically; caller commits.

    A fresh calendar/task/settings snapshot must match the proposed basis. New
    events use unique external IDs and an approval marker prevents replay even
    if someone later edits or deletes a planner block.
    """
    if approved is not True:
        raise ValueError("Explicit user approval is required before saving proposed time blocks.")
    if not plan.blocks:
        raise ValueError("This proposal contains no time blocks to save.")
    # Streamlit sessions may retain ORM objects while another browser session
    # edits the database. Refresh before revalidation rather than trusting the
    # identity map. Flush preserves any caller-owned pending edits for review;
    # this function never commits them.
    try:
        session.flush()
        session.expire_all()
        # Hold a SQLite write reservation while checking and saving, so another
        # browser cannot insert a meeting between validation and approval.
        # Under modern sqlite3 transaction mode, the no-row UPDATE upgrades a
        # read transaction without changing data. A stale WAL snapshot refuses
        # this upgrade, and the user must review a fresh proposal.
        connection = session.connection()
        if connection.dialect.name == "sqlite":
            if not connection.connection.driver_connection.in_transaction:
                # Also preserves caller rollback under sqlite3 legacy mode:
                # otherwise an outermost SAVEPOINT/RELEASE can commit early.
                connection.exec_driver_sql("BEGIN IMMEDIATE")
            else:
                connection.exec_driver_sql("UPDATE settings SET key = key WHERE 0")
    except OperationalError as exc:
        error_code = getattr(exc.orig, "sqlite_errorcode", 0) & 0xff
        if error_code in (5, 6):
            raise ValueError("The calendar or tasks changed, or another approval is being saved. Refresh and generate a new proposal before approval.") from exc
        raise
    marker_key = f"approved_plan:{plan.day.isoformat()}:{plan.id}"
    if session.get(Setting, marker_key) is not None:
        raise ValueError("This proposal has already been approved.")
    planner_setting, priority_setting = session.get(Setting, "scheduler"), session.get(Setting, "priority")
    current_config = PlannerConfig(**planner_setting.value) if planner_setting is not None else PlannerConfig()
    current_priority = PriorityConfig(**priority_setting.value) if priority_setting is not None else PriorityConfig()
    fresh = propose_plan(plan.day, planning_occurrences(session, plan.day), session.scalars(select(Task)).all(), current_config, current_priority)
    if fresh.basis != plan.basis or fresh.blocks != plan.blocks:
        raise ValueError("The calendar, tasks, settings or proposal changed. Generate and review a new proposal before approval.")
    created = []
    with session.begin_nested():
        session.add(Setting(key=marker_key, value={"day": plan.day.isoformat(), "plan_id": plan.id}))
        for index, block in enumerate(plan.blocks):
            event = Event(title=block.title, start=block.start, end=block.end, source="Planner",
                          external_id=f"plan:{plan.day.isoformat()}:{plan.id}:{index}",
                          status="Confirmed", kind=block.kind, task_id=block.task_id,
                          description="Explicitly approved daily plan; synthetic/de-identified prototype.")
            session.add(event)
            created.append(event)
            if block.task_id is not None:
                task = session.get(Task, block.task_id)
                task.status = "Scheduled"
        from .security import audit
        audit(session, "approve_plan", entity=f"day:{plan.day.isoformat()}",
              detail=f"Approved proposal {plan.id}: {len(created)} blocks.")
        session.flush()
    return created
