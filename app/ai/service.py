"""Single entry point for every LLM call, so models, prompts and logging live in one place."""
import json
import re
from typing import Literal

from anthropic import AsyncAnthropic

from app.config import get_settings

Intent = Literal["quote_request", "call_request", "complaint", "faq", "status", "other"]
INTENTS: tuple[str, ...] = ("quote_request", "call_request", "complaint", "faq", "status", "other")

# Cheap, deterministic backstop that runs before (and independently of) the LLM so a call
# request can never be missed by a model error.
_CALL_RE = re.compile(
    r"\b(call me|phone me|give me a call|ring me|can (you|someone) call|speak to (a |someone|somebody)|talk to (a |someone|somebody)|call back)\b",
    re.IGNORECASE,
)

BRAND_VOICE = (
    "You are the assistant for a solar installation company. Be warm, professional and concise. "
    "Never state a price, discount or delivery date yourself; quotes come from the quoting system. "
    "Say you are an automated assistant if asked, and that a team member is available."
)

_client: AsyncAnthropic | None = None


def _anthropic() -> AsyncAnthropic:
    global _client
    if _client is None:
        _client = AsyncAnthropic(api_key=get_settings().anthropic_api_key)
    return _client


def rule_based_call_request(text: str) -> bool:
    return bool(_CALL_RE.search(text))


async def classify_intent(text: str) -> Intent:
    if rule_based_call_request(text):
        return "call_request"
    s = get_settings()
    resp = await _anthropic().messages.create(
        model=s.model_fast,
        max_tokens=20,
        system=(
            "Classify the customer message for a solar company. Reply with exactly one of: "
            + ", ".join(INTENTS)
        ),
        messages=[{"role": "user", "content": text or "(empty)"}],
    )
    label = resp.content[0].text.strip().lower() if resp.content else "other"
    return label if label in INTENTS else "other"  # type: ignore[return-value]


async def reply(history: list[dict], *, funnel_to_whatsapp_link: str | None = None) -> str:
    """Draft a customer-facing reply. history: [{"role": "user"|"assistant", "content": str}]."""
    s = get_settings()
    system = BRAND_VOICE
    if funnel_to_whatsapp_link:
        system += (
            " Your goal in this channel is to move the customer to WhatsApp. Answer briefly, then invite them "
            f"to continue here: {funnel_to_whatsapp_link}"
        )
    resp = await _anthropic().messages.create(
        model=s.model_main, max_tokens=400, system=system, messages=history
    )
    return resp.content[0].text.strip()


async def extract_quote_spec(history: list[dict]) -> dict:
    """Fill a typed spec from the chat. Returns only fields the customer actually stated."""
    s = get_settings()
    resp = await _anthropic().messages.create(
        model=s.model_main,
        max_tokens=500,
        system=(
            "Extract a solar quote request from the conversation as JSON with keys: property_type, "
            "monthly_kwh, peak_load_kw, backup_hours, roof_type, location, grid_type, budget. "
            "Use null for anything not stated. Output JSON only."
        ),
        messages=history,
    )
    try:
        return json.loads(resp.content[0].text)
    except (json.JSONDecodeError, IndexError):
        return {}
