"""Explicit integration boundaries: reviewed CSV works; remote sync is absent.

CSV fields external_id,title,start,end,source,status are mandatory. Dates must
contain an explicit ISO date and time; no date, title, status, or source is ever
guessed. parse_calendar_csv performs a read-only preview. import_events repeats
validation, then atomically upserts by (source, external_id), without committing.

Screenshot extraction is deliberately disabled unless an application developer
supplies an approved adapter and explicit configuration. This module ships no
image provider and no Gmail, Outlook, or clinical-system network client.
"""
import csv
from dataclasses import dataclass
from datetime import datetime
from io import StringIO
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from .calendar_service import get_occurrences, validated_event_fields
from .models import Event
from .security import audit
from .timeutils import parse_datetime

CSV_REQUIRED = ("external_id", "title", "start", "end", "source", "status")
CSV_OPTIONAL = ("kind", "location", "description")


def _validate_import_rows(rows: list[dict]) -> list[dict]:
    if not isinstance(rows, list):
        raise ValueError("Import rows must be a list.")
    if len(rows) > 10000:
        raise ValueError("Import at most 10,000 calendar rows at a time.")
    validated = []
    seen = set()
    for index, raw in enumerate(rows, start=2):
        if not isinstance(raw, dict):
            raise ValueError(f"Row {index}: expected a mapping of calendar fields.")
        missing = [name for name in CSV_REQUIRED if name not in raw or raw[name] is None or (isinstance(raw[name], str) and not raw[name].strip())]
        if missing:
            raise ValueError(f"Row {index}: required fields missing: {', '.join(missing)}.")
        if unknown := set(raw) - set(CSV_REQUIRED + CSV_OPTIONAL):
            raise ValueError(f"Row {index}: unsupported fields: {', '.join(sorted(unknown))}.")
        values = dict(raw)
        try:
            for name in ("start", "end"):
                if isinstance(values[name], str):
                    values[name] = parse_datetime(values[name])
            for name in ("status", "kind"):
                if isinstance(values.get(name), str):
                    values[name] = values[name].strip()
            if not values.get("kind"):
                values["kind"] = "meeting"
            values = validated_event_fields(values)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Row {index}: {exc}") from exc
        key = (values["source"], values["external_id"])
        if key in seen:
            raise ValueError(f"Row {index}: duplicate source and external_id within this file.")
        seen.add(key)
        validated.append(values)
    return validated


def parse_calendar_csv(text: str) -> list[dict]:
    """Return strict validated preview rows; report errors before any database write."""
    if not isinstance(text, str):
        raise ValueError("CSV input must be decoded text.")
    reader = csv.DictReader(StringIO(text.lstrip("\ufeff")), strict=True)
    if not reader.fieldnames:
        raise ValueError("CSV requires a header row.")
    if len(reader.fieldnames) != len(set(reader.fieldnames)):
        raise ValueError("CSV contains duplicate header names.")
    if missing := set(CSV_REQUIRED) - set(reader.fieldnames):
        raise ValueError(f"CSV is missing required headers: {', '.join(sorted(missing))}.")
    if unknown := set(reader.fieldnames) - set(CSV_REQUIRED + CSV_OPTIONAL):
        raise ValueError(f"CSV has unsupported headers: {', '.join(sorted(unknown))}.")
    try:
        rows = list(reader)
    except csv.Error as exc:
        raise ValueError(f"Invalid CSV: {exc}") from exc
    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise ValueError("CSV rows must contain exactly the number of columns in the header.")
    # Return only documented CSV fields: internal task_id/defaults remain private.
    return [{key: value for key, value in row.items() if key != "task_id"} for row in _validate_import_rows(rows)]


def _matches(a, row: dict) -> bool:
    return (a.source, a.title.casefold(), a.start, a.end) == (row["source"], row["title"].casefold(), row["start"], row["end"])


def import_events(session: Session, rows: list[dict]) -> dict[str, int]:
    """Import a reviewed preview; updates preserve event identity and rollback as a unit.

    An exact recurrence already in the calendar is skipped. An exact manual event
    in the same source can acquire its external ID, so subsequent imports update
    the same row. Review is the UI caller's responsibility. Nothing commits here.
    """
    clean = _validate_import_rows(rows)
    result = {"created": 0, "updated": 0, "unchanged": 0, "skipped_recurring": 0, "merged_manual": 0}
    if not clean:
        return result
    lower = min(row["start"].date() for row in clean)
    upper = max(row["end"].date() for row in clean)
    recurring = [item for item in get_occurrences(session, lower, upper) if item.recurring_id is not None]
    # Python's SQLite legacy transaction mode may not BEGIN on SELECT. A bare
    # SAVEPOINT/RELEASE would then persist imports before the caller commits.
    # Start the real outer transaction when required, preserving rollback.
    connection = session.connection()
    if connection.dialect.name == "sqlite" and not connection.connection.driver_connection.in_transaction:
        connection.exec_driver_sql("BEGIN")
    # A savepoint prevents partial import if a database constraint rejects a row.
    with session.begin_nested():
        for row in clean:
            current = session.scalar(select(Event).where(Event.source == row["source"], Event.external_id == row["external_id"]))
            if current is not None:
                changed = any(getattr(current, name) != value for name, value in row.items() if name != "task_id")
                if changed:
                    for name, value in row.items():
                        if name != "task_id":
                            setattr(current, name, value)
                    result["updated"] += 1
                else:
                    result["unchanged"] += 1
                continue
            if any(_matches(item, row) for item in recurring):
                result["skipped_recurring"] += 1
                continue
            manual = list(session.scalars(select(Event).where(
                Event.source == row["source"], Event.external_id.is_(None), Event.start == row["start"],
                Event.end == row["end"], Event.task_id.is_(None),
            )))
            exact_manual = [item for item in manual if _matches(item, row) and item.kind == row["kind"] and item.status == row["status"] and item.location == row["location"]]
            if len(exact_manual) == 1:
                for name, value in row.items():
                    setattr(exact_manual[0], name, value)
                result["merged_manual"] += 1
            else:
                session.add(Event(**row))
                result["created"] += 1
        session.flush()
        audit(session, "calendar.imported", "calendar", ", ".join(f"{key}={value}" for key, value in result.items()))
    return result


class ExtractionUnavailable(RuntimeError):
    """Screenshot extraction is not configured, approved, or implemented."""


class ImageExtractionService(Protocol):
    """Adapters must return explicit reviewed event fields, never guessed values."""
    service_name: str

    def extract(self, image: bytes, mime_type: str) -> list[dict]: ...


@dataclass(frozen=True)
class ExtractionConfig:
    enabled: bool = False
    approved: bool = False
    provider: str = ""


class DisabledImageExtraction:
    service_name = "Disabled"

    def extract(self, image: bytes, mime_type: str) -> list[dict]:
        raise ExtractionUnavailable("Screenshot extraction is disabled. Use manual entry or reviewed CSV import.")


def configured_extractor(config: ExtractionConfig, service: ImageExtractionService | None = None) -> ImageExtractionService:
    """Gate adapters by explicit configuration and approval; never create a client."""
    if not config.enabled or not config.approved or not config.provider.strip() or service is None:
        return DisabledImageExtraction()
    if service.service_name != config.provider:
        raise ValueError("The configured provider must match the approved extraction service.")
    return service
