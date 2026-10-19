"""SQLite persistence models. Event datetimes are Melbourne local wall times.

Validate local times with timeutils before writing. Audit timestamps use UTC.
Recurring commitments deliberately live outside the imported events table.
"""
from datetime import date, datetime, time, timezone
from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, JSON, String, Text, Time, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from clinical_planner.timeutils import today

CATEGORIES = ("Clinical", "Governance", "Administrative", "Academic", "Research", "Correspondence", "Personal")
STATUSES = ("Inbox", "Ready", "Scheduled", "In Progress", "Waiting", "Completed", "Deferred")
EVENT_STATUSES = ("Confirmed", "Tentative")
KINDS = ("meeting", "commitment", "availability", "patient_care", "academic_reading", "paperwork", "lunch", "transition")

def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)

class Base(DeclarativeBase):
    pass

class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(30), default="Administrative")
    source: Mapped[str] = mapped_column(String(80), default="Manual")
    created_date: Mapped[date] = mapped_column(Date, default=today)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    estimated_minutes: Mapped[int] = mapped_column(Integer, default=30)
    status: Mapped[str] = mapped_column(String(20), default="Inbox")
    dependencies: Mapped[list] = mapped_column(JSON, default=list)
    last_verified_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    safety_critical: Mapped[bool] = mapped_column(Boolean, default=False)
    consequences: Mapped[int] = mapped_column(Integer, default=10)
    importance: Mapped[int] = mapped_column(Integer, default=10)
    dependency_impact: Mapped[int] = mapped_column(Integer, default=0)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    deferred_to: Mapped[date | None] = mapped_column(Date, nullable=True)

class RecurringCommitment(Base):
    __tablename__ = "recurring_commitments"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    weekday: Mapped[int] = mapped_column(Integer)
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)
    source: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(20), default="Confirmed")
    kind: Mapped[str] = mapped_column(String(30), default="commitment")
    location: Mapped[str] = mapped_column(String(200), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_until: Mapped[date | None] = mapped_column(Date, nullable=True)

class Event(Base):
    __tablename__ = "events"
    __table_args__ = (UniqueConstraint("source", "external_id", name="uq_event_source_external"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    start: Mapped[datetime] = mapped_column(DateTime)
    end: Mapped[datetime] = mapped_column(DateTime)
    source: Mapped[str] = mapped_column(String(80), default="Manual")
    external_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="Confirmed")
    kind: Mapped[str] = mapped_column(String(30), default="meeting")
    location: Mapped[str] = mapped_column(String(200), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)

class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    actor: Mapped[str] = mapped_column(String(100), default="local-demo")
    action: Mapped[str] = mapped_column(String(100))
    entity: Mapped[str] = mapped_column(String(100), default="")
    detail: Mapped[str] = mapped_column(String(500), default="")
