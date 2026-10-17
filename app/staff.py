"""Commands from whitelisted staff WhatsApp numbers (Director approvals, status)."""
import logging
import re

from app.db.pool import pool
from app.gateway.normalize import InboundMessage
from app.meta import client as meta
from app.quoting import service as quotes

log = logging.getLogger(__name__)

_PRICE_RE = re.compile(r"^price\s+([0-9a-f]{6,36})\s+([0-9][0-9,]*(?:\.[0-9]+)?)$", re.IGNORECASE)
_BUTTON_RE = re.compile(r"^(approve|reject):([0-9a-f-]{6,36})$", re.IGNORECASE)


async def get_staff(phone: str):
    return await pool().fetchrow("SELECT id, name, role FROM staff_users WHERE wa_phone=$1", phone)


async def deliver_quote_to_client(quote: dict, lines: list[dict]) -> None:
    ident = await pool().fetchrow(
        "SELECT ci.external_id, c.wa_number_id FROM conversations c "
        "JOIN channel_identities ci ON ci.contact_id = c.contact_id AND ci.channel = 'whatsapp' "
        "WHERE c.id = $1",
        quote["conversation_id"],
    )
    if ident is None or not ident["wa_number_id"]:
        log.error("No WhatsApp route for quote %s", quote["id"])
        return
    text = quotes.format_quote_for_client(quote, lines)
    await meta.send_whatsapp_text(ident["wa_number_id"], ident["external_id"], text)
    await pool().execute(
        "INSERT INTO messages (conversation_id, direction, sender, type, body) VALUES ($1,'out','staff','text',$2)",
        quote["conversation_id"],
        text,
    )
    await pool().execute("UPDATE conversations SET state='awaiting_client' WHERE id=$1", quote["conversation_id"])
    await quotes.set_status(quote["id"], "sent")


async def handle_staff(msg: InboundMessage, staff) -> bool:
    """Returns True if the message was a staff command (handled), False to fall through."""
    reply = lambda t: meta.send_whatsapp_text(msg.phone_number_id, msg.sender_id, t)  # noqa: E731
    text = msg.text.strip()

    if m := _BUTTON_RE.match(text):
        action, qid = m.group(1).lower(), m.group(2)
        loaded = await quotes.load_quote(qid)
        if loaded is None:
            await reply("I could not find that quote.")
            return True
        quote, lines = loaded
        if quote["status"] != "pending_director":
            await reply(f"Quote {str(quote['id'])[:8]} is already {quote['status']}.")
            return True
        if staff["role"] != "director":
            await reply("Only the Director can approve quotes.")
            return True
        if action == "approve":
            await quotes.set_status(quote["id"], "approved", staff["id"])
            await deliver_quote_to_client(quote, lines)
            await reply("Approved and sent to the client.")
        else:
            await quotes.set_status(quote["id"], "rejected", staff["id"])
            await pool().execute("UPDATE conversations SET state='human_takeover' WHERE id=$1", quote["conversation_id"])
            await reply("Rejected. The conversation is now with the team; the AI will stay silent.")
        await pool().execute(
            "UPDATE escalations SET status='resolved', resolved_at=now() WHERE conversation_id=$1 AND kind='quote_review' AND status<>'resolved'",
            quote["conversation_id"],
        )
        return True

    if m := _PRICE_RE.match(text):
        if staff["role"] != "director":
            await reply("Only the Director can set a quote price.")
            return True
        loaded = await quotes.load_quote(m.group(1))
        if loaded is None:
            await reply("I could not find that quote.")
            return True
        quote, lines = loaded
        if quote["status"] not in ("pending_director", "approved"):
            await reply(f"Quote {str(quote['id'])[:8]} is already {quote['status']}.")
            return True
        await quotes.override_total(quote["id"], float(m.group(2).replace(",", "")), staff["id"])
        quote, lines = await quotes.load_quote(str(quote["id"]))
        await deliver_quote_to_client(quote, lines)
        await pool().execute(
            "UPDATE escalations SET status='resolved', resolved_at=now() WHERE conversation_id=$1 AND kind='quote_review' AND status<>'resolved'",
            quote["conversation_id"],
        )
        await reply("Price set and quote sent to the client.")
        return True

    if text.lower() == "status":
        row = await pool().fetchrow(
            "SELECT (SELECT count(*) FROM quotes WHERE status='pending_director') AS pending_quotes, "
            "(SELECT count(*) FROM jobs WHERE status IN ('new','awaiting_schedule')) AS pending_jobs, "
            "(SELECT count(*) FROM escalations WHERE status<>'resolved') AS open_escalations"
        )
        await reply(
            f"Pending quotes: {row['pending_quotes']}\nJobs to schedule: {row['pending_jobs']}\n"
            f"Open escalations: {row['open_escalations']}"
        )
        return True

    return False
