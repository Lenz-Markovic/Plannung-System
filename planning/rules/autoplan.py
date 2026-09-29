"""
Automatic planning ("🤖 Automatisch planen"): spread unplanned stops over free days.

Pure function: plain data in, proposals out - no database. The system chooses
here, so the working time is strict: a day never goes over 7,5 h net (work +
estimated drives).

How a day is filled (greedy, like a planner would do it by hand):
1. start with the most important stop the person may do that day: stops
   assigned to this person first, then the ones with the earliest deadline,
   then the person's usual region;
2. then always the NEAREST next stop that still fits into the 7,5 h and is at
   most 1 h drive away (MAX_LEG_MINUTES);
3. when nothing fits any more, the day is full - next free day.

Date windows keep the montage rule of section 8: an installation must be at
least 8 days BEFORE the reading of its building (latest), a reading at least
8 days AFTER a planned installation (earliest). This also holds inside one run:
a reading waits for the installation at the same building (same `group`) and
comes at least 8 days after it.
"""

import datetime
from dataclasses import dataclass, field, replace

from .drive_time import distance_km, estimate_drive_minutes
from .working_time import MAX_NET_MINUTES

BUFFER_DAYS = 8  # installation > 7 days before the reading (conflicts/rules.py: warning up to 7 days)
MAX_LEG_MINUTES = 60  # never more than 1 h drive between two stops of an automatic plan ("nahe beieinander")


@dataclass(frozen=True)
class Job:
    key: object                          # e.g. ("reading", building id)
    kind: str                            # "reading" / "installation"
    minutes: int                         # work time
    point: tuple | None = None           # (lat, lon)
    region: str = ""
    earliest: datetime.date | None = None
    latest: datetime.date | None = None
    person: object = None                # preferred person (assigned), or None
    group: str = ""                      # the building (number core): reading + montage there belong together


@dataclass(frozen=True)
class Slot:
    """One free working day of one person."""

    person: object
    date: datetime.date
    can_read: bool = True
    can_install: bool = False
    region: str = ""                     # where the person usually works


@dataclass
class DayProposal:
    person: object
    date: datetime.date
    jobs: list = field(default_factory=list)
    work: int = 0
    drive: int = 0

    @property
    def net(self):
        return self.work + self.drive


def _allowed(job, slot, placed_installs, placed_readings, waiting):
    """May this job be done on this slot? placed_*: {group: date} from this run;
    waiting: groups with an installation that is not placed yet."""
    if job.kind == "reading" and not slot.can_read:
        return False
    if job.kind == "installation" and not slot.can_install:
        return False
    if job.earliest and slot.date < job.earliest:
        return False
    if job.latest and slot.date > job.latest:
        return False
    buffer = datetime.timedelta(days=BUFFER_DAYS)
    if job.group and job.kind == "reading":
        if job.group in waiting:
            return False  # first the installation, then the reading
        if job.group in placed_installs and slot.date < placed_installs[job.group] + buffer:
            return False
    if job.group and job.kind == "installation" and job.group in placed_readings:
        return slot.date <= placed_readings[job.group] - buffer
    return True


def estimated_drive(a, b):
    return estimate_drive_minutes(a, b) if a and b else 15  # unknown position: a rough 15 min


def plan_days(slots, jobs, max_net=MAX_NET_MINUTES):
    """Returns (proposals, not placed jobs). Slots are filled in date order."""
    planned_people = {slot.person for slot in slots}
    # a preferred person only counts if that person has free days in the period
    jobs = [job if job.person in planned_people else replace(job, person=None) for job in jobs]
    remaining = [job for job in jobs if job.minutes <= max_net]
    too_long = [job for job in jobs if job.minutes > max_net]  # longer than a whole day: never automatic
    order = {job.key: i for i, job in enumerate(jobs)}
    proposals = []
    placed_installs, placed_readings = {}, {}

    for slot in sorted(slots, key=lambda s: (s.date, str(s.person))):
        waiting = {job.group for job in remaining if job.kind == "installation" and job.group}
        allowed = [job for job in remaining if job.person in (None, slot.person)
                   and _allowed(job, slot, placed_installs, placed_readings, waiting)]
        if not allowed:
            continue
        seed = min(allowed, key=lambda job: (
            job.person != slot.person,             # assigned to this person first
            job.latest is None, job.latest or slot.date,  # most urgent
            job.region != slot.region,             # usual region
            order[job.key],
        ))
        day = DayProposal(slot.person, slot.date, [seed], seed.minutes, 0)
        last = seed
        pool = [job for job in allowed if job is not seed]
        while pool:
            # nearest next stop that still fits into the day
            pool.sort(key=lambda job: (distance_km(last.point, job.point) if last.point and job.point else 9999,
                                       job.person != slot.person))
            nxt = next((job for job in pool if estimated_drive(last.point, job.point) <= MAX_LEG_MINUTES
                        and day.net + estimated_drive(last.point, job.point) + job.minutes <= max_net), None)
            if nxt is None:
                break
            day.drive += estimated_drive(last.point, nxt.point)
            day.work += nxt.minutes
            day.jobs.append(nxt)
            pool.remove(nxt)
            last = nxt
        proposals.append(day)
        for job in day.jobs:
            if job.group:
                (placed_installs if job.kind == "installation" else placed_readings)[job.group] = slot.date
        used = {job.key for job in day.jobs}
        remaining = [job for job in remaining if job.key not in used]
    return proposals, remaining + too_long
