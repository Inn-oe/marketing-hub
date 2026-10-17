# Solar Automation Platform

WhatsApp + Facebook AI automation for a solar company. Design doc: see the "Solar Automation Platform — System Design" doc.

## Status (Phase 0 + start of Phase 1)

| Area | State |
| --- | --- |
| Postgres schema (`app/db/schema.sql`) | done, applied on startup |
| Webhook gateway: signature check, verify handshake, normalisation of WhatsApp / Messenger / comments | done, tested |
| Orchestrator: idempotent logging, contact merge per channel, call-request escalation, AI reply, WhatsApp funnel link | written, **untested against live Meta and a real DB** |
| Quoting: BOM selection, pricing, Director flags, WhatsApp approve / reject / `price <id> <amount>` commands, `status` command | wired in, pure logic tested; **DB and Meta paths untested** |
| Scheduling: YES to a quote creates the job and reserves stock, solver proposes a slot (Mon-Sat, earliest, lightest crew), client confirms, crew lead gets a roster; `jobs` / `jobs tomorrow` staff commands | solver tested (8 tests); DB and Meta paths untested |
| Render blueprint (`render.yaml`) + seed script | ready |
| Ads, website module, dashboard, 07:00 daily summary | not started |

## Run

```
python -m venv .venv && .venv\Scripts\pip install -e ".[dev]"
copy .env.example .env      # fill in values
.venv\Scripts\pytest
.venv\Scripts\uvicorn app.main:app --reload
```

Needs PostgreSQL with the `pgvector` extension. Point Meta's webhook at `https://<host>/webhook` using `META_VERIFY_TOKEN`.

## Deploy on Render

1. Push this folder to a GitHub repo (`.env` and `.venv` are git-ignored).
2. Render dashboard > New > Blueprint > pick the repo. `render.yaml` creates the web service **and** the Postgres database `solar-db`; `DATABASE_URL` is wired automatically.
3. Fill in the prompted secrets (Meta and Anthropic keys, phone IDs).
4. In Meta's developer console set the webhook to `https://<service>.onrender.com/webhook` with the generated `META_VERIFY_TOKEN` (Render > service > Environment).
5. Seed once from the Render shell: `DIRECTOR_PHONE=<digits> CREW_LEAD_PHONE=<digits> python -m scripts.seed`.

## Known gaps

- Scheduler ignores travel time (no geocoding yet) and uses a placeholder duration of about 2 crew-hours per kW; calibrate from completed jobs.
- Client replies to a slot offer are matched on YES/NO words only; anything else goes to the normal AI reply.

- Sizing rules, labour and margin in `app/quoting/engine.py` are placeholders for the Director's real rules.
- Messages that arrive outside the 24h WhatsApp window need template sends (not implemented).
- No retry or durable timers yet; the 15-minute call SLA is stored (`escalations.sla_due_at`) but nothing re-alerts. Temporal is the planned fix.
- Background processing is in-process; a crash loses queued messages.
