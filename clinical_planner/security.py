"""Local single-account authentication and minimal, content-free audit records.

This is a prototype deployment gate. Production also needs TLS, perimeter rate
limiting, managed secrets, access controls and a deployment-specific review.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import getpass
import hashlib
import hmac
import os
import secrets
import time
from contextvars import ContextVar

from sqlalchemy.orm import Session

from clinical_planner.models import AuditLog

PBKDF2_ITERATIONS = 600_000
MIN_ITERATIONS = 100_000
MAX_ITERATIONS = 2_000_000
AUTH_PREFIX = "_planner_auth_"
_audit_actor: ContextVar[str] = ContextVar("planner_audit_actor", default="local-demo")


def hash_password(password: str, *, iterations: int = PBKDF2_ITERATIONS) -> str:
    """Create a salted PBKDF2-SHA256 hash; never persist a plaintext password."""
    if not 12 <= len(password) <= 4096:
        raise ValueError("Use a password with 12–4096 characters.")
    if not MIN_ITERATIONS <= iterations <= MAX_ITERATIONS:
        raise ValueError("PBKDF2 iterations are outside the supported range.")
    salt = secrets.token_bytes(32)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return "$".join(("pbkdf2_sha256", str(iterations),
                     base64.b64encode(salt).decode(), base64.b64encode(digest).decode()))


def _decode_hash(encoded: str) -> tuple[int, bytes, bytes]:
    algorithm, raw_iterations, raw_salt, raw_digest = encoded.split("$")
    iterations = int(raw_iterations)
    if algorithm != "pbkdf2_sha256" or not MIN_ITERATIONS <= iterations <= MAX_ITERATIONS:
        raise ValueError("Unsupported password hash.")
    salt = base64.b64decode(raw_salt, validate=True)
    digest = base64.b64decode(raw_digest, validate=True)
    if len(salt) < 16 or len(salt) > 64 or len(digest) != 32:
        raise ValueError("Invalid password hash.")
    return iterations, salt, digest


def verify_password(password: str, encoded: str) -> bool:
    """Use a constant-time comparison and reject malformed configuration."""
    try:
        iterations, salt, expected = _decode_hash(encoded)
    except (ValueError, TypeError, binascii.Error, AttributeError):
        return False
    if not isinstance(password, str) or len(password) > 4096:
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return hmac.compare_digest(actual, expected)


def audit(session: Session, action: str, entity: str = "", detail: str = "",
          actor: str | None = None) -> None:
    """Append an audit record to the caller's transaction.

    Use identifiers and short operation summaries only. Never pass task titles,
    clinical text, credentials, uploaded contents or correspondence into detail.
    """
    actor = _audit_actor.get() if actor is None else actor
    session.add(AuditLog(actor=str(actor)[:100], action=str(action)[:100],
                         entity=str(entity)[:100], detail=str(detail)[:500]))


def _log_access(engine, action: str, actor: str) -> None:
    if engine is not None:
        with Session(engine) as session, session.begin():
            audit(session, action, actor=actor)


def _clear_auth(state) -> None:
    for key in list(state):
        if key.startswith(AUTH_PREFIX) and key not in {
            AUTH_PREFIX + "failures", AUTH_PREFIX + "locked_until"
        }:
            del state[key]


def logout(engine=None) -> None:
    """Clear the current browser session and record logout when a DB is supplied."""
    import streamlit as st

    actor = st.session_state.get(AUTH_PREFIX + "actor", "local-demo")
    _log_access(engine, "logout", actor)
    _clear_auth(st.session_state)
    _audit_actor.set("anonymous")
    st.rerun()


def require_auth(engine=None) -> str:
    """Gate Streamlit content before reading or displaying application records.

    Local-demo mode is permitted without a password solely for synthetic data.
    Production mode requires a valid hash and an engine for access logging.
    """
    import streamlit as st

    _audit_actor.set("anonymous")
    production = os.environ.get("PLANNER_DEPLOYMENT", "local-demo").lower() == "production"
    encoded = os.environ.get("PLANNER_AUTH_PASSWORD_HASH", "").strip()
    if production and (not encoded or engine is None):
        st.error("Production access is locked. Configure a password hash and access logging.")
        st.stop()
    if encoded:
        try:
            _decode_hash(encoded)
        except (ValueError, TypeError, binascii.Error):
            st.error("Authentication configuration is invalid. Access is locked.")
            st.stop()
    if not encoded:
        _audit_actor.set("local-demo")
        if not st.session_state.get(AUTH_PREFIX + "access_logged"):
            _log_access(engine, "app_access", "local-demo")
            st.session_state[AUTH_PREFIX + "access_logged"] = True
        return "local-demo"

    state = st.session_state
    current = time.monotonic()
    fingerprint = hashlib.sha256(encoded.encode()).hexdigest()
    last_active = state.get(AUTH_PREFIX + "last_active", 0)
    if state.get(AUTH_PREFIX + "authenticated"):
        if current - last_active > 1800 or state.get(AUTH_PREFIX + "fingerprint") != fingerprint:
            _log_access(engine, "session_expired", "planner-user")
            _clear_auth(state)
            st.warning("Your session expired. Sign in again.")
        else:
            state[AUTH_PREFIX + "last_active"] = current
            _audit_actor.set("planner-user")
            return "planner-user"

    st.title("Clinical Executive Planner V2.0")
    st.caption("Private planner · sign in to continue")
    if current < state.get(AUTH_PREFIX + "locked_until", 0):
        st.error("Sign-in temporarily unavailable. Please try again shortly.")
        st.stop()
    with st.form("planner_login", clear_on_submit=True):
        password = st.text_input("Password", type="password", max_chars=4096)
        submitted = st.form_submit_button("Sign in")
    if submitted:
        if verify_password(password, encoded):
            _log_access(engine, "login_success", "planner-user")
            _log_access(engine, "app_access", "planner-user")
            state[AUTH_PREFIX + "authenticated"] = True
            state[AUTH_PREFIX + "actor"] = "planner-user"
            state[AUTH_PREFIX + "fingerprint"] = fingerprint
            state[AUTH_PREFIX + "last_active"] = current
            state[AUTH_PREFIX + "failures"] = 0
            state[AUTH_PREFIX + "locked_until"] = 0
            _audit_actor.set("planner-user")
            st.rerun()
        else:
            failures = state.get(AUTH_PREFIX + "failures", 0) + 1
            state[AUTH_PREFIX + "failures"] = failures
            if failures >= 5:
                state[AUTH_PREFIX + "locked_until"] = current + min(300, 60 * (failures - 4))
            _log_access(engine, "login_failed", "anonymous")
            st.error("Sign-in unsuccessful.")
    st.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Planner credential helper (never writes secrets).")
    parser.add_argument("command", choices=["hash-password"])
    parser.parse_args()
    password = getpass.getpass("New planner password (at least 12 characters): ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        parser.error("Passwords do not match.")
    try:
        print(hash_password(password))
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
