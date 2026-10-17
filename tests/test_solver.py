from datetime import date

from app.scheduling.solver import CrewInfo, estimate_hours, propose

MON = date(2026, 10, 19)  # a Monday
SAT = date(2026, 10, 24)
SUN = date(2026, 10, 25)
INSTALL = frozenset({"install"})
A = CrewInfo("A", frozenset({"install", "electrical"}))
B = CrewInfo("B", frozenset({"install"}))


def test_earliest_free_day_and_start_hour():
    p = propose(8, {"install"}, [A], {}, MON)
    assert p.start_date == MON and p.days[0].start_hour == 8.0


def test_skips_sunday():
    p = propose(8, {"install"}, [A], {}, SUN)
    assert p.start_date == date(2026, 10, 26)


def test_multi_day_job_spans_workdays_over_sunday():
    p = propose(16, {"install"}, [A], {}, SAT)
    assert [d.day for d in p.days] == [SAT, date(2026, 10, 26)]


def test_busy_crew_pushes_to_other_crew_then_next_day():
    booked = {("A", MON): 8.0}
    assert propose(8, {"install"}, [A, B], booked, MON).crew_id == "B"
    booked[("B", MON)] = 8.0
    assert propose(8, {"install"}, [A, B], booked, MON).start_date == date(2026, 10, 20)


def test_partial_day_appends_after_existing_work():
    p = propose(4, {"install"}, [A], {("A", MON): 4.0}, MON)
    assert p.days[0].start_hour == 12.0


def test_skill_filter_and_excluded_start():
    assert propose(8, {"electrical"}, [B], {}, MON) is None
    p = propose(8, {"install"}, [A], {}, MON, exclude_starts=frozenset({MON}))
    assert p.start_date == date(2026, 10, 20)


def test_ties_go_to_lighter_crew():
    booked = {("A", date(2026, 10, 22)): 8.0}  # A busy later in the window
    assert propose(8, {"install"}, [A, B], booked, MON).crew_id == "B"


def test_estimate_hours():
    assert estimate_hours(1.0) == 4.0
    assert estimate_hours(5.0) == 10.0
