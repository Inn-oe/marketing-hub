"""Turn Meta webhook payloads (WhatsApp, Messenger, Page comments) into one message shape."""
from dataclasses import dataclass
from typing import Literal

Channel = Literal["whatsapp", "messenger", "fb_comment"]


@dataclass(frozen=True)
class InboundMessage:
    channel: Channel
    sender_id: str  # wa_id, PSID or commenter id
    message_id: str  # idempotency key
    text: str
    msg_type: str = "text"
    phone_number_id: str | None = None  # which business number received it (WhatsApp)
    comment_id: str | None = None  # needed for a private reply
    post_id: str | None = None
    media_id: str | None = None


def _whatsapp_text(msg: dict) -> tuple[str, str | None]:
    kind = msg.get("type", "text")
    if kind == "text":
        return msg.get("text", {}).get("body", ""), None
    if kind == "interactive":
        inter = msg.get("interactive", {})
        reply = inter.get("button_reply") or inter.get("list_reply") or {}
        return reply.get("id", ""), None
    if kind == "button":
        return msg.get("button", {}).get("payload", ""), None
    if kind in ("image", "audio", "document", "video"):
        media = msg.get(kind, {})
        return media.get("caption", ""), media.get("id")
    return "", None


def normalize(payload: dict) -> list[InboundMessage]:
    out: list[InboundMessage] = []
    obj = payload.get("object")
    for entry in payload.get("entry", []):
        if obj == "whatsapp_business_account":
            for change in entry.get("changes", []):
                value = change.get("value", {})
                phone_id = value.get("metadata", {}).get("phone_number_id")
                for msg in value.get("messages", []):
                    text, media_id = _whatsapp_text(msg)
                    out.append(
                        InboundMessage(
                            channel="whatsapp",
                            sender_id=msg["from"],
                            message_id=msg["id"],
                            text=text,
                            msg_type=msg.get("type", "text"),
                            phone_number_id=phone_id,
                            media_id=media_id,
                        )
                    )
        elif obj == "page":
            for event in entry.get("messaging", []):
                message = event.get("message")
                if not message or message.get("is_echo"):
                    continue
                out.append(
                    InboundMessage(
                        channel="messenger",
                        sender_id=event["sender"]["id"],
                        message_id=message["mid"],
                        text=message.get("text", ""),
                    )
                )
            for change in entry.get("changes", []):
                value = change.get("value", {})
                if (
                    change.get("field") == "feed"
                    and value.get("item") == "comment"
                    and value.get("verb") == "add"
                    and value.get("from", {}).get("id") != entry.get("id")  # ignore the Page's own comments
                ):
                    out.append(
                        InboundMessage(
                            channel="fb_comment",
                            sender_id=value["from"]["id"],
                            message_id=value["comment_id"],
                            text=value.get("message", ""),
                            comment_id=value["comment_id"],
                            post_id=value.get("post_id"),
                        )
                    )
    return out
