# Clinical Executive Planner V2.0

A private, local Streamlit prototype for a combined clinical, professional and academic calendar. Python 3.12+, SQLite, SQLAlchemy and pytest. All supplied records are synthetic. **Do not enter identifiable patient information, clinical records or real email content.**

## Install and launch

From this directory, with Python 3.12 or later:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]' -c requirements-lock.txt
python -m clinical_planner.db
streamlit run app.py
```

Open `http://localhost:8501`. The checked-in Streamlit configuration binds to `127.0.0.1`. On Windows, activate with `.venv\Scripts\activate` and use the available Python 3.12+ executable. The first app launch also runs numbered migrations and installs idempotent synthetic seed data. Running the database command again does not duplicate recurring commitments or examples.

`requirements-lock.txt` records the dependency versions tested with Python 3.12 on Linux. The supported dependency ranges are in `pyproject.toml`; update and retest the constraints when upgrading.

Run the automated checks:

```bash
python -m pytest
```

Validated on Python 3.12: **123 tests passed**, including five Streamlit workflow acceptance tests. The seeded SQLite database and an online backup both passed integrity checks. These checks cover the local application; external integrations remain unimplemented as listed below.

The default database is `data/planner.db`. To use another SQLite file, set `PLANNER_DB_URL`, for example `sqlite:////absolute/path/planner.db`, before launching. No remote database or external system is contacted. The database contains your edits across app restarts; keep it and its backups private.

## Using the application

- **Dashboard / daily planner:** inspect confirmed appointments, patient care, reading, paperwork capacity, priority tasks, overdue work, completed and deferred tasks, and conflicts. Generate a proposal, review its time blocks and unmet targets, then explicitly approve it to save. Generating a proposal does not save events.
- **Calendar:** browse a week, month or daily agenda. Colours identify sources, statuses distinguish confirmed and tentative commitments, and conflicts flag overlapping occupied time. Edit individual events and recurring commitments with their respective editors.
- **Task register:** create and edit tasks, assess priority factors, track due dates and verification dates, and set dependencies and statuses. Priority is recalculated for the planning date and includes an explanation of every component.
- **Imports:** upload a synthetic screenshot for visual reference and manual transcription, or review a CSV preview before importing. No image is sent to an extraction service. A stable source and external ID identify each imported event.
- **Settings:** adjust daily targets, lunch, transition allowance and priority rules. Changes affect future proposals; review existing saved events separately.

All scheduling uses **Australia/Melbourne**, including daylight saving. Naive event times represent Melbourne local wall time; CSV timestamps with an explicit UTC offset are converted. Nonexistent local times at daylight-saving transitions are rejected. Repeated-hour Melbourne times are currently unsupported and rejected even when an explicit UTC offset is supplied, because the local wall-time database cannot distinguish the two occurrences. Audit timestamps are UTC.

## Seeded weekly commitments

Recurring commitments live in their own table, separately from dated imported events.

| Days | Time | Commitment |
| --- | --- | --- |
| Mon–Thu | 08:00–08:45 | Sunshine Private Hospital inpatient consultations |
| Mon–Thu | 08:45–08:52 | Walk to Sunshine Public Hospital |
| Mon–Thu | 08:52–09:00 | Preparation |
| Mon–Thu | 09:00–15:30 | Western Health working window |
| Mon–Thu | 15:30–15:45 | Transfer and preparation at Sunshine Private |
| Mon–Thu | 15:45–16:20 | Sunshine Private inpatient consultations |
| Mon–Thu | 16:20–16:30 | Travel to Keilor East |
| Mon–Thu | 16:30–21:30 | Keilor East outpatient private practice |
| Friday | 08:00–08:45 | Sunshine Private inpatient session, **tentative; awaiting confirmation** |
| Friday | 09:00–15:30 | Western Health working window |
| Friday | 15:30–16:00 | Transfer and preparation |
| Friday | 16:00–20:00 | Sunshine Private inpatient consultations |

There is no recurring Friday outpatient session. Western Health is an availability window rather than a blocking meeting; its contained meetings and approved work blocks occupy time. Private practice and transfer commitments remain protected. Tentative commitments also reserve time conservatively until a user changes them.

Within the Western Health window the planner targets 120 minutes of patient care and 60 minutes of academic reading, reserves a configurable lunch and transitions, then allocates available paperwork capacity by task priority and dependency readiness. Existing fixed events are retained. The proposal shows unmet minutes and possible actions when capacity is insufficient; it does not move fixed commitments. This is a planning aid: a clinician must check each proposal and all source data.

Paperwork tasks become eligible when Ready, Scheduled or In Progress, or when their deferral date arrives, and only after all dependencies are Completed. All existing task-linked allocations reduce the estimated work remaining, including allocations on other dates. If planned work was missed, edit or delete that allocation and review the estimate before generating another plan. Calendar allocation does not automatically mark work Completed.

## Tasks and priority rules

Tasks support the requested ID, title, description, category, source, created/due dates, priority, estimated minutes, status, dependencies and last verified date. Completion time and deferral date support the dashboard. The synthetic task register includes clinical, governance, administrative, academic, research, correspondence and personal examples.

Statuses are Inbox, Ready, Scheduled, In Progress, Waiting, Completed and Deferred. Dependencies reference task IDs and cannot form cycles. Waiting tasks and uncompleted prerequisites prevent automatic allocation; review the proposal for other eligibility constraints.

P0 is an explicit immediate safety / critical statutory obligation override. Other scores total at most 100:

| Component | Default scoring |
| --- | --- |
| Deadline urgency, 0–30 | Due/overdue: 30; within 1 day: 28; 3 days: 25; 7 days: 20; 14 days: 12; 30 days: 5; later/no due date: 0 |
| Consequences, 0–30 | User assessment |
| Importance, 0–20 | User assessment |
| Dependency impact, 0–10 | User assessment of work unlocked |
| Task age, 0–10 | 1 point per 7 elapsed days, capped at 10 |

Default bands: P1 80–100, P2 60–79, P3 35–59, P4 below 35. The settings editor validates configurable deadline bands, ageing rate and priority thresholds. Each score explains its inputs; dependency impact points and dependency eligibility are separate concepts.

## Reviewed CSV import

Use `examples/calendar.csv` as a synthetic example. Required columns:

```csv
external_id,title,start,end,source,status
demo-meeting-001,Synthetic committee meeting,2026-10-19T10:00:00+11:00,2026-10-19T10:30:00+11:00,Western Health,Confirmed
```

Optional columns: `kind`, `location`, `description`. Status must be `Confirmed` or `Tentative`; use full ISO dates and times. Missing titles, dates, sources and statuses are rejected. Supported kinds include `meeting`, `commitment`, `availability`, `patient_care`, `academic_reading`, `paperwork`, `lunch` and `transition`.

An import updates the event with the same `(source, external_id)`, making repeated imports safe. Keep both fields stable when exporting a corrected screenshot transcription. Rows omitted from a later file are retained; they are not automatically deleted. Exact recurring occurrences are skipped, and exact matching manual events in the same source can acquire the import identity. If source systems cannot supply stable IDs, assign and maintain them manually. The prototype cannot infer that two differently identified or substantially changed rows describe the same event.

## Authentication and data protection

The default local-demo mode permits access without a password for synthetic data on your own machine. Configure a password to enable the built-in single-account login:

```bash
python -m clinical_planner.security hash-password
```

The command prompts twice without echoing the password and prints a salted PBKDF2-SHA256 hash. Set that hash as `PLANNER_AUTH_PASSWORD_HASH` through a secret manager or your private process environment. Quote the entire value when setting it in a shell because it contains `$`. The application never writes a plaintext password. `.env.example` describes variable names; `.env` files are not loaded automatically.

Set `PLANNER_DEPLOYMENT=production` to make missing or invalid authentication configuration fail closed. Login expires after 30 minutes of inactivity; logout clears the session. Failed attempts trigger a short browser-session cooldown. Login, failure, expiry, logout and first app access are logged in SQLite without passwords or content. The prototype uses a shared local account; the cooldown is not a substitute for perimeter rate limiting or an identity provider.

**This local prototype is not certified for clinical deployment.** Before any real-world deployment, complete the controls in [deployment and security](docs/deployment.md), including approved data handling, TLS, named-user identity and authorization as appropriate, managed secrets, encrypted storage, access-log review, tested backups and operating ownership. Do not connect to Western Health, a private practice clinical system, Gmail or Outlook without explicit approval and a separately implemented and tested connector.

## Database, migrations and backups

Numbered SQL files under `clinical_planner/migrations/` are applied transactionally and tracked with checksums in `schema_migrations`. Do not edit an already applied migration; add another numbered file. The SQLite database uses foreign keys and WAL mode. Files are restricted to the owning user where supported. SQLite itself does not encrypt data.

Create and verify an online SQLite backup (destination must not exist):

```bash
python -m clinical_planner.backup --database data/planner.db --output backups/planner-2026-10-15.db
python -m clinical_planner.backup --verify backups/planner-2026-10-15.db
```

This uses SQLite's backup API, includes committed WAL data, runs an integrity check and sets file mode `0600` where supported. It does not automatically upload backups, schedule them or delete older files. Follow [the backup and restore runbook](docs/backup.md) for retention and a non-destructive restore procedure.

## Architecture and extension points

- `app.py`: Streamlit presentation and approval workflow.
- `clinical_planner/models.py`, `db.py`, `migrations/`: SQLAlchemy models and versioned SQLite storage.
- `calendar_service.py`, `tasks.py`: validated calendar and task operations.
- `priority.py`, `scheduler.py`: configurable priority scoring and pure planning logic / approved persistence.
- `integrations.py`: reviewed CSV import and explicitly gated image-extraction adapter boundary.
- `security.py`, `backup.py`: authentication, audit helper and consistent backups.
- `seed.py`: idempotent synthetic examples and the weekly schedule.
- `tests/`: automated behavioural checks for storage, calendar, importing, priority, planning and protection helpers.

## Functions awaiting implementation

- Gmail synchronization, OAuth, message extraction and correspondence ingestion.
- Outlook / Microsoft Graph synchronization and live Western Health calendar access.
- Any connection to clinical or private practice systems.
- An approved image extraction provider, its credentials, confidence reporting, provider tests and an extraction review workflow. Screenshots currently support visual review and manual entry only.
- Real patient or clinical record processing and a formally approved data governance model.
- Named-user roles, SSO/MFA, account administration, centralized tamper-resistant audit storage and perimeter login rate limits.
- Hosted deployment, TLS termination, managed encrypted storage, automatic backup scheduling and restore monitoring.
- External reminders, push notifications, calendar write-back and mobile/offline synchronization.
- Complex recurrence exceptions, cross-timezone travel calendars and automatic reconciliation across unrelated event IDs.

No Gmail or Outlook synchronization is implemented or represented as working.
