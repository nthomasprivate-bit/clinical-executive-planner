"""Versioned SQLite migrations and transactional SQLAlchemy sessions."""
from contextlib import contextmanager
from functools import lru_cache
import hashlib
import os
from pathlib import Path
import sqlite3
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

PROJECT_ROOT = Path(__file__).resolve().parent.parent

def make_engine(url: str | None = None) -> Engine:
    url = url or os.getenv("PLANNER_DB_URL") or f"sqlite:///{PROJECT_ROOT / 'data' / 'planner.db'}"
    if not url.startswith("sqlite:"):
        raise ValueError("This local MVP supports SQLite databases only.")
    engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 15, "autocommit": False})
    if engine.url.database and engine.url.database != ":memory:":
        database_path = Path(engine.url.database)
        database_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if database_path.parent == PROJECT_ROOT / "data":
            database_path.parent.chmod(0o700)
        # Create privately before SQLite creates WAL sidecars, whose modes are
        # inherited from the main database. Do not rely on the process umask.
        database_path.touch(mode=0o600, exist_ok=True)
        database_path.chmod(0o600)

    @event.listens_for(engine, "connect")
    def configure_sqlite(connection, _record):
        # PRAGMAs must run outside a transaction. Python 3.12's explicit
        # transaction mode then protects DDL and SAVEPOINT rollback semantics.
        connection.autocommit = True
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=15000")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.autocommit = False
    return engine

@lru_cache(maxsize=1)
def get_engine() -> Engine:
    return make_engine()

def _statements(script: str):
    buffer = ""
    for line in script.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            yield buffer
            buffer = ""
    if buffer.strip():
        raise ValueError("Incomplete migration SQL.")

def init_db(engine: Engine) -> None:
    """Apply each immutable numbered SQL migration once, verifying checksums."""
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, checksum TEXT NOT NULL, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
        applied = dict(connection.execute(text("SELECT version, checksum FROM schema_migrations")).all())
        for migration in sorted((Path(__file__).parent / "migrations").glob("*.sql")):
            script = migration.read_text(encoding="utf-8")
            checksum = hashlib.sha256(script.encode()).hexdigest()
            if migration.name in applied:
                if applied[migration.name] != checksum:
                    raise RuntimeError(f"Applied migration {migration.name} has changed; restore it and add a new migration.")
                continue
            for statement in _statements(script):
                connection.exec_driver_sql(statement)
            connection.execute(text("INSERT INTO schema_migrations(version, checksum) VALUES (:version, :checksum)"), {"version": migration.name, "checksum": checksum})
    if engine.url.database and engine.url.database != ":memory:":
        for path in (Path(engine.url.database), Path(str(engine.url.database) + "-wal"), Path(str(engine.url.database) + "-shm")):
            if path.exists():
                path.chmod(0o600)

initialize_database = init_db

@contextmanager
def session_scope(engine: Engine):
    with Session(engine, expire_on_commit=False) as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise

if __name__ == "__main__":
    from clinical_planner.seed import seed_data
    engine = get_engine()
    init_db(engine)
    with session_scope(engine) as session:
        seed_data(session)
    print("Database migrated and synthetic seed data installed.")
