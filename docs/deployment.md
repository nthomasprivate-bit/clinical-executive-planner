# Deployment and security notes

The authorized initial use is a local prototype containing synthetic or de-identified planning information. No connector to hospital systems, clinical records or email is included. A password alone does not make this application suitable for sensitive clinical information.

## Local operation

Keep Streamlit bound to `127.0.0.1`; the checked-in `.streamlit/config.toml` does this. Keep SQLite, uploaded materials, working directories and backups on an access-controlled device. Use synthetic screenshots only. The screenshot review UI does not call a remote extraction service. Do not send application data in issue reports or version-control commits.

The app uses one local identity when login is enabled. The default unauthenticated mode is explicitly `local-demo`. Configure a password hash with `python -m clinical_planner.security hash-password` and place it in `PLANNER_AUTH_PASSWORD_HASH`; the password is never stored. Set `PLANNER_DEPLOYMENT=production` before any hosted use so missing credentials block access. The environment is evaluated server-side; this variable is not an approval to process real records.

## Required deployment work

Before a real deployment, assign an owner and establish:

1. Approval for the specific hosting environment, purpose, user population and data classes. Confirm applicable employer, hospital and privacy obligations with the responsible parties.
2. Authentication for every access, with named identities, MFA/SSO and appropriate authorization if more than one person uses the service. The current single shared password is a prototype gate.
3. TLS at an approved reverse proxy, a private network or suitable network restrictions, tested session handling, and proxy-level rate limits. The current browser-session login cooldown can be bypassed by creating another session.
4. Managed secret injection and rotation. Do not put credentials into code, `.env` commits, database records, uploaded files or logs. Password-hash rotation expires active sessions on their next app interaction.
5. Device / volume encryption, least-privilege OS access, restricted backups and secure disposal. SQLite is not encrypted by this application. Database and backup file permissions are reduced to `0600` on supporting filesystems; those permissions cannot protect an already compromised account.
6. Review and retention for content-free access and mutation logs, reliable time synchronization, and central log collection if required. The local `audit_log` table can be modified by someone with database access; it is not tamper-resistant. Do not store sensitive task text, correspondence, passwords, screenshot data or patient identifiers in audit details.
7. The documented backup schedule, encrypted off-device copies, integrity checks and regular restore exercises. A successful integrity check confirms SQLite consistency; a restore exercise checks operational recovery.
8. Dependency patching, vulnerability review, availability monitoring and an incident/recovery owner. Test the actual reverse proxy and deployment configuration rather than assuming local tests cover hosting.

## Logging and sessions

With an engine supplied to `require_auth`, app access is logged once per browser session. Login success/failure, session expiry and logout also create audit entries. Application services use the audit helper for user operations, carrying the current authenticated identity through a request-local context. The single authenticated account is recorded as `planner-user`; unauthenticated synthetic sessions are `local-demo`. Authentication fails closed in production when an engine for access logging is absent. UTC audit timestamps are separate from Melbourne calendar times.

Login expires after 30 minutes without an application interaction. Five failed attempts in one browser session start a 60-second cooldown, increasing to a maximum of five minutes. This is a convenience guard for the local prototype, not distributed brute-force prevention. Streamlit session state is server memory and is reset when the process/session ends.

## Integration boundary

`ImageExtractionService` and its explicit enabled/approved/provider gate are extension points only. No provider, network client, OAuth credentials or live integration ships with the prototype. A future provider must be approved, explicitly configured, tested with synthetic data, and return reviewable fields. Missing information must remain absent or require manual correction. A recurring schedule must remain separate from imported dated events, and imported identities must remain stable.
