"""Outbound calls to the Meta Graph API (WhatsApp Cloud, Messenger, comments)."""
import httpx

from app.config import get_settings


def _base() -> str:
    return f"https://graph.facebook.com/{get_settings().meta_graph_version}"


def _headers() -> dict:
    return {"Authorization": f"Bearer {get_settings().meta_system_user_token}"}


async def _post(path: str, json: dict) -> dict:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(f"{_base()}/{path}", json=json, headers=_headers())
        resp.raise_for_status()
        return resp.json()


async def send_whatsapp_text(phone_number_id: str, to: str, body: str) -> dict:
    return await _post(
        f"{phone_number_id}/messages",
        {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": body}},
    )


async def send_whatsapp_buttons(phone_number_id: str, to: str, body: str, buttons: list[tuple[str, str]]) -> dict:
    """buttons: [(id, title)], max 3, titles max 20 chars."""
    return await _post(
        f"{phone_number_id}/messages",
        {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": body},
                "action": {
                    "buttons": [{"type": "reply", "reply": {"id": i, "title": t[:20]}} for i, t in buttons[:3]]
                },
            },
        },
    )


async def send_messenger_text(psid: str, body: str) -> dict:
    return await _post(
        "me/messages",
        {"recipient": {"id": psid}, "messaging_type": "RESPONSE", "message": {"text": body}},
    )


async def private_reply_to_comment(comment_id: str, body: str) -> dict:
    """One private reply per comment, within 7 days."""
    return await _post(f"{comment_id}/private_replies", {"message": body})


async def public_reply_to_comment(comment_id: str, body: str) -> dict:
    return await _post(f"{comment_id}/comments", {"message": body})
