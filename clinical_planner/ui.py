"""Local Streamlit presentation layer. All integrations remain explicit."""
from __future__ import annotations

import calendar
import hashlib
import html
import json
import os
from dataclasses import asdict
from datetime import date, datetime, time, timedelta, timezone

import pandas as pd
import streamlit as st
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from .calendar_service import detect_conflicts, get_occurrences, save_event, save_recurring, delete_event, source_color
from .integrations import parse_calendar_csv, import_events
from .models import CATEGORIES, EVENT_STATUSES, KINDS, STATUSES, Event, RecurringCommitment, Task
from .priority import PriorityConfig, rank_tasks, score_task
from .scheduler import PlannerConfig, approve_plan, planning_occurrences, propose_plan
from .security import logout
from .settings import DEFAULT_PRIORITY, DEFAULT_SCHEDULER, get_settings, save_settings
from .tasks import list_tasks, save_task
from .timeutils import ZONE, today


CSS = """
<style>
.block-container {padding-top:2rem; padding-bottom:3rem; max-width:1600px;}
[data-testid="stSidebar"] {background:#f2f5f8;}
.eyebrow {color:#52716d; text-transform:uppercase; letter-spacing:.15em; font-size:.74rem; font-weight:700; margin-bottom:.3rem;}
.page-subtitle {color:#657180; margin-top:-.7rem; margin-bottom:1.4rem;}
.calendar-day {min-height:155px; padding:.65rem; background:#f6f8fb; border:1px solid #e4e8ef; border-radius:9px; margin-bottom:.5rem;}
.calendar-date {font-size:.85rem; font-weight:700; color:#34445b; margin-bottom:.45rem;}
.calendar-event {font-size:.76rem; line-height:1.4; padding:.4rem .45rem; margin-bottom:.35rem; border-left:3px solid; border-radius:4px; background:white; overflow-wrap:anywhere;}
.calendar-event .time {font-weight:700; display:block;}
.legend-item {display:inline-block; margin:0 1rem .3rem 0; font-size:.8rem; color:#536171;}
.legend-dot {display:inline-block; width:.6rem; height:.6rem; border-radius:50%; margin-right:.35rem;}
.empty-day {color:#8390a1; font-size:.8rem; padding:.3rem 0;}
.task-card {padding:.8rem 1rem; border:1px solid #e1e7ee; border-radius:8px; margin-bottom:.55rem; background:#fff;}
.task-card strong {color:#26374d;}
.task-detail {font-size:.8rem; color:#697586; margin-top:.25rem;}
.pill {display:inline-block; font-size:.7rem; font-weight:700; padding:.12rem .4rem; border-radius:5px; background:#eaf0f5; color:#34445b; margin-right:.35rem;}
.notice {background:#edf5f2; border:1px solid #cee1d8; border-radius:8px; padding:.7rem 1rem; color:#365c4e; font-size:.83rem;}
div[data-testid="stMetric"] {background:#f4f7fa; padding:.9rem 1rem; border-radius:9px; border:1px solid #e4eaf0;}
</style>
"""


def _e(value) -> str:
    return html.escape(str(value))


def _date_label(day: date) -> str:
    return day.strftime("%A, %d %B %Y")


def _config(session):
    return PlannerConfig(**get_settings(session, "scheduler")), PriorityConfig(**get_settings(session, "priority"))


def _persist(session, action, message: str, *, rerun=True):
    try:
        result = action()
        session.commit()
        if rerun:
            st.session_state["_planner_flash"] = message
            st.rerun()
        st.success(message)
        return result
    except (ValueError, TypeError, KeyError) as exc:
        session.rollback()
        st.error(str(exc))
        return None
    except SQLAlchemyError:
        session.rollback()
        st.error("The database could not save this change. Please try again. If the problem persists, check database access and the application logs.")
        return None


def render_app(session):
    st.markdown(CSS, unsafe_allow_html=True)
    with st.sidebar:
        st.markdown("### 🩺 Clinical Executive Planner")
        st.caption("VERSION 2.0 · LOCAL WORKSPACE")
        selected_day = st.date_input("Planning date", value=today(), key="planning_day")
        page = st.radio("Workspace", ["Dashboard", "Calendar", "Tasks", "Import & review", "Settings & safety"], label_visibility="collapsed")
        st.divider()
        st.caption("Australia/Melbourne")
        st.caption("Synthetic and de-identified information only. Do not enter patient details or real email content.")
        st.caption("Outlook and Gmail synchronisation is not implemented.")
        if os.environ.get("PLANNER_AUTH_PASSWORD_HASH") and st.button("Sign out"):
            logout(session.get_bind())
    st.markdown('<div class="eyebrow">Your clinical and professional workspace</div>', unsafe_allow_html=True)
    st.title(page)
    st.markdown(f'<p class="page-subtitle">{_date_label(selected_day)} · Australia/Melbourne</p>', unsafe_allow_html=True)
    if flash := st.session_state.pop("_planner_flash", None):
        st.success(flash)
    if page == "Dashboard":
        dashboard(session, selected_day)
    elif page == "Calendar":
        calendar_page(session, selected_day)
    elif page == "Tasks":
        tasks_page(session, selected_day)
    elif page == "Import & review":
        import_page(session, selected_day)
    else:
        settings_page(session)


def _clipped_minutes(events, kind, windows):
    intervals = []
    for event in events:
        if event.kind != kind or event.status != "Confirmed":
            continue
        for window in windows:
            start, end = max(event.start, window.start), min(event.end, window.end)
            if start < end:
                intervals.append((start, end))
    intervals.sort()
    merged = []
    for start, end in intervals:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return sum(int((end - start).total_seconds() // 60) for start, end in merged)


def dashboard(session, day):
    config, priority_config = _config(session)
    tasks = list_tasks(session)
    occurrences = get_occurrences(session, day, day)
    proposal = propose_plan(day, planning_occurrences(session, day), tasks, config, priority_config)
    windows = [event for event in occurrences if event.kind == "availability" and event.source == "Western Health"]
    patient = _clipped_minutes(occurrences, "patient_care", windows)
    academic = _clipped_minutes(occurrences, "academic_reading", windows)
    cols = st.columns(4)
    cols[0].metric("Patient care scheduled", f"{patient} min", f"Target {config.patient_minutes} min", delta_color="off")
    cols[1].metric("Academic reading scheduled", f"{academic} min", f"Target {config.academic_minutes} min", delta_color="off")
    cols[2].metric("Paperwork capacity", f"{proposal.paperwork_minutes} min", "After targets, lunch & transitions", delta_color="off")
    confirmed = [event for event in occurrences if event.status == "Confirmed" and event.kind != "availability"]
    cols[3].metric("Confirmed appointments", len(confirmed))
    st.caption(f"Today is {_date_label(today())}. Metrics above apply to the selected planning date. Patient care and reading count inside Western Health availability only.")
    conflicts = detect_conflicts(occurrences)
    if conflicts:
        st.warning(f"{len(conflicts)} calendar conflict(s) need review. Fixed commitments will stay in place.")
        _show_conflicts(conflicts)
    left, right = st.columns([1.2, 1], gap="large")
    with left:
        st.subheader("Daily agenda")
        _agenda(occurrences)
    with right:
        st.subheader("Top five priority tasks")
        active = [task for task in tasks if task.status not in ("Completed", "Deferred")]
        for task in rank_tasks(active, day, priority_config)[:5]:
            _task_card(task, day, priority_config)
        if not active:
            st.info("Your active task list is empty.")
        overdue = [task for task in tasks if task.due_date and task.due_date < day and task.status != "Completed"]
        with st.expander(f"Overdue tasks · {len(overdue)}", expanded=bool(overdue)):
            for task in overdue:
                st.write(f"#{task.id} · {task.title} — due {task.due_date:%d %b}")
            if not overdue:
                st.caption("No overdue tasks.")
        completed = [task for task in tasks if task.completed_at and task.completed_at.replace(tzinfo=timezone.utc).astimezone(ZONE).date() == day]
        deferred = [task for task in tasks if task.status == "Deferred" and task.deferred_to == day + timedelta(days=1)]
        st.write(f"**Tasks completed on this date:** {len(completed)}")
        for task in completed:
            st.caption(f"✓ {task.title}")
        st.write(f"**Tasks deferred to tomorrow:** {len(deferred)}")
        for task in deferred:
            st.caption(f"→ {task.title}")
    st.divider()
    st.subheader("Build a proposed daily plan")
    st.caption("The planner uses free time inside Western Health availability. Meetings, tentative commitments and private practice remain fixed. Review every block before saving.")
    if st.button("Generate proposed daily plan", type="primary"):
        st.session_state["proposed_plan"] = proposal
        st.session_state["proposal_approved"] = False
    plan = st.session_state.get("proposed_plan")
    if plan is not None and plan.day == day:
        if plan.blocks:
            st.dataframe(pd.DataFrame([{"Start": block.start.strftime("%H:%M"), "End": block.end.strftime("%H:%M"), "Activity": block.title, "Type": block.kind.replace("_", " ").title(), "Minutes": block.minutes} for block in plan.blocks]), hide_index=True, use_container_width=True)
        else:
            st.info("No new time blocks are available to propose.")
        unmet = {key: value for key, value in plan.unmet_minutes.items() if value}
        if unmet:
            st.warning("Unmet targets: " + "; ".join(f"{key.replace('_', ' ').title()} {value} min" for key, value in unmet.items()))
        for suggestion in plan.suggestions:
            st.write(f"• {suggestion}")
        st.checkbox("I have reviewed and approve these proposed time blocks.", key="proposal_approved")
        if st.button("Approve and save time blocks", disabled=not st.session_state.get("proposal_approved") or not plan.blocks):
            saved = _persist(session, lambda: approve_plan(session, plan, approved=True), "Approved time blocks saved.", rerun=False)
            if saved is not None:
                del st.session_state["proposed_plan"]
                st.session_state["_planner_flash"] = "Approved time blocks saved."
                st.rerun()


def _task_card(task, day, config):
    score = score_task(task, day, config)
    due = task.due_date.strftime("%d %b %Y") if task.due_date else "No due date"
    st.markdown(f'<div class="task-card"><span class="pill">{_e(score.priority)} · {_e(score.total)}</span><strong>#{task.id} {_e(task.title)}</strong><div class="task-detail">{_e(task.category)} · {_e(task.status)} · {_e(task.estimated_minutes)} min · {_e(due)}</div></div>', unsafe_allow_html=True)
    with st.expander(f"Why this score? · Task {task.id}"):
        for explanation in score.explanation:
            st.write(explanation)


def _agenda(occurrences):
    if not occurrences:
        st.info("No commitments for this date.")
        return
    rows = [{"Time": f"{event.start:%H:%M}–{event.end:%H:%M}", "Appointment": event.title, "Source": event.source, "Status": event.status, "Type": event.kind.replace("_", " ").title(), "Location": event.location} for event in sorted(occurrences, key=lambda event: (event.start, event.end))]
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)


def _show_conflicts(conflicts):
    for conflict in conflicts:
        # Calendar service returns two overlapping occurrences per conflict.
        first, second = conflict
        st.write(f"⚠ {first.start:%a %H:%M} · {first.title} / {second.title}")


def _legend(occurrences):
    sources = sorted({event.source for event in occurrences})
    st.markdown("".join(f'<span class="legend-item"><span class="legend-dot" style="background:{_e(source_color(source))}"></span>{_e(source)}</span>' for source in sources), unsafe_allow_html=True)


def _day_html(day, occurrences, compact=False):
    events = sorted([event for event in occurrences if event.start.date() <= day <= (event.end - timedelta(microseconds=1)).date()], key=lambda event: event.start)
    label = day.strftime("%a %d") if not compact else str(day.day)
    parts = [f'<div class="calendar-day"><div class="calendar-date">{_e(label)}</div>']
    for event in events:
        tentative = " · tentative" if event.status == "Tentative" else ""
        parts.append(f'<div class="calendar-event" style="border-left-color:{_e(source_color(event.source))}"><span class="time">{event.start:%H:%M}–{event.end:%H:%M}{_e(tentative)}</span>{_e(event.title)}</div>')
    if not events:
        parts.append('<div class="empty-day">No commitments</div>')
    parts.append("</div>")
    return "".join(parts)


def calendar_page(session, day):
    view = st.radio("Calendar view", ["Week", "Month", "Daily agenda"], horizontal=True)
    if view == "Week":
        start = day - timedelta(days=day.weekday())
        end = start + timedelta(days=6)
        occurrences = get_occurrences(session, start, end)
        st.subheader(f"{start:%d %B} – {end:%d %B %Y}")
        _legend(occurrences)
        for index, column in enumerate(st.columns(7, gap="small")):
            with column:
                st.markdown(_day_html(start + timedelta(days=index), occurrences), unsafe_allow_html=True)
    elif view == "Month":
        weeks = calendar.Calendar(firstweekday=0).monthdatescalendar(day.year, day.month)
        occurrences = get_occurrences(session, weeks[0][0], weeks[-1][-1])
        st.subheader(day.strftime("%B %Y"))
        _legend(occurrences)
        for index, column in enumerate(st.columns(7)):
            column.markdown(f"**{calendar.day_abbr[index]}**")
        for week in weeks:
            for current, column in zip(week, st.columns(7, gap="small")):
                with column:
                    st.markdown(_day_html(current, occurrences, compact=True), unsafe_allow_html=True)
    else:
        occurrences = get_occurrences(session, day, day)
        _legend(occurrences)
        _agenda(occurrences)
    conflicts = detect_conflicts(occurrences)
    if conflicts:
        with st.expander(f"Calendar conflicts · {len(conflicts)}", expanded=True):
            _show_conflicts(conflicts)
    st.caption("Western Health working windows show availability. Overlap with work scheduled inside that window is expected; overlapping fixed appointments are flagged.")
    st.divider()
    event_tab, recurring_tab = st.tabs(["Add or edit an event", "Recurring weekly commitments"])
    with event_tab:
        _event_editor(session, day)
    with recurring_tab:
        _recurring_editor(session)


def _event_editor(session, day):
    events = list(session.scalars(select(Event).order_by(Event.start.desc())))
    lookup = {event.id: event for event in events}
    selected = st.selectbox("Event", [None] + list(lookup), format_func=lambda key: "Create a new event" if key is None else f"#{key} · {lookup[key].start:%d %b %H:%M} · {lookup[key].title}", key="event_editor_select")
    event = lookup.get(selected)
    with st.form("event_editor"):
        title = st.text_input("Event title", value=event.title if event else "")
        a, b = st.columns(2)
        event_day = a.date_input("Event date", value=event.start.date() if event else day)
        end_day = b.date_input("End date", value=event.end.date() if event else day)
        a, b = st.columns(2)
        start_time = a.time_input("Starts at", value=event.start.time() if event else time(10))
        end_time = b.time_input("Ends at", value=event.end.time() if event else time(10, 30))
        a, b = st.columns(2)
        source = a.text_input("Calendar source", value=event.source if event else "Manual")
        status = b.selectbox("Event status", EVENT_STATUSES, index=EVENT_STATUSES.index(event.status) if event else 0)
        kind = st.selectbox("Event type", KINDS, index=KINDS.index(event.kind) if event else 0)
        location = st.text_input("Location", value=event.location if event else "")
        description = st.text_area("Description (synthetic or de-identified only)", value=event.description if event else "")
        submitted = st.form_submit_button("Save event", type="primary")
    if submitted:
        _persist(session, lambda: save_event(session, event_id=selected, title=title, start=datetime.combine(event_day, start_time), end=datetime.combine(end_day, end_time), source=source, status=status, kind=kind, location=location, description=description), "Event saved.")
    if event:
        remove = st.checkbox("I want to remove this individual event.", key=f"delete_event_{event.id}")
        if st.button("Delete event", disabled=not remove):
            _persist(session, lambda: delete_event(session, event.id), "Event removed.")


def _recurring_editor(session):
    recurring = list(session.scalars(select(RecurringCommitment).order_by(RecurringCommitment.weekday, RecurringCommitment.start_time)))
    st.dataframe(pd.DataFrame([{"ID": event.id, "Day": calendar.day_name[event.weekday], "Start": event.start_time.strftime("%H:%M"), "End": event.end_time.strftime("%H:%M"), "Commitment": event.title, "Source": event.source, "Status": event.status, "Active": event.active} for event in recurring]), hide_index=True, use_container_width=True)
    st.caption("Recurring commitments are stored separately from imported calendar events. Friday morning private consultations are provisional; no recurring Friday outpatient practice is seeded.")
    lookup = {event.id: event for event in recurring}
    selected = st.selectbox("Recurring commitment", [None] + list(lookup), format_func=lambda key: "Create a recurring commitment" if key is None else f"#{key} · {calendar.day_name[lookup[key].weekday]} {lookup[key].start_time:%H:%M} · {lookup[key].title}")
    event = lookup.get(selected)
    with st.form("recurring_editor"):
        title = st.text_input("Commitment title", value=event.title if event else "")
        weekday = st.selectbox("Weekday", list(range(7)), format_func=lambda index: calendar.day_name[index], index=event.weekday if event else 0)
        a, b = st.columns(2)
        start = a.time_input("Start time", value=event.start_time if event else time(9))
        end = b.time_input("End time", value=event.end_time if event else time(10))
        a, b = st.columns(2)
        source = a.text_input("Commitment source", value=event.source if event else "Manual")
        status = b.selectbox("Commitment status", EVENT_STATUSES, index=EVENT_STATUSES.index(event.status) if event else 0)
        kind = st.selectbox("Commitment type", KINDS, index=KINDS.index(event.kind) if event else KINDS.index("commitment"))
        location = st.text_input("Commitment location", value=event.location if event else "")
        active = st.checkbox("Active", value=event.active if event else True)
        limited = st.checkbox("Limit to an effective date range", value=bool(event and (event.effective_from or event.effective_until)))
        a, b = st.columns(2)
        effective_from = a.date_input("Effective from", value=event.effective_from if event and event.effective_from else today())
        effective_until = b.date_input("Effective until", value=event.effective_until if event and event.effective_until else today() + timedelta(days=365))
        submitted = st.form_submit_button("Save recurring commitment", type="primary")
    if submitted:
        _persist(session, lambda: save_recurring(session, recurring_id=selected, title=title, weekday=weekday, start_time=start, end_time=end, source=source, status=status, kind=kind, location=location, active=active, effective_from=effective_from if limited else None, effective_until=effective_until if limited else None), "Recurring commitment saved. Deactivate a commitment to remove future occurrences.")


def tasks_page(session, day):
    tasks = list_tasks(session)
    _, config = _config(session)
    a, b, c = st.columns([2, 1, 1])
    query = a.text_input("Search tasks", placeholder="Title, description or source")
    selected_status = b.multiselect("Statuses", STATUSES)
    selected_categories = c.multiselect("Categories", CATEGORIES)
    filtered = [task for task in tasks if (not query or query.lower() in f"{task.title} {task.description} {task.source}".lower()) and (not selected_status or task.status in selected_status) and (not selected_categories or task.category in selected_categories)]
    rows = []
    ordered = rank_tasks(filtered, day, config) + sorted((task for task in filtered if task.status == "Completed"), key=lambda task: task.id)
    for task in ordered:
        score = score_task(task, day, config)
        rows.append({"Task ID": task.id, "Title": task.title, "Description": task.description, "Category": task.category, "Source": task.source, "Created": task.created_date, "Due": task.due_date, "Priority": score.priority, "Score": score.total, "Minutes": task.estimated_minutes, "Status": task.status, "Dependencies": ", ".join(map(str, task.dependencies)), "Last verified": task.last_verified_date})
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    st.caption(f"{len(filtered)} of {len(tasks)} tasks · P0 requires an explicit immediate safety or critical statutory flag. Other priorities are calculated from configurable score rules.")
    st.divider()
    st.subheader("Add or edit a task")
    lookup = {task.id: task for task in tasks}
    selected = st.selectbox("Task to edit", [None] + list(lookup), format_func=lambda key: "Create a new task" if key is None else f"#{key} · {lookup[key].title}")
    task = lookup.get(selected)
    if task:
        _task_card(task, day, config)
    with st.form("task_editor"):
        title = st.text_input("Task title", value=task.title if task else "")
        description = st.text_area("Task description (synthetic or de-identified only)", value=task.description if task else "")
        a, b, c = st.columns(3)
        category = a.selectbox("Category", CATEGORIES, index=CATEGORIES.index(task.category) if task else 2)
        source = b.text_input("Task source", value=task.source if task else "Manual")
        status = c.selectbox("Task status", STATUSES, index=STATUSES.index(task.status) if task else 0)
        a, b, c = st.columns(3)
        created = a.date_input("Created date", value=task.created_date if task else day)
        due_enabled = b.checkbox("Has a due date", value=bool(task and task.due_date))
        due = b.date_input("Due date", value=task.due_date if task and task.due_date else day)
        minutes = c.number_input("Estimated duration (minutes)", min_value=1, max_value=10000, value=task.estimated_minutes if task else 30, step=5)
        safety = st.checkbox("P0: immediate safety or critical statutory obligation", value=task.safety_critical if task else False)
        a, b, c = st.columns(3)
        consequences = a.number_input("Consequences (0–30)", 0, 30, task.consequences if task else 10)
        importance = b.number_input("Importance (0–20)", 0, 20, task.importance if task else 10)
        impact = c.number_input("Dependency impact (0–10)", 0, 10, task.dependency_impact if task else 0)
        dependency_options = [key for key in lookup if key != selected]
        dependencies = st.multiselect("Depends on these tasks being completed", dependency_options, default=task.dependencies if task else [], format_func=lambda key: f"#{key} · {lookup[key].title}")
        a, b = st.columns(2)
        verified_enabled = a.checkbox("Has a last verified date", value=bool(task and task.last_verified_date))
        verified = a.date_input("Last verified date", value=task.last_verified_date if task and task.last_verified_date else day)
        deferred_to = b.date_input("Defer until (used for Deferred status)", value=task.deferred_to if task and task.deferred_to else day + timedelta(days=1))
        submitted = st.form_submit_button("Save task", type="primary")
    if submitted:
        _persist(session, lambda: save_task(session, task_id=selected, title=title, description=description, category=category, source=source, created_date=created, due_date=due if due_enabled else None, estimated_minutes=int(minutes), status=status, dependencies=dependencies, last_verified_date=verified if verified_enabled else None, safety_critical=safety, consequences=int(consequences), importance=int(importance), dependency_impact=int(impact), deferred_to=deferred_to if status == "Deferred" else None), "Task saved. Priority is recalculated from its current dates and impact.")


def import_page(session, day):
    st.markdown('<div class="notice">Manual entry and reviewed CSV imports are available. Outlook and Gmail synchronisation and screenshot extraction are awaiting implementation.</div>', unsafe_allow_html=True)
    csv_tab, screenshot_tab = st.tabs(["CSV import", "Screenshot review"])
    with csv_tab:
        st.write("Import de-identified calendar events from an explicit CSV. Keep the same source and external ID when updating an event so the import updates it instead of duplicating it.")
        template = "external_id,title,start,end,source,status,kind,location,description\nexample-review,Demo governance review,2026-10-19T11:00:00+11:00,2026-10-19T11:30:00+11:00,Western Health,Confirmed,meeting,Demo meeting room,Synthetic example\n"
        st.download_button("Download CSV template", template, "calendar_template.csv", "text/csv")
        st.caption("Every event needs an explicit date, start, end, source and stable external ID. Times use Australia/Melbourne; explicit ISO UTC offsets are supported.")
        upload = st.file_uploader("Calendar CSV", type=["csv"])
        if upload:
            raw = upload.getvalue()
            if len(raw) > 2_000_000:
                st.error("CSV files must be 2 MB or smaller.")
            else:
                try:
                    events = parse_calendar_csv(raw.decode("utf-8-sig"))
                    st.dataframe(pd.DataFrame(events), hide_index=True, use_container_width=True)
                    reviewed = st.checkbox("I have reviewed these events and confirm they contain no identifiable patient information.", key=f"csv_review_{hashlib.sha256(raw).hexdigest()}")
                    if st.button("Approve and import CSV", disabled=not reviewed):
                        result = _persist(session, lambda: import_events(session, events), "CSV import complete. Reimporting the same event IDs updates existing records.", rerun=False)
                        if result is not None:
                            st.write(result)
                except (ValueError, TypeError, UnicodeDecodeError) as exc:
                    st.error(f"CSV needs correction: {exc}")
    with screenshot_tab:
        st.warning("Image extraction is disabled. No approved extraction service is configured. Screenshots are never sent to an external service by this prototype.")
        st.caption("Only upload a synthetic or de-identified screenshot. The preview stays in this Streamlit session; this interface does not save the uploaded image to the database.")
        image = st.file_uploader("Preview a calendar screenshot", type=["png", "jpg", "jpeg"], key="calendar_screenshot")
        if image:
            if image.size <= 10_000_000:
                try:
                    st.image(image, caption="Review only — enter the event details manually below.", use_container_width=True)
                except (OSError, ValueError):
                    st.error("This file could not be displayed as an image. Upload a valid PNG or JPEG screenshot.")
            else:
                st.error("Use an image smaller than 10 MB.")
        st.button("Extract calendar events", disabled=True, help="Requires an explicitly configured, approved image extraction service.")
        st.subheader("Manually transcribe a reviewed event")
        _event_editor(session, day)


def settings_page(session):
    scheduler, priority = _config(session)
    st.subheader("Daily scheduling targets")
    with st.form("scheduler_settings"):
        a, b, c = st.columns(3)
        patient = a.number_input("Patient-care target (minutes)", 0, 1440, scheduler.patient_minutes, step=5)
        academic = b.number_input("Academic-reading target (minutes)", 0, 1440, scheduler.academic_minutes, step=5)
        lunch = c.number_input("Lunch break (minutes)", 0, 1440, scheduler.lunch_minutes, step=5)
        a, b, c = st.columns(3)
        lunch_start = a.time_input("Preferred lunch start", value=time.fromisoformat(scheduler.lunch_start))
        transition = b.number_input("Transitions (minutes)", 0, 1440, scheduler.transition_minutes)
        minimum = c.number_input("Minimum work block (minutes)", 1, 390, scheduler.min_block_minutes)
        submitted = st.form_submit_button("Save scheduling preferences", type="primary")
    if submitted:
        config = dict(patient_minutes=patient, academic_minutes=academic, lunch_minutes=lunch, lunch_start=lunch_start.strftime("%H:%M"), transition_minutes=transition, min_block_minutes=minimum)
        _persist(session, lambda: save_settings(session, "scheduler", config), "Scheduling preferences saved.")
    st.subheader("Priority scoring rules")
    st.write("Immediate safety and critical statutory tasks receive P0. Other tasks score up to 100: deadline 30, consequences 30, importance 20, dependency impact 10 and age 10.")
    st.caption("Deadline rules are pairs of maximum days until due and points. Age earns one point per configured number of days, capped at 10. Scores include a full explanation on task cards.")
    with st.form("priority_settings"):
        rules_text = st.text_area("Priority configuration (JSON)", value=json.dumps(asdict(priority), indent=2), height=270)
        submitted = st.form_submit_button("Save priority rules")
    if submitted:
        try:
            raw = json.loads(rules_text)
            config = PriorityConfig(**raw)
            _persist(session, lambda: save_settings(session, "priority", asdict(config)), "Priority rules saved.")
        except (ValueError, TypeError) as exc:
            st.error(f"Invalid priority configuration: {exc}")
    st.divider()
    st.subheader("Privacy, access and deployment")
    st.write("This local prototype is for synthetic or de-identified information. Do not enter identifiable patient information, clinical records, credentials or real email content. No Western Health or private practice clinical system is connected.")
    st.write("Access logging records application access and key changes without task descriptions or clinical content. The README documents authentication, secret configuration and SQLite backup and restore.")
    st.info("Real-world deployment requires authentication, secure transport and secrets management, tested backups, appropriate hosting access controls and explicit approval for each external clinical integration.")
    with st.expander("Functions awaiting implementation"):
        st.write("• Approved screenshot extraction with review and reconciliation\n• Outlook / Microsoft Graph calendar synchronisation\n• Gmail correspondence and action-item synchronisation\n• Clinical-system integrations, subject to explicit approval\n• Multi-user roles, enterprise identity integration and production deployment hardening\n• Automated encrypted backup storage and monitored restore drills")
