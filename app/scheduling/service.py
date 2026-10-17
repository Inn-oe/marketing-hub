"""Quote acceptance -> job -> proposed slot -> client confirmation -> crew roster."""
import json
import logging
import re
import uuid
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.db.pool import pool
from app.meta import client as meta
from app.scheduling.solver import CrewInfo, Proposal, estimate_hours, propose

log = logging.getLogger(__name__)

YES_RE = re.compile(r"^\s*(yes|y|yeah|yep|ok|okay|accept|accepted|confirm|confirmed|sure|proceed|agree)\b", re.I)
NO_RE = re.compile(r"^\s*(no|nope|nah|another|different|later|not)\b", re.I)


def tz() -> ZoneInfo:
    return ZoneInfo(get_settings().timezone)


def is_yes(text: str) -> bool:
    return bool(YES_RE.match(text))


def is_no(text: str) -> bool:
    return bool(NO_RE.match(text))


def _at(day: date, hour: float) -> datetime:
    return datetime.combine(day, time(int(hour), int(round((hour % 1) * 60))), tzinfo=tz())


async def _crews() -> list[CrewInfo]:
    rows = await pool().fetch("SELECT id, skills, daily_hours FROM crews")
    return [CrewInfo(str(r["id"]), frozenset(r["skills"] or []), float(r["daily_hours"] or 8)) for r in rows]


async def _booked() -> dict[tuple[str, date], float]:
    rows = await pool().fetch(
        "SELECT crew_id, starts_at, ends_at FROM schedule_slots "
        "WHERE status NOT IN ('moved','done') AND ends_at >= now() - interval '1 day'"
    )
    booked: dict[tuple[str, date], float] = {}
    for r in rows:
        local = r["starts_at"].astimezone(tz())
        hours = (r["ends_at"] - r["starts_at"]).total_seconds() / 3600
        key = (str(r["crew_id"]), local.date())
        booked[key] = booked.get(key, 0.0) + hours
    return booked


async def create_job_from_quote(quote: dict) -> dict:
    """Accept the quote, log the job once, reserve stock. Returns the job row."""
    existing = await pool().fetchrow("SELECT * FROM jobs WHERE quote_id=$1", quote["id"])
    if existing:
        return dict(existing)
    spec = quote["spec"] if isinstance(quote["spec"], dict) else json.loads(quote["spec"] or "{}")
    sizing = spec.get("sizing", {})
    panel_kw = float(sizing.get("panel_kw") or 3)
    conv = await pool().fetchrow("SELECT channel FROM conversations WHERE id=$1", quote["conversation_id"])
    async with pool().acquire() as conn, conn.transaction():
        job = await conn.fetchrow(
            "INSERT INTO jobs (ref, contact_id, quote_id, source_channel, type, size_kw, est_hours, required_skills, "
            "address, status) VALUES ($1,$2,$3,$4,'install',$5,$6,$7,$8,'awaiting_schedule') RETURNING *",
            "J" + uuid.uuid4().hex[:6].upper(),
            quote["contact_id"],
            quote["id"],
            conv["channel"] if conv else "whatsapp",
            panel_kw,
            estimate_hours(panel_kw),
            ["install"],
            spec.get("location"),
        )
        await conn.execute("UPDATE quotes SET status='accepted' WHERE id=$1", quote["id"])
        await conn.execute(
            "UPDATE stock_levels s SET qty_reserved = s.qty_reserved + qi.qty "
            "FROM quote_items qi WHERE qi.quote_id=$1 AND qi.product_id = s.product_id",
            quote["id"],
        )
    return dict(job)


def _describe(proposal: Proposal) -> str:
    first = _at(proposal.days[0].day, proposal.days[0].start_hour)
    days = len(proposal.days)
    span = "1 day" if days == 1 else f"{days} working days"
    return f"{first:%A %d %b} from {first:%H:%M} (about {span})"


async def propose_slot(job: dict, exclude_starts: frozenset[date] = frozenset()) -> tuple[Proposal, str] | None:
    """Hold a slot for the job (status 'proposed') and return the client-facing offer text."""
    earliest = datetime.now(tz()).date() + timedelta(days=1)
    proposal = propose(
        float(job["est_hours"]),
        set(job["required_skills"] or []),
        await _crews(),
        await _booked(),
        earliest,
        exclude_starts,
    )
    if proposal is None:
        return None
    async with pool().acquire() as conn, conn.transaction():
        for d in proposal.days:
            await conn.execute(
                "INSERT INTO schedule_slots (job_id, crew_id, starts_at, ends_at, status, created_by) "
                "VALUES ($1,$2,$3,$4,'proposed','ai')",
                job["id"],
                proposal.crew_id,
                _at(d.day, d.start_hour),
                _at(d.day, d.start_hour) + timedelta(hours=d.hours),
            )
    text = (
        f"Thank you for accepting! We can install on {_describe(proposal)}. "
        "Reply YES to confirm, or NO and we'll offer another date."
    )
    return proposal, text


async def declined_starts(job_id) -> frozenset[date]:
    rows = await pool().fetch("SELECT starts_at FROM schedule_slots WHERE job_id=$1 AND status='moved'", job_id)
    return frozenset(r["starts_at"].astimezone(tz()).date() for r in rows)


async def decline_current(job_id) -> None:
    await pool().execute("UPDATE schedule_slots SET status='moved' WHERE job_id=$1 AND status='proposed'", job_id)


async def latest_job_for_conversation(conversation_id) -> dict | None:
    row = await pool().fetchrow(
        "SELECT j.* FROM jobs j JOIN quotes q ON q.id = j.quote_id WHERE q.conversation_id=$1 "
        "ORDER BY j.created_at DESC LIMIT 1",
        conversation_id,
    )
    return dict(row) if row else None


async def confirm_job(job: dict) -> str:
    """Client said YES: lock the slot, mark the job scheduled, roster the crew lead. Returns client text."""
    async with pool().acquire() as conn, conn.transaction():
        slots = await conn.fetch(
            "UPDATE schedule_slots SET status='client_confirmed' WHERE job_id=$1 AND status='proposed' "
            "RETURNING crew_id, starts_at, ends_at",
            job["id"],
        )
        await conn.execute("UPDATE jobs SET status='scheduled' WHERE id=$1", job["id"])
        leads = []
        if slots:
            leads = await conn.fetch(
                "SELECT wa_phone FROM staff_users WHERE crew_id=$1 AND role='crew_lead' AND wa_phone IS NOT NULL",
                slots[0]["crew_id"],
            )
        materials = await conn.fetch(
            "SELECT qi.qty, p.name FROM quote_items qi JOIN products p ON p.id=qi.product_id WHERE qi.quote_id=$1",
            job["quote_id"],
        )
        phone = await conn.fetchval("SELECT phone FROM contacts WHERE id=$1", job["contact_id"])
    if not slots:
        return "Sorry, that slot is no longer held. A team member will contact you shortly to arrange a date."

    first = slots[0]["starts_at"].astimezone(tz())
    roster = (
        f"New job {job['ref']}\nWhen: {first:%A %d %b %H:%M}, about {float(job['est_hours']):g} hours\n"
        f"Where: {job['address'] or 'address to be confirmed'}\nClient: +{phone or 'unknown'}\n"
        "Materials:\n" + "\n".join(f"- {m['qty']} x {m['name']}" for m in materials)
    )
    for lead in leads:
        try:
            await meta.send_whatsapp_text(get_settings().whatsapp_sales_phone_id, lead["wa_phone"], roster)
            await pool().execute("UPDATE schedule_slots SET status='team_notified' WHERE job_id=$1 AND status='client_confirmed'", job["id"])
        except Exception:
            log.exception("Failed to roster crew lead %s", lead["wa_phone"])
    return f"Confirmed! Your installation is booked for {first:%A %d %b} from {first:%H:%M}. We'll message you the day before."


async def jobs_summary(day: date) -> str:
    rows = await pool().fetch(
        "SELECT j.ref, j.address, s.starts_at, c.name AS crew FROM schedule_slots s "
        "JOIN jobs j ON j.id=s.job_id LEFT JOIN crews c ON c.id=s.crew_id "
        "WHERE s.status IN ('client_confirmed','team_notified') ORDER BY s.starts_at"
    )
    todays = [r for r in rows if r["starts_at"].astimezone(tz()).date() == day]
    if not todays:
        return f"No jobs scheduled for {day:%a %d %b}."
    return f"Jobs for {day:%a %d %b}:\n" + "\n".join(
        f"- {r['starts_at'].astimezone(tz()):%H:%M} {r['ref']} {r['address'] or ''} ({r['crew'] or 'unassigned'})"
        for r in todays
    )
