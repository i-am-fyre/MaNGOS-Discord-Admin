"""World-event schedule maths, mirroring ``GameEventMgr::CheckOneGameEvent``.

An event (row of the world ``game_event`` table) is active when::

    start_time < now < end_time  and  (now - start_time) % occurence < length

where ``occurence`` and ``length`` are in minutes. Times in the table are the
world server's local time.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any, Iterable


@dataclass(frozen=True)
class GameEvent:
    entry: int
    start: datetime | None
    end: datetime | None
    occurence: int  # minutes between starts
    length: int  # minutes
    holiday: int
    description: str

    @property
    def schedulable(self) -> bool:
        return bool(self.start and self.end and self.occurence > 0 and self.length > 0)


@dataclass(frozen=True)
class Occurrence:
    event: GameEvent
    start: datetime
    end: datetime

    @property
    def key(self) -> int:
        return int(self.start.timestamp())


def _as_aware(value: Any, tz: tzinfo) -> datetime | None:
    # PyMySQL returns zero dates ("0000-00-00 00:00:00") as strings.
    if not isinstance(value, datetime):
        return None
    if value.year < 1971:
        return None
    aware = value.replace(tzinfo=tz) if value.tzinfo is None else value
    # mangosd converts to time_t once and then adds plain seconds, so do the
    # maths in UTC too; wall-clock arithmetic would drift an hour across DST.
    return aware.astimezone(timezone.utc)


def from_row(row: dict[str, Any], tz: tzinfo) -> GameEvent:
    return GameEvent(
        entry=int(row["entry"]),
        start=_as_aware(row.get("start_time"), tz),
        end=_as_aware(row.get("end_time"), tz),
        occurence=int(row.get("occurence") or 0),
        length=int(row.get("length") or 0),
        holiday=int(row.get("holiday") or 0),
        description=str(row.get("description") or f"Event {row['entry']}").strip(),
    )


def _clip(event: GameEvent, start: datetime) -> Occurrence:
    end = start + timedelta(minutes=event.length)
    assert event.end is not None
    return Occurrence(event, start, min(end, event.end))


def occurrence_at(event: GameEvent, now: datetime) -> Occurrence | None:
    """The occurrence running at ``now``, if any."""
    if not event.schedulable:
        return None
    assert event.start is not None and event.end is not None
    if not (event.start < now < event.end):
        return None
    period = timedelta(minutes=event.occurence)
    cycles = (now - event.start) // period
    start = event.start + cycles * period
    occ = _clip(event, start)
    return occ if now < occ.end else None


def upcoming(event: GameEvent, now: datetime, until: datetime, limit: int = 20) -> list[Occurrence]:
    """Occurrences that start after ``now`` and no later than ``until``."""
    if not event.schedulable:
        return []
    assert event.start is not None and event.end is not None
    period = timedelta(minutes=event.occurence)
    if now < event.start:
        start = event.start
    else:
        cycles = (now - event.start) // period + 1
        start = event.start + cycles * period
    out: list[Occurrence] = []
    while start <= until and start < event.end and len(out) < limit:
        out.append(_clip(event, start))
        start += period
    return out


def next_start(event: GameEvent, now: datetime) -> datetime | None:
    if not event.schedulable:
        return None
    assert event.end is not None
    found = upcoming(event, now, event.end, limit=1)
    return found[0].start if found else None


def is_announceable(event: GameEvent, *, only_holidays: bool, include: Iterable[int], exclude: Iterable[int]) -> bool:
    if event.entry in set(exclude):
        return False
    if event.entry in set(include):
        return True
    if only_holidays:
        return event.holiday != 0
    return True


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
