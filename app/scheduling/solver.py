"""Slot allocation as a pure function: crews, existing bookings and a job in, a day-by-day plan out.

Greedy by design: earliest start date wins, ties go to the crew with the lightest next two weeks.
Swap for OR-Tools once travel time and many crews matter.
"""
import math
from dataclasses import dataclass
from datetime import date, timedelta

WORKDAYS = (0, 1, 2, 3, 4, 5)  # Monday to Saturday
DAY_START_HOUR = 8.0


@dataclass(frozen=True)
class CrewInfo:
    id: str
    skills: frozenset[str]
    daily_hours: float = 8.0


@dataclass(frozen=True)
class DayPlan:
    day: date
    start_hour: float
    hours: float


@dataclass(frozen=True)
class Proposal:
    crew_id: str
    days: tuple[DayPlan, ...]

    @property
    def start_date(self) -> date:
        return self.days[0].day


def estimate_hours(panel_kw: float) -> float:
    """Placeholder: about 2 crew-hours per kW of panels, minimum half a day. Calibrate from completed jobs."""
    return max(4.0, math.ceil(panel_kw * 2.0))


def _next_workday(d: date) -> date:
    while d.weekday() not in WORKDAYS:
        d += timedelta(days=1)
    return d


def _fit(crew: CrewInfo, hours: float, start: date, booked: dict[tuple[str, date], float]) -> tuple[DayPlan, ...] | None:
    remaining, day, plan = hours, start, []
    while remaining > 0:
        day = _next_workday(day)
        chunk = min(remaining, crew.daily_hours)
        already = booked.get((crew.id, day), 0.0)
        if already + chunk > crew.daily_hours:
            return None  # a job day is never split around other work
        plan.append(DayPlan(day, DAY_START_HOUR + already, chunk))
        remaining -= chunk
        day += timedelta(days=1)
        if len(plan) > 10:
            return None
    return tuple(plan)


def _load(crew_id: str, start: date, booked: dict[tuple[str, date], float]) -> float:
    return sum(booked.get((crew_id, start + timedelta(days=i)), 0.0) for i in range(14))


def propose(
    hours: float,
    required_skills: set[str],
    crews: list[CrewInfo],
    booked: dict[tuple[str, date], float],
    earliest: date,
    exclude_starts: frozenset[date] = frozenset(),
    horizon_days: int = 45,
) -> Proposal | None:
    eligible = [c for c in crews if required_skills <= c.skills]
    if not eligible:
        return None
    for offset in range(horizon_days):
        d = earliest + timedelta(days=offset)
        if d.weekday() not in WORKDAYS or d in exclude_starts:
            continue
        options = []
        for crew in eligible:
            plan = _fit(crew, hours, d, booked)
            if plan and plan[0].day == d:
                options.append((_load(crew.id, d, booked), crew.id, plan))
        if options:
            _, crew_id, plan = min(options, key=lambda o: (o[0], o[1]))
            return Proposal(crew_id, plan)
    return None
