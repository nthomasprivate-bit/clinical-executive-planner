import os
import sqlite3

import pytest

from clinical_planner.backup import backup_database, verify_backup


def test_online_backup_has_committed_wal_data(tmp_path):
    database = tmp_path / "planner.db"
    with sqlite3.connect(database) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, value TEXT)")
        writer.execute("INSERT INTO sample(value) VALUES ('synthetic committed record')")
        writer.commit()
        writer.execute("INSERT INTO sample(value) VALUES ('uncommitted record')")
        destination = backup_database(database, tmp_path / "backup.db")
        with sqlite3.connect(destination) as backup:
            assert backup.execute("SELECT value FROM sample").fetchall() == [("synthetic committed record",)]
        writer.rollback()
    verify_backup(destination)
    if os.name == "posix":
        assert destination.stat().st_mode & 0o777 == 0o600


def test_backup_never_overwrites_source_or_destination(tmp_path):
    source = tmp_path / "source.db"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE sample (value TEXT)")
    with pytest.raises(ValueError, match="differ"):
        backup_database(source, source)
    output = tmp_path / "backup.db"
    output.write_text("keep me", encoding="utf-8")
    with pytest.raises(FileExistsError):
        backup_database(source, output)
    assert output.read_text() == "keep me"


def test_bad_or_missing_source_is_rejected(tmp_path):
    with pytest.raises(FileNotFoundError):
        backup_database(tmp_path / "missing.db", tmp_path / "output.db")
    source = tmp_path / "invalid.db"
    source.write_text("not a database", encoding="utf-8")
    with pytest.raises(sqlite3.DatabaseError):
        backup_database(source, tmp_path / "output.db")
    assert not (tmp_path / "output.db").exists()
    with pytest.raises(sqlite3.DatabaseError):
        verify_backup(source)
