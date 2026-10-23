#!/bin/sh
# Prepares a fresh database for the API and worker containers.
#
# Runs as a one-shot `init` service in docker-compose.yml before `api`/`worker` start
# (they depend on it completing successfully). Every step is idempotent, so restarting
# the stack - or running this against an already-populated volume - is safe.
#
# Steps, in order:
#   1. wait for Postgres to accept connections
#   2. alembic upgrade head            - application tables
#   3. Procrastinate job queue schema - created only when genuinely missing, because
#      apply_schema_async() is not idempotent and fails on an existing database
#   4. seed system roles               - idempotent, self-healing
set -e

echo "[init] waiting for postgres..."
python - <<'PY'
import asyncio
import time

import asyncpg

DSN = "postgresql://supportai:supportai_dev_password@db:5432/supportai"


async def probe() -> None:
    conn = await asyncpg.connect(DSN)
    await conn.close()


deadline = time.time() + 120
while True:
    try:
        asyncio.run(probe())
        break
    except Exception as exc:  # noqa: BLE001 - any connection error just means "not up yet"
        if time.time() > deadline:
            raise SystemExit(f"[init] postgres never became reachable: {exc}")
        print(f"[init] not ready yet ({type(exc).__name__}), retrying...")
        time.sleep(2)
PY

echo "[init] applying alembic migrations..."
alembic upgrade head

echo "[init] checking Procrastinate job queue schema..."
if python -m scripts.check_procrastinate | grep -q "procrastinate_jobs"; then
    echo "[init] procrastinate schema already present, skipping"
else
    echo "[init] applying procrastinate schema..."
    python -m scripts.apply_procrastinate_schema
fi

echo "[init] seeding system roles..."
python -m scripts.seed_roles

echo "[init] database ready"