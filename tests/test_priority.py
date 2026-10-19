"""Meaningful priority boundaries, explanations and configurable scoring rules."""
from datetime import date, timedelta

import pytest

from clinical_planner.models import Task
from clinical_planner.priority import PriorityConfig, rank_tasks, score_task


TODAY = date(2026, 10, 19)


def task(task_id=1, **changes):
    values = dict(
        id=task_id, title=f"Synthetic task {task_id}", description="Synthetic example",
        category="Administrative", source="Synthetic", created_date=TODAY,
        due_date=None, estimated_minutes=30, status="Ready", dependencies=[],
        last_verified_date=TODAY, safety_critical=False, consequences=0,
        importance=0, dependency_impact=0, completed_at=None, deferred_to=None,
    )
    values.update(changes)
    return Task(**values)


def test_safety_critical_obligation_is_p0_even_with_zero_numeric_score():
    score = score_task(task(safety_critical=True), TODAY)
    assert score.priority == "P0"
    assert score.explanation


@pytest.mark.parametrize(
    "consequences,importance,dependency_impact,age,expected_total,expected_priority",
    [
        (10, 20, 10, 70, 80, "P1"),
        (9, 20, 10, 70, 79, "P2"),
        (0, 20, 0, 70, 60, "P2"),
        (0, 19, 0, 70, 59, "P3"),
        (0, 5, 0, 0, 35, "P3"),
        (0, 4, 0, 0, 34, "P4"),
    ],
)
def test_priority_band_boundaries(consequences, importance, dependency_impact, age,
                                  expected_total, expected_priority):
    item = task(due_date=TODAY, consequences=consequences, importance=importance,
                dependency_impact=dependency_impact,
                created_date=TODAY - timedelta(days=age))
    score = score_task(item, TODAY)
    assert score.total == expected_total
    assert score.priority == expected_priority


def test_maximum_score_is_capped_and_components_explain_total():
    item = task(due_date=TODAY - timedelta(days=100), consequences=30,
                importance=20, dependency_impact=10,
                created_date=TODAY - timedelta(days=700))
    score = score_task(item, TODAY)
    assert score.total == 100
    assert score.components == {
        "deadline_urgency": 30, "consequences": 30, "importance": 20,
        "dependencies": 10, "age": 10,
    }
    assert sum(score.components.values()) == score.total
    assert score.explanation


def test_deadline_urgency_increases_towards_due_date_and_missing_date_has_zero():
    due_now = score_task(task(due_date=TODAY), TODAY)
    due_week = score_task(task(due_date=TODAY + timedelta(days=7)), TODAY)
    due_later = score_task(task(due_date=TODAY + timedelta(days=60)), TODAY)
    undated = score_task(task(), TODAY)
    assert due_now.components["deadline_urgency"] > due_week.components["deadline_urgency"]
    assert due_week.components["deadline_urgency"] > due_later.components["deadline_urgency"]
    assert undated.components["deadline_urgency"] == 0


def test_rules_and_priority_thresholds_are_configurable():
    config = PriorityConfig(deadline_rules=((0, 10), (10, 4)), age_days_per_point=2,
                            p1_min=40, p2_min=25, p3_min=10)
    score = score_task(task(due_date=TODAY, created_date=TODAY - timedelta(days=8)),
                       TODAY, config)
    assert score.components["deadline_urgency"] == 10
    assert score.components["age"] == 4
    assert score.priority == "P3"


def test_rank_uses_p0_then_score_and_excludes_completed_tasks():
    low = task(1, consequences=1)
    high = task(2, due_date=TODAY, consequences=30, importance=20)
    critical = task(3, safety_critical=True)
    completed = task(4, safety_critical=True, status="Completed")
    assert [item.id for item in rank_tasks([low, completed, high, critical], TODAY)] == [3, 2, 1]
