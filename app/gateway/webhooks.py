import json
import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, Response

from app.config import get_settings
from app.gateway.normalize import normalize
from app.gateway.signature import verify_signature
from app.orchestrator import handle_inbound

log = logging.getLogger(__name__)
router = APIRouter()


@router.get("/webhook")
async def verify(
    mode: str = Query(alias="hub.mode", default=""),
    token: str = Query(alias="hub.verify_token", default=""),
    challenge: str = Query(alias="hub.challenge", default=""),
) -> Response:
    if mode == "subscribe" and token == get_settings().meta_verify_token:
        return Response(content=challenge, media_type="text/plain")
    raise HTTPException(status_code=403, detail="Verification failed")


async def _process(messages) -> None:
    for m in messages:
        try:
            await handle_inbound(m)
        except Exception:
            log.exception("Failed to handle message %s", m.message_id)


@router.post("/webhook")
async def receive(request: Request, background: BackgroundTasks) -> dict:
    body = await request.body()
    if not verify_signature(get_settings().meta_app_secret, body, request.headers.get("x-hub-signature-256")):
        raise HTTPException(status_code=401, detail="Bad signature")
    messages = normalize(json.loads(body))
    # Meta expects a fast 200; processing happens after the response is sent.
    background.add_task(_process, messages)
    return {"received": len(messages)}
