"""A proposed plan is read-only until explicitly approved against current data."""
from dataclasses import asdict, replace
from datetime import date, datetime, time, timedelta

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from clinical_planner.calendar_service import save_event, save_recurring
from clinical_planner.db import make_engine
from clinical_planner.models import AuditLog, Base, Event, Setting, Task
from clinical_planner.scheduler import PlannerConfig, approve_plan, planning_occurrences, propose_plan


DAY = date(2026, 10, 19)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        save_recurring(db, title="Western Health working window", weekday=DAY.weekday(),
                       start_time=time(9), end_time=time(15, 30),
                       source="Western Health", kind="availability")
        db.add(Task(title="Synthetic governance paperwork", created_date=DAY,
                    category="Governance", source="Synthetic", status="Ready",
                    estimated_minutes=30))
        db.commit()
        db.info["initial_audit_count"] = db.scalar(select(func.count()).select_from(AuditLog))
        yield db
    engine.dispose()


def proposal(session):
    return propose_plan(DAY, planning_occurrences(session, DAY),
                        session.scalars(select(Task)).all())


def count(session, model):
    return session.scalar(select(func.count()).select_from(model))


def test_proposal_and_missing_approval_do_not_save_blocks(session):
    plan = proposal(session)
    assert plan.blocks
    assert count(session, Event) == 0
    assert count(session, AuditLog) == session.info["initial_audit_count"]
    with pytest.raises(ValueError, match="approval"):
        approve_plan(session, plan)
    assert count(session, Event) == 0
    assert count(session, Setting) == 0
    assert session.scalar(select(Task)).status == "Ready"


def test_explicit_approval_saves_blocks_updates_tasks_and_audits(session):
    plan = proposal(session)
    saved = approve_plan(session, plan, approved=True)
    session.commit()
    assert len(saved) == len(plan.blocks)
    assert count(session, Event) == len(plan.blocks)
    assert all(event.source == "Planner" and event.status == "Confirmed" for event in saved)
    assert session.scalar(select(Task)).status == "Scheduled"
    assert session.scalar(select(AuditLog).where(AuditLog.action == "approve_plan")) is not None
    assert session.get(Setting, f"approved_plan:{DAY.isoformat()}:{plan.id}") is not None


def test_caller_rollback_reverts_approved_blocks_marker_status_and_audit(session):
    plan = proposal(session)
    approve_plan(session, plan, approved=True)
    session.rollback()
    assert count(session, Event) == 0
    assert count(session, Setting) == 0
    assert count(session, AuditLog) == session.info["initial_audit_count"]
    assert session.scalar(select(Task)).status == "Ready"


def test_approval_marker_blocks_replay_even_after_calendar_blocks_deleted(session):
    plan = proposal(session)
    saved = approve_plan(session, plan, approved=True)
    session.commit()
    for event in saved:
        session.delete(event)
    session.commit()
    with pytest.raises(ValueError, match="already been approved"):
        approve_plan(session, plan, approved=True)
    assert count(session, Event) == 0
    assert count(session, AuditLog) == session.info["initial_audit_count"] + 1


def test_added_calendar_appointment_invalidates_previously_reviewed_plan(session):
    plan = proposal(session)
    save_event(session, title="Synthetic fixed meeting",
               start=datetime.combine(DAY, time(9)), end=datetime.combine(DAY, time(10)))
    with pytest.raises(ValueError, match="changed"):
        approve_plan(session, plan, approved=True)
    assert count(session, Event) == 1
    assert count(session, Setting) == 0


def test_task_change_invalidates_previously_reviewed_plan(session):
    plan = proposal(session)
    item = session.scalar(select(Task))
    item.status = "Waiting"
    session.flush()
    with pytest.raises(ValueError, match="changed"):
        approve_plan(session, plan, approved=True)
    assert count(session, Event) == 0
    assert count(session, Setting) == 0


def test_task_changed_in_another_session_invalidates_cached_proposal(session):
    cached_task = session.scalar(select(Task))
    plan = proposal(session)
    with Session(session.get_bind()) as other:
        current = other.get(Task, cached_task.id)
        current.status = "Waiting"
        other.commit()
    # This session still holds the old object until approval refreshes its snapshot.
    assert cached_task.status == "Ready"
    with pytest.raises(ValueError, match="changed"):
        approve_plan(session, plan, approved=True)
    assert count(session, Event) == 0


def test_new_task_invalidates_previously_reviewed_plan(session):
    plan = proposal(session)
    session.add(Task(title="Synthetic urgent obligation", created_date=DAY,
                     status="Ready", estimated_minutes=15, safety_critical=True))
    session.flush()
    with pytest.raises(ValueError, match="changed"):
        approve_plan(session, plan, approved=True)
    assert count(session, Event) == 0


def test_settings_change_invalidates_previously_reviewed_plan(session):
    plan = proposal(session)
    values = asdict(plan.config)
    values["lunch_minutes"] = 45
    session.add(Setting(key="scheduler", value=values))
    session.flush()
    with pytest.raises(ValueError, match="changed"):
        approve_plan(session, plan, approved=True)
    assert count(session, Event) == 0
    assert count(session, AuditLog) == session.info["initial_audit_count"]


def test_modified_proposal_block_is_rejected(session):
    plan = proposal(session)
    original = plan.blocks[0]
    plan.blocks[0] = replace(original, end=original.end + timedelta(minutes=1))
    with pytest.raises(ValueError, match="changed"):
        approve_plan(session, plan, approved=True)
    assert count(session, Event) == 0
    assert count(session, Setting) == 0


def test_regenerating_after_approval_does_not_grow_transition_buffers(session):
    plan = proposal(session)
    approve_plan(session, plan, approved=True)
    session.commit()
    follow_up = proposal(session)
    assert not follow_up.blocks
    assert not follow_up.unmet_minutes["patient_care"]
    assert not follow_up.unmet_minutes["academic_reading"]


def test_custom_proposal_rules_must_match_persisted_settings(session):
    plan = propose_plan(DAY, planning_occurrences(session, DAY),
                        session.scalars(select(Task)).all(),
                        PlannerConfig(lunch_minutes=45))
    with pytest.raises(ValueError, match="changed"):
        approve_plan(session, plan, approved=True)
    assert count(session, Event) == 0


def test_removing_custom_settings_invalidates_custom_proposal(session):
    config = PlannerConfig(lunch_minutes=45)
    session.add(Setting(key="scheduler", value=asdict(config)))
    session.commit()
    plan = propose_plan(DAY, planning_occurrences(session, DAY),
                        session.scalars(select(Task)).all(), config)
    session.delete(session.get(Setting, "scheduler"))
    session.commit()
    with pytest.raises(ValueError, match="changed"):
        approve_plan(session, plan, approved=True)
    assert count(session, Event) == 0


def test_production_wal_snapshot_change_requires_fresh_review(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'approval.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as first:
        save_recurring(first, title="Western Health working window", weekday=DAY.weekday(),
                       start_time=time(9), end_time=time(15, 30),
                       source="Western Health", kind="availability")
        first.add(Task(title="Synthetic paperwork", created_date=DAY,
                       status="Ready", estimated_minutes=30))
        first.commit()
        plan = proposal(first)
        with Session(engine) as other:
            item = other.scalar(select(Task))
            item.status = "Waiting"
            other.commit()
        with pytest.raises(ValueError, match="changed"):
            approve_plan(first, plan, approved=True)
        first.rollback()
        assert count(first, Event) == 0
        assert first.scalar(select(Task)).status == "Waiting"
    engine.dispose()
