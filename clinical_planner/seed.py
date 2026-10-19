"""Idempotent initial weekly commitments and explicitly synthetic examples."""
from datetime import datetime, time, timedelta
from clinical_planner.models import Event, RecurringCommitment, Setting
from clinical_planner.tasks import save_task
from clinical_planner.timeutils import today

def seed_data(session):
    if session.get(Setting, "seed_v2") is not None:
        return
    morning = [
        ("08:00", "08:45", "Sunshine Private inpatient consultations", "Sunshine Private Hospital", "patient_care"),
        ("08:45", "08:52", "Walk to Sunshine Public Hospital", "Transitions", "transition"),
        ("08:52", "09:00", "Preparation for Western Health", "Western Health", "transition"),
        ("09:00", "15:30", "Western Health working window", "Western Health", "availability"),
        ("15:30", "15:45", "Transfer and preparation at Sunshine Private", "Transitions", "transition"),
        ("15:45", "16:20", "Sunshine Private inpatient consultations", "Sunshine Private Hospital", "patient_care"),
        ("16:20", "16:30", "Travel to Keilor East", "Transitions", "transition"),
        ("16:30", "21:30", "Keilor East outpatient private practice", "Keilor East", "commitment"),
    ]
    for weekday in range(4):
        for start, end, title, source, kind in morning:
            session.add(RecurringCommitment(title=title, weekday=weekday, start_time=time.fromisoformat(start), end_time=time.fromisoformat(end), source=source, status="Confirmed", kind=kind, location=source if source != "Transitions" else "Local transfer"))
    for start, end, title, source, kind, status in [
        ("08:00", "08:45", "Sunshine Private morning inpatient session — awaiting confirmation", "Sunshine Private Hospital", "patient_care", "Tentative"),
        ("09:00", "15:30", "Western Health working window", "Western Health", "availability", "Confirmed"),
        ("15:30", "16:00", "Transfer and preparation at Sunshine Private", "Transitions", "transition", "Confirmed"),
        ("16:00", "20:00", "Sunshine Private inpatient consultations", "Sunshine Private Hospital", "patient_care", "Confirmed"),
    ]:
        session.add(RecurringCommitment(title=title, weekday=4, start_time=time.fromisoformat(start), end_time=time.fromisoformat(end), source=source, status=status, kind=kind, location=source))
    current = today()
    examples = [
        dict(title="[SYNTHETIC] Review mock statutory reporting deadline", category="Governance", status="Ready", due_date=current, estimated_minutes=30, safety_critical=True, consequences=30, importance=20),
        dict(title="[SYNTHETIC] Complete de-identified service audit summary", category="Clinical", status="Ready", due_date=current + timedelta(days=1), estimated_minutes=45, consequences=25, importance=18, dependency_impact=8),
        dict(title="[SYNTHETIC] Review governance committee agenda", category="Governance", status="Ready", due_date=current + timedelta(days=3), estimated_minutes=30, consequences=24, importance=17),
        dict(title="[SYNTHETIC] Prepare mock research ethics checklist", category="Research", status="Ready", due_date=current + timedelta(days=7), estimated_minutes=60, consequences=15, importance=18),
        dict(title="[SYNTHETIC] Read evidence appraisal teaching paper", category="Academic", status="Inbox", due_date=current + timedelta(days=5), estimated_minutes=60, importance=18),
        dict(title="[SYNTHETIC] Draft reply to fictional conference invitation", category="Correspondence", source="Gmail manual example", status="Ready", due_date=current + timedelta(days=2), estimated_minutes=15),
        dict(title="[SYNTHETIC] Reconcile mock practice administration register", category="Administrative", status="Ready", due_date=current - timedelta(days=2), estimated_minutes=30, consequences=20),
        dict(title="[SYNTHETIC] Await sample committee approval", category="Governance", status="Waiting", estimated_minutes=20),
        dict(title="[SYNTHETIC] Update teaching slide references", category="Academic", status="Deferred", deferred_to=current + timedelta(days=1), estimated_minutes=25),
        dict(title="[SYNTHETIC] Archive mock meeting notes", category="Administrative", status="Completed", estimated_minutes=10),
        dict(title="[SYNTHETIC] Book personal professional development time", category="Personal", status="Inbox", estimated_minutes=15),
    ]
    tasks = []
    for index, values in enumerate(examples):
        tasks.append(save_task(session, description="Fictional prototype example. Contains no patient data, real records or real correspondence.", created_date=current - timedelta(days=7 * (index + 1)), last_verified_date=current, **values))
    save_task(session, title="[SYNTHETIC] Submit mock audit summary after review", description="Dependency example; requires completion of the service audit summary.", category="Clinical", status="Ready", dependencies=[tasks[1].id], estimated_minutes=15, due_date=current + timedelta(days=2), consequences=20, importance=16)
    monday = current - timedelta(days=current.weekday())
    for offset, start, end, title in [
        (0, "10:00", "10:30", "[SYNTHETIC] Team planning meeting"),
        (1, "11:00", "12:00", "[SYNTHETIC] Governance review"),
        (3, "13:00", "14:00", "[SYNTHETIC] Research planning meeting"),
    ]:
        day = monday + timedelta(days=offset)
        session.add(Event(title=title, start=datetime.combine(day, time.fromisoformat(start)), end=datetime.combine(day, time.fromisoformat(end)), source="Western Health", external_id=f"seed-meeting-{offset}", status="Confirmed", kind="meeting", location="Synthetic meeting room", description="Fictional calendar example, not imported from Outlook."))
    session.add(Setting(key="seed_v2", value={"installed": True, "reference_date": current.isoformat()}))
    session.flush()
