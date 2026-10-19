"""Transparent, configurable priority scores; no external services are used."""
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable


@dataclass(frozen=True)
class PriorityConfig:
    # First matching deadline horizon wins; overdue tasks use the first band.
    deadline_rules: tuple = ((0, 30), (1, 28), (3, 25), (7, 20), (14, 12), (30, 5))
    age_days_per_point: int = 7
    p1_min: int = 80
    p2_min: int = 60
    p3_min: int = 35

    def __post_init__(self):
        rules = tuple(tuple(rule) for rule in self.deadline_rules)
        if not rules or any(len(rule) != 2 for rule in rules):
            raise ValueError("Deadline rules need (days until due, points) pairs.")
        if any(not isinstance(day, int) or not isinstance(points, int) or day < 0 or not 0 <= points <= 30 for day, points in rules):
            raise ValueError("Deadline horizons must be nonnegative integers and points must be 0–30.")
        if tuple(sorted(rules)) != rules or len({rule[0] for rule in rules}) != len(rules):
            raise ValueError("Deadline horizons must be unique and ascending.")
        if not isinstance(self.age_days_per_point, int) or self.age_days_per_point <= 0:
            raise ValueError("Age days per point must be a positive integer.")
        if not 0 <= self.p3_min < self.p2_min < self.p1_min <= 100:
            raise ValueError("Priority thresholds must increase between 0 and 100.")
        object.__setattr__(self, "deadline_rules", rules)


@dataclass(frozen=True)
class PriorityScore:
    priority: str
    total: int
    components: dict[str, int]
    explanation: list[str]


def _bounded(value: Any, maximum: int) -> int:
    return max(0, min(maximum, int(value or 0)))


def score_task(task, on_date: date, config: PriorityConfig | None = None) -> PriorityScore:
    config = config or PriorityConfig()
    due = getattr(task, "due_date", None)
    days_to_due = (due - on_date).days if due else None
    deadline = 0
    if days_to_due is not None:
        deadline = next((points for days, points in config.deadline_rules if days_to_due <= days), 0)
    created = getattr(task, "created_date", None) or on_date
    age_days = max(0, (on_date - created).days)
    components = {
        "deadline_urgency": deadline,
        "consequences": _bounded(getattr(task, "consequences", 0), 30),
        "importance": _bounded(getattr(task, "importance", 0), 20),
        "dependencies": _bounded(getattr(task, "dependency_impact", 0), 10),
        "age": min(10, age_days // config.age_days_per_point),
    }
    total = sum(components.values())
    if getattr(task, "safety_critical", False):
        priority = "P0"
    elif total >= config.p1_min:
        priority = "P1"
    elif total >= config.p2_min:
        priority = "P2"
    elif total >= config.p3_min:
        priority = "P3"
    else:
        priority = "P4"
    due_text = "no due date" if due is None else f"due {due.isoformat()} ({days_to_due} days from planning date)"
    explanation = [
        f"Deadline urgency: {deadline}/30; {due_text}; configured deadline bands apply.",
        f"Consequences: {components['consequences']}/30; entered consequence assessment, bounded to 0–30.",
        f"Importance: {components['importance']}/20; entered importance assessment, bounded to 0–20.",
        f"Dependencies: {components['dependencies']}/10; entered impact on other work, bounded to 0–10.",
        f"Age: {components['age']}/10; {age_days} days old, one point per {config.age_days_per_point} days.",
    ]
    if priority == "P0":
        explanation.insert(0, "P0 override: immediate safety or critical statutory obligation; numeric score is still shown for transparency.")
    else:
        explanation.append(f"Total {total}/100 → {priority}; thresholds P1 ≥{config.p1_min}, P2 ≥{config.p2_min}, P3 ≥{config.p3_min}, otherwise P4.")
    return PriorityScore(priority, total, components, explanation)


def rank_tasks(tasks: Iterable, on_date: date, config: PriorityConfig | None = None) -> list:
    """Rank open tasks deterministically; eligibility for planning is separate."""
    config = config or PriorityConfig()
    def key(task):
        score = score_task(task, on_date, config)
        return (0 if score.priority == "P0" else 1, -score.total,
                getattr(task, "due_date", None) or date.max,
                getattr(task, "created_date", None) or on_date,
                getattr(task, "id", None) or 0)
    return sorted((task for task in tasks if getattr(task, "status", None) != "Completed"), key=key)
