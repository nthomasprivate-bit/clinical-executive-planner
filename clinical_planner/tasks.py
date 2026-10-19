"""Validated task register operations. Priority is derived, never a stale field."""
from datetime import date, timedelta
from sqlalchemy import select
from sqlalchemy.orm import Session
from clinical_planner.models import Task, CATEGORIES, STATUSES, utcnow
from clinical_planner.timeutils import today

def list_tasks(session: Session) -> list[Task]:
    return list(session.scalars(select(Task).order_by(Task.id)))

def _validate_dependencies(task_id, dependency_ids, tasks):
    by_id = {task.id: list(task.dependencies) for task in tasks}
    if any(type(value) is not int or value not in by_id for value in dependency_ids):
        raise ValueError("Dependencies must refer to existing task IDs.")
    if task_id in dependency_ids:
        raise ValueError("A task cannot depend on itself.")
    if task_id is not None:
        by_id[task_id] = dependency_ids
    visiting, visited = set(), set()
    def visit(node):
        if node in visiting:
            raise ValueError("Dependencies would create a cycle.")
        if node in visited:
            return
        visiting.add(node)
        for target in by_id.get(node, []):
            visit(target)
        visiting.remove(node)
        visited.add(node)
    for node in by_id:
        visit(node)

def save_task(session: Session, task_id: int | None = None, **fields) -> Task:
    task = session.get(Task, task_id) if task_id is not None else None
    if task_id is not None and task is None:
        raise ValueError("Task no longer exists.")
    defaults = dict(title="", description="", category="Administrative", source="Manual", created_date=today(), due_date=None, estimated_minutes=30, status="Inbox", dependencies=[], last_verified_date=today(), safety_critical=False, consequences=10, importance=10, dependency_impact=0, deferred_to=None)
    if task:
        defaults.update({key: getattr(task, key) for key in defaults})
    unknown = set(fields) - set(defaults)
    if unknown:
        raise ValueError(f"Unknown task fields: {', '.join(sorted(unknown))}")
    values = defaults | fields
    if any(not isinstance(values[key], str) for key in ("title", "source", "description")):
        raise ValueError("Title, source and description must be text.")
    values["title"] = values["title"].strip()
    values["source"] = values["source"].strip()
    if not values["title"] or len(values["title"]) > 200:
        raise ValueError("Title must contain 1–200 characters.")
    if not values["source"] or len(values["source"]) > 80:
        raise ValueError("Source must contain 1–80 characters.")
    if len(values["description"]) > 20000:
        raise ValueError("Description must be at most 20000 characters.")
    if type(values["safety_critical"]) is not bool:
        raise ValueError("The immediate safety flag must be true or false.")
    if values["category"] not in CATEGORIES or values["status"] not in STATUSES:
        raise ValueError("Invalid task category or status.")
    if type(values["estimated_minutes"]) is not int or not 1 <= values["estimated_minutes"] <= 10000:
        raise ValueError("Estimated duration must be 1–10000 whole minutes.")
    for key, maximum in (("consequences", 30), ("importance", 20), ("dependency_impact", 10)):
        if type(values[key]) is not int or not 0 <= values[key] <= maximum:
            raise ValueError(f"{key} must be between 0 and {maximum}.")
    for key in ("created_date", "due_date", "last_verified_date", "deferred_to"):
        if values[key] is not None and type(values[key]) is not date:
            raise ValueError(f"{key} must be a date.")
    if values["created_date"] is None:
        raise ValueError("Created date is required.")
    if not isinstance(values["dependencies"], (list, tuple)) or any(type(value) is not int for value in values["dependencies"]):
        raise ValueError("Dependencies must be a list of task IDs.")
    if len(values["dependencies"]) != len(set(values["dependencies"])):
        raise ValueError("Duplicate dependency IDs are not allowed.")
    _validate_dependencies(task_id, values["dependencies"], list_tasks(session))
    values["dependencies"] = list(values["dependencies"])
    old_status = task.status if task else None
    if values["status"] == "Deferred" and values["deferred_to"] is None:
        values["deferred_to"] = today() + timedelta(days=1)
    if values["status"] != "Deferred":
        values["deferred_to"] = None
    if task is None:
        task = Task(**values)
        session.add(task)
    else:
        for key, value in values.items():
            setattr(task, key, value)
    if values["status"] == "Completed" and old_status != "Completed":
        task.completed_at = utcnow()
    elif values["status"] != "Completed":
        task.completed_at = None
    session.flush()
    from clinical_planner.security import audit
    audit(session, "task.updated" if task_id else "task.created", f"task:{task.id}")
    return task
