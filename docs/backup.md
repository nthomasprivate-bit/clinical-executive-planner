# Backup and restore runbook

Backups contain the same information as the live database. Protect them under the same access and encryption controls. No backup scheduling, remote upload or automatic deletion is enabled by the prototype.

## Backup

For routine local use, back up at the end of each working day and before a migration or larger import. Choose an explicit filename that does not already exist:

```bash
python -m clinical_planner.backup --database data/planner.db --output backups/planner-2026-10-15.db
python -m clinical_planner.backup --verify backups/planner-2026-10-15.db
```

The app can remain running during backup: the SQLite online backup API takes a consistent committed snapshot including WAL contents. A new destination is created exclusively, checked with `PRAGMA integrity_check`, and restricted to mode `0600` where the OS supports it. An unsuccessful new backup is removed. Existing backup files are never overwritten by this helper. Do not copy only the main live `.db` file while WAL mode is active.

Use a private backup directory, such as one restricted to mode `0700` on Unix. Keep an encrypted off-device copy if required by the approved operating model. Suggested local retention is seven daily and four weekly copies; the data owner must choose actual retention and disposal requirements. Check a backup immediately after creation and perform a test restore at least monthly. Scheduling and retention are operator tasks until automation is separately implemented and tested.

## Non-destructive restore procedure

1. Stop Streamlit and any other process using the database. Confirm the application is stopped before touching the active SQLite file or its `-wal` and `-shm` files.
2. Verify the chosen backup using the command above. Record the backup date and the last known good application version.
3. Preserve the current database, any `-wal` / `-shm` files, and application version in a separate private recovery directory. Do not overwrite or delete this evidence. Do not mix sidecar files from the current database with the restored copy.
4. Copy the verified backup to a **new** private path, for example `data/restored-planner.db`, and restrict file permissions. Keep the source backup unchanged.
5. Set `PLANNER_DB_URL` to the new file (`sqlite:////absolute/path/data/restored-planner.db`). Start the matching application version. If upgrading, run and review migrations on this new copy; never alter the original backup.
6. Check the calendar, recurring commitments, task count, recent completed tasks, audit entries and application settings. Confirm that recovery reflects the expected backup time. Existing tests use synthetic data and do not certify the content of an individual backup.
7. Resume work only after those checks. Retain the original recovery files according to the chosen retention policy. Document recovery time and any work lost since the snapshot.

The backup CLI intentionally has no destructive restore command. A restore exercise should always use a different database path so the running data remains recoverable.
