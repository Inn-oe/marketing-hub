import hashlib
import hmac

from app.ai.service import rule_based_call_request
from app.gateway.normalize import normalize
from app.gateway.signature import verify_signature


def _sig(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_signature_valid_and_invalid():
    body = b'{"a":1}'
    assert verify_signature("s3cret", body, _sig("s3cret", body))
    assert not verify_signature("s3cret", body, _sig("other", body))
    assert not verify_signature("s3cret", body, None)
    assert not verify_signature("", body, _sig("", body))  # unset secret never validates


def test_normalize_whatsapp_text():
    payload = {
        "object": "whatsapp_business_account",
        "entry": [{"changes": [{"value": {
            "metadata": {"phone_number_id": "PN1"},
            "messages": [{"from": "263771234567", "id": "wamid.1", "type": "text", "text": {"body": "Quote please"}}],
        }}]}],
    }
    [m] = normalize(payload)
    assert (m.channel, m.sender_id, m.message_id, m.text, m.phone_number_id) == (
        "whatsapp", "263771234567", "wamid.1", "Quote please", "PN1")


def test_normalize_whatsapp_button_reply():
    payload = {
        "object": "whatsapp_business_account",
        "entry": [{"changes": [{"value": {"metadata": {"phone_number_id": "PN1"}, "messages": [
            {"from": "1", "id": "w2", "type": "interactive", "interactive": {"button_reply": {"id": "approve:42", "title": "Approve"}}}
        ]}}]}],
    }
    [m] = normalize(payload)
    assert m.text == "approve:42"


def test_normalize_messenger_ignores_echo():
    payload = {"object": "page", "entry": [{"id": "PAGE", "messaging": [
        {"sender": {"id": "U1"}, "message": {"mid": "m1", "text": "hi"}},
        {"sender": {"id": "PAGE"}, "message": {"mid": "m2", "text": "bot", "is_echo": True}},
    ]}]}
    msgs = normalize(payload)
    assert [m.message_id for m in msgs] == ["m1"]
    assert msgs[0].channel == "messenger"


def test_normalize_comment_ignores_page_own_comment():
    def entry(author):
        return {"object": "page", "entry": [{"id": "PAGE", "changes": [{"field": "feed", "value": {
            "item": "comment", "verb": "add", "comment_id": "c1", "post_id": "p1",
            "message": "How much for 5kVA?", "from": {"id": author}}}]}]}
    [m] = normalize(entry("U9"))
    assert (m.channel, m.comment_id, m.post_id) == ("fb_comment", "c1", "p1")
    assert normalize(entry("PAGE")) == []


def test_call_request_rules():
    assert rule_based_call_request("Can you call me tomorrow?")
    assert rule_based_call_request("I'd rather speak to someone")
    assert not rule_based_call_request("What is the price of a 5kVA inverter?")
