"""Phase 1 orchestrator: log every inbound message once, escalate call requests, reply, funnel to WhatsApp."""
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from app.ai import service as ai
from app.config import get_settings
from app.db.pool import pool
from app.gateway.normalize import InboundMessage
from app.meta import client as meta
from app.quoting import service as quotes
from app.staff import deliver_quote_to_client, get_staff, handle_staff

log = logging.getLogger(__name__)

CALL_ACK = "Of course, a member of our team will call you shortly. Thank you for your patience."
SLA_MINUTES = 15


def wa_link(ref: str | None = None) -> str | None:
    number = get_settings().whatsapp_sales_number
    if not number:
        return None
    text = quote(f"QUOTE-{ref}" if ref else "Hi, I'd like a solar quote")
    return f"https://wa.me/{number}?text={text}"


async def _upsert_contact(conn, msg: InboundMessage) -> str:
    row = await conn.fetchrow(
        "SELECT contact_id FROM channel_identities WHERE channel=$1 AND external_id=$2",
        msg.channel,
        msg.sender_id,
    )
    if row:
        return row["contact_id"]
    phone = msg.sender_id if msg.channel == "whatsapp" else None
    contact_id = await conn.fetchval(
        "INSERT INTO contacts (phone, first_source) VALUES ($1, $2) RETURNING id", phone, msg.channel
    )
    await conn.execute(
        "INSERT INTO channel_identities (contact_id, channel, external_id) VALUES ($1,$2,$3) "
        "ON CONFLICT (channel, external_id) DO NOTHING",
        contact_id,
        msg.channel,
        msg.sender_id,
    )
    return contact_id


async def _log_inbound(msg: InboundMessage) -> tuple[str, str, str] | None:
    """Store the message. Returns (contact_id, conversation_id, state) or None for a duplicate."""
    async with pool().acquire() as conn, conn.transaction():
        contact_id = await _upsert_contact(conn, msg)
        conv = await conn.fetchrow(
            "INSERT INTO conversations (contact_id, channel, wa_number_id, last_inbound_at) "
            "VALUES ($1,$2,$3,now()) "
            "ON CONFLICT (contact_id, channel) DO UPDATE SET last_inbound_at = now(), updated_at = now() "
            "RETURNING id, state",
            contact_id,
            msg.channel,
            msg.phone_number_id,
        )
        inserted = await conn.fetchval(
            "INSERT INTO messages (conversation_id, direction, sender, external_message_id, type, body) "
            "VALUES ($1,'in','contact',$2,$3,$4) ON CONFLICT (external_message_id) DO NOTHING RETURNING id",
            conv["id"],
            msg.message_id,
            msg.msg_type,
            msg.text,
        )
        if inserted is None:
            return None
        return contact_id, conv["id"], conv["state"]


async def _history(conversation_id: str, limit: int = 12) -> list[dict]:
    rows = await pool().fetch(
        "SELECT direction, body FROM messages WHERE conversation_id=$1 AND body IS NOT NULL AND body <> '' "
        "ORDER BY created_at DESC LIMIT $2",
        conversation_id,
        limit,
    )
    turns = [{"role": "user" if r["direction"] == "in" else "assistant", "content": r["body"]} for r in reversed(rows)]
    # The API requires the first turn to be from the user.
    while turns and turns[0]["role"] != "user":
        turns.pop(0)
    return turns


async def _record_outbound(conversation_id: str, body: str, sender: str = "ai", intent: str | None = None) -> None:
    await pool().execute(
        "INSERT INTO messages (conversation_id, direction, sender, type, body, intent) VALUES ($1,'out',$2,'text',$3,$4)",
        conversation_id,
        sender,
        body,
        intent,
    )


async def _send(msg: InboundMessage, text: str) -> None:
    if msg.channel == "whatsapp" and msg.phone_number_id:
        await meta.send_whatsapp_text(msg.phone_number_id, msg.sender_id, text)
    elif msg.channel == "messenger":
        await meta.send_messenger_text(msg.sender_id, text)
    elif msg.channel == "fb_comment" and msg.comment_id:
        await meta.public_reply_to_comment(msg.comment_id, "Thanks for your interest! We've sent you a message.")
        await meta.private_reply_to_comment(msg.comment_id, text)


async def _escalate_call(msg: InboundMessage, conversation_id: str) -> None:
    due = datetime.now(timezone.utc) + timedelta(minutes=SLA_MINUTES)
    async with pool().acquire() as conn, conn.transaction():
        await conn.execute(
            "INSERT INTO escalations (conversation_id, kind, reason, priority, sla_due_at) "
            "VALUES ($1,'call_request',$2,1,$3)",
            conversation_id,
            msg.text[:500],
            due,
        )
        await conn.execute("UPDATE conversations SET state='human_takeover' WHERE id=$1", conversation_id)
        staff = await conn.fetch(
            "SELECT wa_phone FROM staff_users WHERE on_duty AND wa_phone IS NOT NULL "
            "ORDER BY (role='director') ASC, created_at"
        )
    sales_id = get_settings().whatsapp_sales_phone_id
    alert = (
        f"Call request via {msg.channel}\nFrom: {msg.sender_id}\nMessage: {msg.text[:200]}\n"
        f"Please call within {SLA_MINUTES} minutes."
    )
    for s in staff:
        try:
            await meta.send_whatsapp_text(sales_id, s["wa_phone"], alert)
        except Exception:  # one failed alert must not block the others
            log.exception("Failed to alert %s", s["wa_phone"])


async def _last_outbound_intent(conversation_id: str) -> str | None:
    return await pool().fetchval(
        "SELECT intent FROM messages WHERE conversation_id=$1 AND direction='out' ORDER BY created_at DESC LIMIT 1",
        conversation_id,
    )


async def _run_quote_flow(msg: InboundMessage, contact_id: str, conversation_id: str) -> None:
    spec = await ai.extract_quote_spec(await _history(conversation_id))
    if question := quotes.followup_question(spec):
        await _send(msg, question)
        await _record_outbound(conversation_id, question, intent="quote_followup")
        return

    quote_id, result = await quotes.create_quote(contact_id, conversation_id, spec)
    if not result.needs_director:
        loaded = await quotes.load_quote(str(quote_id))
        await deliver_quote_to_client(*loaded)
        return

    wait = "Thank you. Our Director is reviewing your quote personally and we'll message you here shortly."
    await _send(msg, wait)
    await _record_outbound(conversation_id, wait, intent="quote_pending")
    async with pool().acquire() as conn, conn.transaction():
        await conn.execute(
            "INSERT INTO escalations (conversation_id, kind, reason, priority, sla_due_at) "
            "VALUES ($1,'quote_review',$2,2, now() + interval '4 hours')",
            conversation_id,
            ", ".join(result.flags)[:500],
        )
        await conn.execute("UPDATE conversations SET state='awaiting_director' WHERE id=$1", conversation_id)
        directors = await conn.fetch("SELECT wa_phone FROM staff_users WHERE role='director' AND wa_phone IS NOT NULL")
    quote, _ = await quotes.load_quote(str(quote_id))
    note = quotes.format_quote_for_director(quote_id, quote, result.flags)
    for d in directors:
        try:
            await meta.send_whatsapp_buttons(
                get_settings().whatsapp_sales_phone_id,
                d["wa_phone"],
                note,
                [(f"approve:{quote_id}", "Approve"), (f"reject:{quote_id}", "Take over")],
            )
        except Exception:
            log.exception("Failed to alert director %s", d["wa_phone"])


async def handle_inbound(msg: InboundMessage) -> None:
    if msg.channel == "whatsapp":
        staff = await get_staff(msg.sender_id)
        if staff and await handle_staff(msg, staff):
            return

    logged = await _log_inbound(msg)
    if logged is None:
        log.info("Duplicate message %s ignored", msg.message_id)
        return
    contact_id, conversation_id, state = logged
    if state in ("human_takeover", "awaiting_director", "closed"):
        return  # a human holds this thread; the AI stays silent

    intent = await ai.classify_intent(msg.text)
    if intent == "call_request":
        await _escalate_call(msg, conversation_id)
        await _send(msg, CALL_ACK)
        await _record_outbound(conversation_id, CALL_ACK, intent=intent)
        return

    if msg.channel == "whatsapp" and (
        intent == "quote_request" or await _last_outbound_intent(conversation_id) == "quote_followup"
    ):
        await _run_quote_flow(msg, contact_id, conversation_id)
        return

    funnel = wa_link(msg.sender_id[-6:]) if msg.channel != "whatsapp" else None
    text = await ai.reply(await _history(conversation_id), funnel_to_whatsapp_link=funnel)
    await _send(msg, text)
    await _record_outbound(conversation_id, text, intent=intent)
