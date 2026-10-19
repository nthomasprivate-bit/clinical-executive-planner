from datetime import date, datetime, time

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from clinical_planner.calendar_service import (
    Occurrence, delete_event, detect_conflicts, get_occurrences, save_event, save_recurring, source_color,
)
from clinical_planner.models import AuditLog, Base, Event


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def event(session, **overrides):
    values = dict(title="Synthetic meeting", start=datetime(2026, 10, 19, 10), end=datetime(2026, 10, 19, 11), source="Western Health")
    return save_event(session, **(values | overrides))


def recurrence(session, **overrides):
    values = dict(title="Private practice", weekday=0, start_time=time(16, 30), end_time=time(21, 30), source="Keilor East")
    return save_recurring(session, **(values | overrides))


def test_weekly_recurrences_expand_without_persisting_duplicates(session):
    row = recurrence(session)
    event(session)
    first = get_occurrences(session, date(2026, 10, 19), date(2026, 10, 26))
    second = get_occurrences(session, date(2026, 10, 19), date(2026, 10, 26))
    assert first == second
    assert [item.start.date() for item in first if item.recurring_id == row.id] == [date(2026, 10, 19), date(2026, 10, 26)]
    assert len(list(session.scalars(select(Event)))) == 1


def test_recurrence_respects_effective_dates_inactive_and_tentative(session):
    recurrence(session, status="Tentative", effective_from=date(2026, 10, 20), effective_until=date(2026, 10, 26))
    recurrence(session, title="Inactive", active=False)
    occurrences = get_occurrences(session, date(2026, 10, 19), date(2026, 11, 2))
    assert len(occurrences) == 1
    assert occurrences[0].start.date() == date(2026, 10, 26)
    assert occurrences[0].status == "Tentative"


def test_date_range_includes_events_crossing_midnight_and_excludes_next_day(session):
    event(session, start=datetime(2026, 10, 18, 23), end=datetime(2026, 10, 19, 1))
    event(session, start=datetime(2026, 10, 20), end=datetime(2026, 10, 20, 1))
    items = get_occurrences(session, date(2026, 10, 19), date(2026, 10, 19))
    assert len(items) == 1
    assert items[0].start.date() == date(2026, 10, 18)


def occurrence(id, start, end, kind="meeting", status="Confirmed"):
    return Occurrence(id, id, datetime(2026, 10, 19, start), datetime(2026, 10, 19, end), "Western Health", status, kind)


def test_conflicts_are_half_open_exclude_availability_and_include_tentative():
    envelope = occurrence("window", 9, 16, "availability")
    first = occurrence("first", 10, 11)
    adjacent = occurrence("adjacent", 11, 12)
    tentative = occurrence("tentative", 10, 12, status="Tentative")
    pairs = detect_conflicts([adjacent, envelope, tentative, first])
    assert {frozenset((a.id, b.id)) for a, b in pairs} == {frozenset(("first", "tentative")), frozenset(("adjacent", "tentative"))}


def test_manual_edit_and_delete_are_flush_only_and_keep_identifier(session):
    row = event(session)
    saved_id = row.id
    assert save_event(session, event_id=saved_id, title="Updated") is row
    assert row.title == "Updated"
    delete_event(session, saved_id)
    assert session.get(Event, saved_id) is None
    assert session.in_transaction()


def test_invalid_partial_update_does_not_change_event(session):
    row = event(session)
    with pytest.raises(ValueError, match="end must be after"):
        save_event(session, event_id=row.id, title="Must not be saved", end=datetime(2026, 10, 19, 9))
    assert row.title == "Synthetic meeting"


@pytest.mark.parametrize("overrides", [
    {"title": " "}, {"source": ""}, {"kind": "made_up"}, {"status": "Maybe"},
    {"start": date(2026, 10, 19)}, {"end": datetime(2026, 10, 19, 9)}, {"task_id": 999},
    {"start": datetime(2026, 10, 4, 2, 30), "end": datetime(2026, 10, 4, 4)},
])
def test_invalid_events_rejected_before_database_changes(session, overrides):
    with pytest.raises(ValueError):
        event(session, **overrides)
    assert list(session.scalars(select(Event))) == []


@pytest.mark.parametrize("overrides", [{"weekday": 7}, {"weekday": True}, {"end_time": time(8)}, {"effective_from": date(2026, 11, 1), "effective_until": date(2026, 10, 1)}])
def test_invalid_recurrence_rejected(session, overrides):
    with pytest.raises(ValueError):
        recurrence(session, **overrides)


def test_duplicate_import_identity_not_allowed_via_manual_editor(session):
    event(session, external_id="example")
    with pytest.raises(ValueError, match="already exist"):
        event(session, external_id="example")


def test_explicit_offsets_are_normalized_to_melbourne(session):
    from datetime import timezone
    row = event(session, start=datetime(2026, 10, 19, 0, tzinfo=timezone.utc), end=datetime(2026, 10, 19, 1, tzinfo=timezone.utc))
    assert row.start == datetime(2026, 10, 19, 11)
    assert row.end == datetime(2026, 10, 19, 12)


@pytest.mark.parametrize("start", ["2026-04-05T02:30:00+11:00", "2026-04-05T02:30:00+10:00", "2026-04-05T02:30:00"])
def test_repeated_hour_is_rejected_even_with_explicit_offset(session, start):
    with pytest.raises(ValueError, match="Repeated-hour times are currently unsupported"):
        event(session, start=datetime.fromisoformat(start), end=datetime(2026, 4, 5, 3, 30))
    assert list(session.scalars(select(Event))) == []


def test_source_colours_are_stable():
    assert source_color("Western Health") != source_color("Keilor East")
    assert source_color("Unknown") == "#64748B"


def test_calendar_mutations_append_identifier_only_audit_logs(session):
    row = event(session, title="Sensitive synthetic content")
    save_event(session, event_id=row.id, title="Changed synthetic content")
    recurring = recurrence(session)
    save_recurring(session, recurring_id=recurring.id, active=False)
    delete_event(session, row.id)
    logs = list(session.scalars(select(AuditLog).order_by(AuditLog.id)))
    assert [log.action for log in logs] == ["event.created", "event.updated", "recurring.created", "recurring.updated", "event.deleted"]
    assert all("synthetic" not in log.detail.casefold() and "synthetic" not in log.entity.casefold() for log in logs)
    assert all(log.entity.startswith(("event:", "recurring:")) for log in logs)
