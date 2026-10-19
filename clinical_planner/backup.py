"""Consistent SQLite online backups with integrity checks and private permissions."""
from __future__ import annotations

import argparse
import os
import sqlite3
from pathlib import Path
from urllib.parse import quote


def _read_only_uri(path: Path) -> str:
    return "file:" + quote(str(path.resolve()), safe="/") + "?mode=ro"


def verify_backup(path: str | Path) -> None:
    """Raise if a backup is absent or SQLite integrity_check fails."""
    database = Path(path).expanduser().resolve()
    if not database.is_file():
        raise FileNotFoundError(database)
    with sqlite3.connect(_read_only_uri(database), uri=True) as connection:
        result = connection.execute("PRAGMA integrity_check").fetchall()
    if result != [("ok",)]:
        raise ValueError("SQLite integrity check failed.")


def backup_database(database: str | Path, output: str | Path) -> Path:
    """Back up a live SQLite database; never overwrite an existing destination."""
    source = Path(database).expanduser().resolve()
    destination = Path(output).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if source == destination:
        raise ValueError("Backup destination must differ from the database.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    try:
        with sqlite3.connect(_read_only_uri(source), uri=True) as reader:
            with sqlite3.connect(destination) as writer:
                reader.backup(writer)
        verify_backup(destination)
        os.chmod(destination, 0o600)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description="Create or verify a private SQLite backup.")
    parser.add_argument("--database", help="Existing source SQLite database")
    parser.add_argument("--output", help="New backup file (must not exist)")
    parser.add_argument("--verify", help="Verify an existing backup without changing it")
    args = parser.parse_args()
    try:
        if args.verify:
            if args.database or args.output:
                parser.error("Use --verify alone.")
            verify_backup(args.verify)
            print("SQLite integrity check passed.")
        else:
            if not args.database or not args.output:
                parser.error("Supply --database and --output.")
            backup_database(args.database, args.output)
            print("Backup created and integrity check passed.")
    except (OSError, sqlite3.DatabaseError, ValueError) as exc:
        parser.exit(1, f"Backup failed: {exc}\n")


if __name__ == "__main__":
    main()
