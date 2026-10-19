"""Planner invariants: protected commitments, unmet targets and safe task eligibility."""
from datetime import date, datetime, time, timedelta
from types import SimpleNamespace

from clinical_planner.models import Task
from clinical_planner.scheduler import PlannerConfig, propose_plan


DAY = date(2026, 10, 19)


def at(hour, minute=0):
    return datetime.combine(DAY, time(hour, minute))


def occurrence(start, end, *, kind="meeting", source="Western Health", task_id=None,
               status="Confirmed"):
    return SimpleNamespace(start=start, end=end, kind=kind, source=source,
                           title="Synthetic commitment", status=status, task_id=task_id)


def window(start=None, end=None):
    return occurrence(start or at(9), end or at(15, 30), kind="availability")


def task(task_id=1, **changes):
    values = dict(id=task_id, title=f"Synthetic task {task_id}", description="",
                  category="Administrative", source="Synthetic", created_date=DAY,
                  due_date=None, estimated_minutes=30, status="Ready", dependencies=[],
                  last_verified_date=DAY, safety_critical=False, consequences=0,
                  importance=0, dependency_impact=0, completed_at=None, deferred_to=None)
    values.update(changes)
    return Task(**values)


def minutes(block):
    return int((block.end - block.start).total_seconds() // 60)


def overlaps(left, right):
    return left.start < right.end and right.start < left.end


def paperwork_config(**changes):
    values = dict(patient_minutes=0, academic_minutes=0, lunch_minutes=0,
                  transition_minutes=0, min_block_minutes=15)
    values.update(changes)
    return PlannerConfig(**values)


def test_normal_day_meets_targets_and_protects_lunch_and_transitions():
    plan = propose_plan(DAY, [window()], [task(estimated_minutes=45)])
    assert plan.day == DAY
    assert plan.patient_minutes == 120
    assert plan.academic_minutes == 60
    assert plan.unmet_minutes["patient_care"] == 0
    assert plan.unmet_minutes["academic_reading"] == 0
    assert sum(minutes(block) for block in plan.blocks if block.kind == "lunch") == 30
    assert any(block.kind == "transition" for block in plan.blocks)
    assert plan.paperwork_minutes > 0
    for index, block in enumerate(plan.blocks):
        assert at(9) <= block.start < block.end <= at(15, 30)
        assert all(not overlaps(block, other) for other in plan.blocks[index + 1:])


def test_confirmed_tentative_meetings_and_private_practice_all_block_planning():
    blockers = [
        occurrence(at(9), at(10)),
        occurrence(at(11), at(11, 30), status="Tentative"),
        occurrence(at(15), at(16), kind="commitment", source="Sunshine Private"),
    ]
    plan = propose_plan(DAY, [window(), *blockers], [task(estimated_minutes=90)])
    for block in plan.blocks:
        assert at(9) <= block.start < block.end <= at(15, 30)
        assert all(not overlaps(block, fixed) for fixed in blockers)


def test_short_day_reports_unmet_minutes_and_never_moves_fixed_meeting():
    meeting = occurrence(at(10), at(15, 30))
    plan = propose_plan(DAY, [window(), meeting], [],
                        PlannerConfig(lunch_minutes=0, transition_minutes=0))
    assert plan.patient_minutes + plan.academic_minutes <= 60
    assert plan.unmet_minutes["patient_care"] + plan.unmet_minutes["academic_reading"] >= 120
    assert plan.suggestions
    assert meeting.start == at(10) and meeting.end == at(15, 30)
    assert all(not overlaps(block, meeting) for block in plan.blocks)


def test_no_working_window_has_no_blocks_and_reports_both_daily_targets():
    plan = propose_plan(DAY, [], [task()])
    assert not plan.blocks
    assert plan.unmet_minutes["patient_care"] == 120
    assert plan.unmet_minutes["academic_reading"] == 60
    assert plan.suggestions


def test_existing_patient_and_academic_blocks_reduce_new_target_allocations():
    existing = [
        occurrence(at(9), at(10), kind="patient_care"),
        occurrence(at(10), at(11), kind="academic_reading"),
    ]
    plan = propose_plan(DAY, [window(), *existing], [],
                        PlannerConfig(lunch_minutes=0, transition_minutes=0))
    assert sum(minutes(block) for block in plan.blocks if block.kind == "patient_care") == 60
    assert sum(minutes(block) for block in plan.blocks if block.kind == "academic_reading") == 0
    assert plan.patient_minutes == 120
    assert plan.academic_minutes == 60


def test_private_care_outside_working_window_does_not_satisfy_wh_target():
    private = occurrence(at(8), at(8, 45), kind="patient_care", source="Sunshine Private")
    plan = propose_plan(DAY, [window(), private], [],
                        PlannerConfig(lunch_minutes=0, transition_minutes=0))
    assert sum(minutes(block) for block in plan.blocks if block.kind == "patient_care") == 120
    assert plan.patient_minutes == 120


def test_existing_partial_task_allocation_is_deducted_from_estimate():
    existing = occurrence(at(9), at(9, 30), kind="paperwork", task_id=1)
    plan = propose_plan(DAY, [window(), existing], [task(estimated_minutes=60)],
                        paperwork_config())
    assert sum(minutes(block) for block in plan.blocks if block.task_id == 1) == 30
    assert all(not overlaps(block, existing) for block in plan.blocks)


def test_tasks_with_unfinished_or_missing_dependencies_are_not_scheduled():
    prerequisite = task(1)
    dependent = task(2, safety_critical=True, dependencies=[1])
    missing_dependency = task(3, dependencies=[999])
    plan = propose_plan(DAY, [window()], [dependent, prerequisite, missing_dependency],
                        paperwork_config())
    scheduled_ids = {block.task_id for block in plan.blocks}
    assert 1 in scheduled_ids
    assert 2 not in scheduled_ids
    assert 3 not in scheduled_ids


def test_completed_dependency_unlocks_dependent_task():
    completed = task(1, status="Completed")
    dependent = task(2, dependencies=[1])
    plan = propose_plan(DAY, [window()], [completed, dependent], paperwork_config())
    scheduled_ids = {block.task_id for block in plan.blocks}
    assert 2 in scheduled_ids
    assert 1 not in scheduled_ids


def test_status_and_deferral_dates_control_task_eligibility():
    tasks = [
        task(1, status="Inbox"), task(2, status="Waiting"),
        task(3, status="Completed"),
        task(4, status="Deferred", deferred_to=DAY + timedelta(days=1)),
        task(5, status="Deferred", deferred_to=DAY),
        task(6, status="In Progress"), task(7, status="Scheduled"),
    ]
    plan = propose_plan(DAY, [window()], tasks, paperwork_config())
    scheduled_ids = {block.task_id for block in plan.blocks}
    assert scheduled_ids.issuperset({5, 6, 7})
    assert scheduled_ids.isdisjoint({1, 2, 3, 4})


def test_priority_determines_which_task_uses_limited_paperwork_capacity():
    low = task(1, estimated_minutes=30)
    critical = task(2, estimated_minutes=30, safety_critical=True)
    plan = propose_plan(DAY, [window(at(9), at(9, 30))], [low, critical],
                        paperwork_config())
    assert [block.task_id for block in plan.blocks if block.kind == "paperwork"] == [2]
