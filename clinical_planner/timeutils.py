"""Explicit Melbourne dates and DST validation; never infer missing dates."""
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

ZONE = ZoneInfo("Australia/Melbourne")

def today() -> date:
    return datetime.now(ZONE).date()

def now_local() -> datetime:
    return datetime.now(ZONE).replace(tzinfo=None)

def local_datetime(value: datetime) -> datetime:
    if value.tzinfo is not None:
        value = value.astimezone(ZONE).replace(tzinfo=None, fold=0)
    first = value.replace(tzinfo=ZONE, fold=0)
    second = value.replace(tzinfo=ZONE, fold=1)
    valid = [x for x in (first, second) if x.astimezone(timezone.utc).astimezone(ZONE).replace(tzinfo=None) == value]
    if not valid:
        raise ValueError("Nonexistent Melbourne time during daylight-saving transition.")
    if len(valid) == 2 and first.utcoffset() != second.utcoffset():
        raise ValueError("Ambiguous Melbourne time in a repeated daylight-saving hour. Repeated-hour times are currently unsupported, even with an explicit UTC offset.")
    return value

def parse_datetime(value: str) -> datetime:
    if "T" not in value and " " not in value:
        raise ValueError("A full ISO date and time are required.")
    return local_datetime(datetime.fromisoformat(value.strip().replace("Z", "+00:00")))
