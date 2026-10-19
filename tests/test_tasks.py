from datetime import timedelta
import pytest
from clinical_planner.db import init_db, make_engine, session_scope
from clinical_planner.models import utcnow
from clinical_planner.tasks import save_task
from clinical_planner.timeutils import today
from clinical_planner.settings import get_settings, save_settings

@pytest.fixture
def session(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'tasks.db'}")
    init_db(engine)
    with session_scope(engine) as session:
        yield session
    engine.dispose()

def test_dependency_cycles_self_and_missing_rejected(session):
    first = save_task(session,title="First")
    second = save_task(session,title="Second",dependencies=[first.id])
    with pytest.raises(ValueError,match="cycle"):
        save_task(session,task_id=first.id,dependencies=[second.id])
    with pytest.raises(ValueError,match="itself"):
        save_task(session,task_id=first.id,dependencies=[first.id])
    with pytest.raises(ValueError,match="existing"):
        save_task(session,title="Missing",dependencies=[99999])
    assert first.dependencies == []

@pytest.mark.parametrize("changes", [{"title":" "},{"title":None},{"status":"unknown"},{"category":"unknown"},{"estimated_minutes":0},{"estimated_minutes":1.5},{"importance":21},{"safety_critical":"yes"},{"dependencies":None},{"dependencies":[True]},{"due_date":"2026-10-15"}])
def test_invalid_fields_rejected_before_mutation(session,changes):
    with pytest.raises(ValueError):
        save_task(session,**({"title":"Example"}|changes))

def test_complete_reopen_and_defer(session):
    task = save_task(session,title="Synthetic",status="Completed")
    assert abs((utcnow() - task.completed_at).total_seconds()) < 2
    save_task(session,task_id=task.id,status="Ready")
    assert task.completed_at is None
    save_task(session,task_id=task.id,status="Deferred")
    assert task.deferred_to == today() + timedelta(days=1)
    save_task(session,task_id=task.id,status="Ready")
    assert task.deferred_to is None

def test_settings_validation_and_copy(session):
    settings = get_settings(session,"scheduler")
    settings["patient_minutes"] = 999
    assert get_settings(session,"scheduler")["patient_minutes"] == 120
    with pytest.raises(ValueError):
        save_settings(session,"scheduler",{"lunch_minutes":-1})
    save_settings(session,"scheduler",{"patient_minutes":100})
    assert get_settings(session,"scheduler")["patient_minutes"] == 100
