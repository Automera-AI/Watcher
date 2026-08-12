# Watcher v2 — receptionist scaffold

This is the Phase A skeleton described in `Automera_Watcher_v2_Phase_A_Spec.md`. It runs, it has
tests, and every file is either finished or has a single clearly marked gap.

## What is here

```
app/channels/     one file per way a customer can reach you. Each does two things only.
app/core/         the receptionist itself. Knows nothing about WhatsApp or phones.
app/tools/        the nine things the receptionist is allowed to do.
app/integrations/ connections out to Hostaway, Google Calendar, HubSpot.
app/api/          the web server that receives incoming messages.
migrations/       database changes, applied in order.
eval/             the accuracy test harness and its question set.
```

## Run it

```bash
cp .env.example .env          # fill in the blanks
docker compose up -d db redis
pip install -e ".[dev]"
alembic upgrade head
pytest
uvicorn app.api.main:app --reload
```

## The one rule

`app/core/` must never import from `app/channels/`. If you find yourself wanting to, the thing you
want belongs in the `InboundTurn` envelope instead. A test enforces this.
