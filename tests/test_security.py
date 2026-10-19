import sys
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from clinical_planner.models import AuditLog, Base
from clinical_planner import security


class Stopped(Exception):
    pass


class Rerun(Exception):
    pass


@pytest.fixture
def engine():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def streamlit(monkeypatch):
    actor_token = security._audit_actor.set("local-demo")
    def stop():
        raise Stopped

    def rerun():
        raise Rerun

    fake = SimpleNamespace(session_state={}, errors=[], warnings=[], password="", submitted=False)
    fake.stop, fake.rerun = stop, rerun
    fake.error, fake.warning = fake.errors.append, fake.warnings.append
    fake.title = fake.caption = lambda *args, **kwargs: None
    fake.form = lambda *args, **kwargs: nullcontext()
    fake.text_input = lambda *args, **kwargs: fake.password
    fake.form_submit_button = lambda *args, **kwargs: fake.submitted
    monkeypatch.setitem(sys.modules, "streamlit", fake)
    monkeypatch.delenv("PLANNER_DEPLOYMENT", raising=False)
    monkeypatch.delenv("PLANNER_AUTH_PASSWORD_HASH", raising=False)
    yield fake
    security._audit_actor.reset(actor_token)


@pytest.fixture(scope="module")
def encoded():
    return security.hash_password("a long test password", iterations=100_000)


def actions(engine):
    with Session(engine) as session:
        return list(session.scalars(select(AuditLog.action).order_by(AuditLog.id)))


def test_hash_salted_and_constant_verification(encoded):
    assert encoded != security.hash_password("a long test password", iterations=100_000)
    assert security.verify_password("a long test password", encoded)
    assert not security.verify_password("another password", encoded)
    assert not security.verify_password("anything", "pbkdf2_sha256$999999999$bad$bad")
    assert not security.verify_password("anything", "invalid")
    with pytest.raises(ValueError, match="12"):
        security.hash_password("short")


def test_audit_uses_callers_transaction(engine):
    with Session(engine) as session:
        security.audit(session, "event.saved", "event:1", actor="planner-user")
        session.rollback()
    assert actions(engine) == []
    with Session(engine) as session, session.begin():
        security.audit(session, "event.saved", "event:1", actor="planner-user")
    assert actions(engine) == ["event.saved"]


def test_local_demo_records_one_access_per_session(streamlit, engine):
    assert security.require_auth(engine) == "local-demo"
    assert security.require_auth(engine) == "local-demo"
    assert actions(engine) == ["app_access"]


def test_production_denies_missing_hash(streamlit, engine, monkeypatch):
    monkeypatch.setenv("PLANNER_DEPLOYMENT", "production")
    with pytest.raises(Stopped):
        security.require_auth(engine)
    assert actions(engine) == []
    assert "locked" in streamlit.errors[-1]


def test_malformed_auth_never_falls_back_to_demo(streamlit, engine, monkeypatch):
    monkeypatch.setenv("PLANNER_AUTH_PASSWORD_HASH", "bad-hash")
    with pytest.raises(Stopped):
        security.require_auth(engine)
    assert actions(engine) == []


def test_production_requires_access_logging(streamlit, monkeypatch, encoded):
    monkeypatch.setenv("PLANNER_DEPLOYMENT", "production")
    monkeypatch.setenv("PLANNER_AUTH_PASSWORD_HASH", encoded)
    with pytest.raises(Stopped):
        security.require_auth()


def test_successful_login_expiry_and_logout(streamlit, engine, monkeypatch, encoded):
    monkeypatch.setenv("PLANNER_AUTH_PASSWORD_HASH", encoded)
    monkeypatch.setattr(security.time, "monotonic", lambda: 1000)
    streamlit.password, streamlit.submitted = "a long test password", True
    with pytest.raises(Rerun):
        security.require_auth(engine)
    assert security.require_auth(engine) == "planner-user"
    assert actions(engine) == ["login_success", "app_access"]
    with Session(engine) as session, session.begin():
        security.audit(session, "task.updated", "task:1")
    with Session(engine) as session:
        record = session.scalar(select(AuditLog).where(AuditLog.action == "task.updated"))
        assert record.actor == "planner-user"
    monkeypatch.setattr(security.time, "monotonic", lambda: 2801)
    streamlit.submitted = False
    with pytest.raises(Stopped):
        security.require_auth(engine)
    assert actions(engine)[-1] == "session_expired"
    assert not streamlit.session_state.get(security.AUTH_PREFIX + "authenticated")
    with pytest.raises(Rerun):
        security.logout(engine)
    assert actions(engine)[-1] == "logout"


def test_context_actor_does_not_override_explicit_identity(engine):
    token = security._audit_actor.set("planner-user")
    try:
        with Session(engine) as session, session.begin():
            security.audit(session, "service.updated", "event:1")
            security.audit(session, "login_failed", actor="anonymous")
        with Session(engine) as session:
            records = list(session.scalars(select(AuditLog).order_by(AuditLog.id)))
            assert [record.actor for record in records] == ["planner-user", "anonymous"]
    finally:
        security._audit_actor.reset(token)


def test_failed_login_lockout_and_content_free_log(streamlit, engine, monkeypatch, encoded):
    monkeypatch.setenv("PLANNER_AUTH_PASSWORD_HASH", encoded)
    monkeypatch.setattr(security.time, "monotonic", lambda: 1000)
    streamlit.password, streamlit.submitted = "do not log this secret", True
    for _ in range(5):
        with pytest.raises(Stopped):
            security.require_auth(engine)
    assert actions(engine) == ["login_failed"] * 5
    streamlit.password = "a long test password"
    with pytest.raises(Stopped):
        security.require_auth(engine)
    assert "temporarily" in streamlit.errors[-1]
    with Session(engine) as session:
        assert all(record.detail == "" for record in session.scalars(select(AuditLog)))
