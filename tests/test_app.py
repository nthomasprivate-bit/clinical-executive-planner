"""Exercise actual Streamlit pages and the approval boundary with AppTest."""
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from streamlit.testing.v1 import AppTest

from clinical_planner.db import get_engine
from clinical_planner.models import Event, Task


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("PLANNER_DB_URL", f"sqlite:///{tmp_path / 'ui-test.db'}")
    monkeypatch.setenv("PLANNER_DEPLOYMENT", "local-demo")
    monkeypatch.delenv("PLANNER_AUTH_PASSWORD_HASH", raising=False)
    get_engine.cache_clear()
    application = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=30).run()
    assert not application.exception
    yield application
    get_engine().dispose()
    get_engine.cache_clear()


def _button(app, label):
    return next(button for button in app.button if button.label == label)


def test_dashboard_and_all_workspaces_render(app):
    assert app.title[0].value == "Dashboard"
    assert len(app.metric) == 4
    for page in ("Calendar", "Tasks", "Import & review", "Settings & safety"):
        app.sidebar.radio[0].set_value(page).run()
        assert not app.exception, page
        assert app.title[0].value == page
    app.sidebar.radio[0].set_value("Calendar").run()
    calendar_view = next(radio for radio in app.radio if radio.label == "Calendar view")
    calendar_view.set_value("Month").run()
    assert not app.exception
    calendar_view = next(radio for radio in app.radio if radio.label == "Calendar view")
    calendar_view.set_value("Daily agenda").run()
    assert not app.exception


def test_proposed_blocks_require_explicit_approval(app):
    app.sidebar.date_input[0].set_value(date(2026, 10, 19)).run()
    engine = get_engine()
    with engine.connect() as connection:
        before = connection.scalar(select(func.count()).select_from(Event))
    _button(app, "Generate proposed daily plan").click().run()
    assert not app.exception
    assert _button(app, "Approve and save time blocks").disabled
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(Event)) == before
    next(checkbox for checkbox in app.checkbox if checkbox.label == "I have reviewed and approve these proposed time blocks.").check().run()
    assert not _button(app, "Approve and save time blocks").disabled
    _button(app, "Approve and save time blocks").click().run()
    assert not app.exception
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(Event)) > before


def test_task_register_includes_completed_tasks(app):
    app.sidebar.radio[0].set_value("Tasks").run()
    statuses = next(widget for widget in app.multiselect if widget.label == "Statuses")
    statuses.set_value(["Completed"]).run()
    assert not app.exception
    frame = app.dataframe[0].value
    assert not frame.empty
    assert set(frame["Status"]) == {"Completed"}


def test_manual_task_and_calendar_event_are_saved(app):
    app.sidebar.radio[0].set_value("Tasks").run()
    next(widget for widget in app.text_input if widget.label == "Task title").set_value("Synthetic UI task")
    next(widget for widget in app.selectbox if widget.label == "Task status").set_value("Ready")
    _button(app, "Save task").click().run()
    assert not app.exception
    with get_engine().connect() as connection:
        assert connection.scalar(select(Task.title).where(Task.title == "Synthetic UI task"))
    app.sidebar.radio[0].set_value("Calendar").run()
    next(widget for widget in app.text_input if widget.label == "Event title").set_value("Synthetic UI meeting")
    _button(app, "Save event").click().run()
    assert not app.exception
    with get_engine().connect() as connection:
        assert connection.scalar(select(Event.title).where(Event.title == "Synthetic UI meeting"))


def test_database_save_error_is_reported_without_database_details(app, monkeypatch):
    import clinical_planner.ui as ui

    def unavailable(*args, **kwargs):
        raise SQLAlchemyError("Private database error details must not appear")

    app.sidebar.radio[0].set_value("Tasks").run()
    monkeypatch.setattr(ui, "save_task", unavailable)
    next(widget for widget in app.text_input if widget.label == "Task title").set_value("Synthetic unsaved task")
    _button(app, "Save task").click().run()
    assert not app.exception
    assert any("database could not save" in error.value for error in app.error)
    assert all("Private database error" not in error.value for error in app.error)
    with get_engine().connect() as connection:
        assert connection.scalar(select(Task.title).where(Task.title == "Synthetic unsaved task")) is None
