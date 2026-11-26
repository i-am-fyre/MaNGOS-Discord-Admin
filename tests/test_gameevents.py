from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from mangosbot import gameevents
from mangosbot.gameevents import GameEvent

UTC = timezone.utc


def midsummer():
    # Row 1 of mangoszero game_event: yearly (525600 min), 14 days long.
    return gameevents.from_row(
        {"entry": 1, "start_time": datetime(2022, 6, 20, 20, 0), "end_time": datetime(2037, 12, 31, 22, 59, 59),
         "occurence": 525600, "length": 20160, "holiday": 341, "description": "Midsummer Fire Festival"},
        UTC,
    )


def test_active_occurrence_uses_fixed_365_day_periods():
    ev = midsummer()
    occ = gameevents.occurrence_at(ev, datetime(2026, 6, 25, tzinfo=UTC))
    assert occ is not None
    # 4 * 365 days after 2022-06-20 (2024 was a leap year, mangos doesn't care).
    assert occ.start == datetime(2026, 6, 19, 20, 0, tzinfo=UTC)
    assert occ.end == occ.start + timedelta(days=14)


def test_inactive_between_occurrences_and_next_start():
    ev = midsummer()
    now = datetime(2026, 8, 1, tzinfo=UTC)
    assert gameevents.occurrence_at(ev, now) is None
    assert gameevents.next_start(ev, now) == datetime(2027, 6, 19, 20, 0, tzinfo=UTC)
    soon = gameevents.upcoming(ev, now, now + timedelta(days=400))
    assert [o.start.year for o in soon] == [2027]


def test_occurrence_is_clipped_to_event_end():
    ev = GameEvent(9, datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 1, 12, tzinfo=UTC), 1440, 1440, 0, "x")
    occ = gameevents.occurrence_at(ev, datetime(2026, 1, 1, 6, tzinfo=UTC))
    assert occ.end == datetime(2026, 1, 1, 12, tzinfo=UTC)


def test_unschedulable_rows():
    row = {"entry": 17, "start_time": "0000-00-00 00:00:00", "end_time": None, "occurence": 525600,
           "length": 1, "holiday": 0, "description": "Scourge Invasion"}
    ev = gameevents.from_row(row, UTC)
    assert not ev.schedulable
    assert gameevents.occurrence_at(ev, datetime.now(UTC)) is None
    assert gameevents.upcoming(ev, datetime.now(UTC), datetime.now(UTC) + timedelta(days=30)) == []


def test_times_are_absolute_across_dst():
    # A daily noon event defined in London time keeps its UTC time all year,
    # exactly like mangosd's time_t arithmetic (it drifts in wall-clock time).
    london = ZoneInfo("Europe/London")
    ev = gameevents.from_row({"entry": 2, "start_time": datetime(2026, 1, 1, 12, 0), "end_time": datetime(2030, 1, 1),
                              "occurence": 1440, "length": 60, "holiday": 0, "description": "Noon"}, london)
    occ = gameevents.occurrence_at(ev, datetime(2026, 7, 1, 12, 30, tzinfo=UTC))
    assert occ is not None and occ.start == datetime(2026, 7, 1, 12, 0, tzinfo=UTC)


def test_announce_filter():
    ev = midsummer()
    assert gameevents.is_announceable(ev, only_holidays=True, include=[], exclude=[])
    assert not gameevents.is_announceable(ev, only_holidays=True, include=[], exclude=[1])
    plain = GameEvent(27, ev.start, ev.end, 1440, 720, 0, "Nights")
    assert not gameevents.is_announceable(plain, only_holidays=True, include=[], exclude=[])
    assert gameevents.is_announceable(plain, only_holidays=True, include=[27], exclude=[])
