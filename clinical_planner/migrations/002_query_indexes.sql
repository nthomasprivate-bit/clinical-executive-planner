CREATE INDEX ix_event_start_end ON events(start, end);
CREATE INDEX ix_event_task ON events(task_id);
CREATE INDEX ix_task_status_due ON tasks(status, due_date);
CREATE INDEX ix_audit_timestamp ON audit_log(timestamp);
CREATE INDEX ix_recurring_weekday_active ON recurring_commitments(weekday, active);
