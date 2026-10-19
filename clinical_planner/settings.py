"""Validated, persisted scoring and scheduling configuration."""
from copy import deepcopy
from dataclasses import asdict
from clinical_planner.models import Setting
from clinical_planner.priority import PriorityConfig
from clinical_planner.scheduler import PlannerConfig

DEFAULT_SCHEDULER = asdict(PlannerConfig())
DEFAULT_PRIORITY = asdict(PriorityConfig())

def get_settings(session, key: str) -> dict:
    defaults = {"scheduler": DEFAULT_SCHEDULER, "priority": DEFAULT_PRIORITY}
    if key not in defaults:
        raise ValueError("Unknown configuration group.")
    stored = session.get(Setting, key)
    return deepcopy(stored.value if stored is not None else defaults[key])

def save_settings(session, key: str, value: dict):
    if key == "scheduler":
        validated = asdict(PlannerConfig(**value))
    elif key == "priority":
        validated = asdict(PriorityConfig(**value))
    else:
        raise ValueError("Unknown configuration group.")
    stored = session.get(Setting, key)
    if stored is None:
        stored = Setting(key=key, value=validated)
        session.add(stored)
    else:
        stored.value = validated
    from clinical_planner.security import audit
    audit(session, "settings.updated", f"settings:{key}")
    session.flush()
    return stored
