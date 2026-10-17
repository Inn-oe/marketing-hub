from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db.pool import apply_schema, close_pool, init_pool
from app.gateway.webhooks import router as webhook_router


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_pool()
    await apply_schema()
    yield
    await close_pool()


app = FastAPI(title="Solar Automation Platform", lifespan=lifespan)
app.include_router(webhook_router)


@app.get("/health")
async def health() -> dict:
    return {"ok": True}
