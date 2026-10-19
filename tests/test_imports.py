from datetime import date, datetime, time

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from clinical_planner.calendar_service import get_occurrences, save_event, save_recurring
from clinical_planner.integrations import (
    ExtractionConfig, ExtractionUnavailable, configured_extractor, import_events, parse_calendar_csv,
)
from clinical_planner.models import AuditLog, Base, Event


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


HEADER = "external_id,title,start,end,source,status\n"
ROW = "wh-demo,Synthetic meeting,2026-10-19T10:00:00+11:00,2026-10-19T11:00:00+11:00,Western Health,Confirmed\n"


def test_csv_preview_is_explicit_and_timezone_aware():
    rows = parse_calendar_csv(HEADER + ROW)
    assert rows[0]["start"] == datetime(2026, 10, 19, 10)
    assert rows[0]["status"] == "Confirmed"
    assert rows[0]["source"] == "Western Health"


@pytest.mark.parametrize("offset", ["+11:00", "+10:00"])
def test_csv_repeated_hour_rejected_consistently_with_manual_entry(offset):
    row = f"dst-demo,Synthetic repeated-hour meeting,2026-04-05T02:30:00{offset},2026-04-05T03:30:00+10:00,Western Health,Confirmed\n"
    with pytest.raises(ValueError, match="Repeated-hour times are currently unsupported"):
        parse_calendar_csv(HEADER + row)


@pytest.mark.parametrize("csv_text", [
    "title,start,end,source,status\n" + ROW,
    HEADER + ROW.replace("Confirmed", ""),
    HEADER + ROW.replace("2026-10-19T10:00:00+11:00", "10:00"),
    HEADER + ROW.replace("2026-10-19T10:00:00+11:00", "2026-10-19"),
    HEADER + ROW.replace("Confirmed", "Unclear"),
    HEADER + ROW + ROW,
    HEADER + ROW.rstrip("\n") + ",unexpected\n",
    "external_id,title,start,end,source,status,status\n",
    HEADER + ROW.replace("2026-10-19T11:00:00+11:00", "2026-10-19T09:00:00+11:00"),
])
def test_csv_rejects_missing_ambiguous_and_invalid_information(csv_text):
    with pytest.raises(ValueError):
        parse_calendar_csv(csv_text)


def test_import_is_idempotent_and_updated_screenshot_keeps_same_event(session):
    rows = parse_calendar_csv(HEADER + ROW)
    assert import_events(session, rows)["created"] == 1
    saved = session.scalar(select(Event))
    original_id = saved.id
    assert import_events(session, rows)["unchanged"] == 1
    changed = parse_calendar_csv(HEADER + ROW.replace("T10:00", "T10:15"))
    assert import_events(session, changed)["updated"] == 1
    assert saved.id == original_id
    assert saved.start == datetime(2026, 10, 19, 10, 15)
    assert len(list(session.scalars(select(Event)))) == 1


def test_same_external_identifier_in_different_sources_is_separate(session):
    rows = parse_calendar_csv(HEADER + ROW + ROW.replace("Western Health", "Manual"))
    assert import_events(session, rows)["created"] == 2


def test_import_never_commits_before_caller_approval(session):
    import_events(session, parse_calendar_csv(HEADER + ROW))
    session.rollback()
    assert list(session.scalars(select(Event))) == []
    assert list(session.scalars(select(AuditLog))) == []


def test_import_audit_records_counts_without_uploaded_contents(session):
    import_events(session, parse_calendar_csv(HEADER + ROW))
    log = session.scalar(select(AuditLog))
    assert log.action == "calendar.imported"
    assert "created=1" in log.detail
    assert "Synthetic meeting" not in log.detail
    assert "wh-demo" not in log.detail


def test_duplicate_or_invalid_row_prevents_any_partial_update(session):
    rows = parse_calendar_csv(HEADER + ROW)
    import_events(session, rows)
    saved = session.scalar(select(Event))
    changed = dict(rows[0], title="Must not be saved")
    invalid = dict(rows[0], external_id="other", title="")
    with pytest.raises(ValueError):
        import_events(session, [changed, invalid])
    assert saved.title == "Synthetic meeting"
    with pytest.raises(ValueError, match="duplicate"):
        import_events(session, [rows[0], changed])
    assert saved.title == "Synthetic meeting"


def test_import_skips_exact_recurring_commitment(session):
    save_recurring(session, title="Synthetic meeting", weekday=0, start_time=time(10), end_time=time(11), source="Western Health")
    counts = import_events(session, parse_calendar_csv(HEADER + ROW))
    assert counts["skipped_recurring"] == 1
    assert len(get_occurrences(session, date(2026, 10, 19), date(2026, 10, 19))) == 1
    assert list(session.scalars(select(Event))) == []


def test_same_title_and_times_in_another_source_are_not_silently_skipped(session):
    save_recurring(session, title="Synthetic meeting", weekday=0, start_time=time(10), end_time=time(11), source="Keilor East")
    counts = import_events(session, parse_calendar_csv(HEADER + ROW))
    assert counts["created"] == 1
    assert counts["skipped_recurring"] == 0
    assert len(get_occurrences(session, date(2026, 10, 19), date(2026, 10, 19))) == 2


def test_import_merges_exact_reviewed_manual_entry(session):
    row = save_event(session, title="Synthetic meeting", start=datetime(2026, 10, 19, 10), end=datetime(2026, 10, 19, 11), source="Western Health")
    old_id = row.id
    assert import_events(session, parse_calendar_csv(HEADER + ROW))["merged_manual"] == 1
    assert row.id == old_id
    assert row.external_id == "wh-demo"
    assert len(list(session.scalars(select(Event)))) == 1


def test_csv_bom_optional_fields_and_quoted_title(session):
    rows = parse_calendar_csv("\ufeff" + HEADER.replace("status\n", "status,kind,location,description\n") + ROW.rstrip("\n").replace("Synthetic meeting", '"Synthetic, quoted meeting"') + ",meeting,Demo room,Synthetic only\n")
    assert rows[0]["title"] == "Synthetic, quoted meeting"
    assert import_events(session, rows)["created"] == 1


class FakeExtractor:
    service_name = "approved-test-adapter"

    def __init__(self):
        self.called = False

    def extract(self, image, mime_type):
        self.called = True
        return []


@pytest.mark.parametrize("config", [
    ExtractionConfig(),
    ExtractionConfig(enabled=True, approved=False, provider="approved-test-adapter"),
    ExtractionConfig(enabled=False, approved=True, provider="approved-test-adapter"),
    ExtractionConfig(enabled=True, approved=True, provider=""),
])
def test_screenshots_never_use_service_without_explicit_approval(config):
    adapter = FakeExtractor()
    service = configured_extractor(config, adapter)
    with pytest.raises(ExtractionUnavailable, match="disabled"):
        service.extract(b"synthetic", "image/png")
    assert not adapter.called


def test_enabling_extraction_still_requires_implemented_matching_adapter():
    config = ExtractionConfig(enabled=True, approved=True, provider="approved-test-adapter")
    with pytest.raises(ExtractionUnavailable):
        configured_extractor(config).extract(b"", "image/png")
    adapter = FakeExtractor()
    assert configured_extractor(config, adapter) is adapter
    with pytest.raises(ValueError, match="match"):
        configured_extractor(ExtractionConfig(True, True, "different-provider"), adapter)
