from datetime import date, time
import pytest
from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import IntegrityError
from clinical_planner.db import init_db, make_engine, session_scope
from clinical_planner.models import Event, RecurringCommitment, Setting, Task
from clinical_planner.seed import seed_data
from clinical_planner.calendar_service import detect_conflicts, get_occurrences

@pytest.fixture
def engine(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'planner.db'}")
    init_db(engine)
    yield engine
    engine.dispose()

def test_migrations_repeat_and_are_checksum_verified(engine):
    init_db(engine)
    assert set(inspect(engine).get_table_names()) == {"tasks", "events", "recurring_commitments", "settings", "audit_log", "schema_migrations"}
    with engine.begin() as connection:
        assert connection.scalar(text("SELECT COUNT(*) FROM schema_migrations")) == 2
        connection.execute(text("UPDATE schema_migrations SET checksum='changed' WHERE version='001_initial.sql'"))
    with pytest.raises(RuntimeError, match="has changed"):
        init_db(engine)

def test_seed_is_idempotent_and_recurrences_separate(engine):
    with session_scope(engine) as session:
        seed_data(session)
        counts = tuple(session.scalar(select(func.count()).select_from(model)) for model in (Task, Event, RecurringCommitment))
        seed_data(session)
        assert tuple(session.scalar(select(func.count()).select_from(model)) for model in (Task, Event, RecurringCommitment)) == counts
        assert counts == (12, 3, 36)
        assert session.get(Setting, "seed_v2")

def test_exact_weekly_schedule_and_no_conflicts(engine):
    with session_scope(engine) as session:
        seed_data(session)
        for weekday in range(4):
            commitments = session.scalars(select(RecurringCommitment).where(RecurringCommitment.weekday == weekday).order_by(RecurringCommitment.start_time)).all()
            assert [(row.start_time, row.end_time) for row in commitments] == [(time(8),time(8,45)),(time(8,45),time(8,52)),(time(8,52),time(9)),(time(9),time(15,30)),(time(15,30),time(15,45)),(time(15,45),time(16,20)),(time(16,20),time(16,30)),(time(16,30),time(21,30))]
        friday = session.scalars(select(RecurringCommitment).where(RecurringCommitment.weekday == 4).order_by(RecurringCommitment.start_time)).all()
        assert len(friday) == 4
        assert friday[0].status == "Tentative"
        assert (friday[-1].start_time, friday[-1].end_time) == (time(16),time(20))
        assert not any(row.source == "Keilor East" for row in friday)
        assert not detect_conflicts(get_occurrences(session,date(2026,10,19),date(2026,10,25)))

def test_session_scope_rolls_back_on_failure(engine):
    with pytest.raises(ValueError):
        with session_scope(engine) as session:
            session.add(Setting(key="rollback", value={}))
            session.flush()
            raise ValueError("cancel")
    with session_scope(engine) as session:
        assert session.get(Setting,"rollback") is None

def test_database_constraints_and_foreign_keys_enforced(engine):
    with engine.connect() as connection:
        assert connection.scalar(text("PRAGMA foreign_keys")) == 1
    with pytest.raises(IntegrityError):
        with session_scope(engine) as session:
            session.add(RecurringCommitment(title="Invalid",weekday=7,start_time=time(9),end_time=time(10),source="Manual",status="Confirmed",kind="meeting"))
