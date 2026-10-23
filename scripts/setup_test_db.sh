#!/bin/bash
# Creates the disposable test database that backend/tests/conftest.py expects.
# conftest.py builds and tears down the schema itself (DROP/CREATE SCHEMA and the
# `vector` extension) on every test session, so this only needs to make the
# (empty) database exist once. Re-running it is a no-op.
#
# Run from this directory:
#   sh scripts/setup_test_db.sh        # host-side helper (needs docker compose)
# or, if you prefer an one-liner:
#   docker compose exec -T db psql -U supportai -d supportai -c "CREATE DATABASE supportai_test;"
set -euo pipefail

DB_NAME=supportai_test

# Create the database by connecting to the 'supportai' maintenance database.
if ! docker compose exec -T db psql -U supportai -d supportai -Atc \
    "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}';" | grep -q 1; then
  docker compose exec -T db createdb -U supportai "${DB_NAME}"
fi

# The image (pgvector/pgvector:pg16) provides the vector extension; install it
# so conftest's "CREATE EXTENSION IF NOT EXISTS vector" can succeed.
docker compose exec -T db psql -U supportai -d "${DB_NAME}" -c \
  "CREATE EXTENSION IF NOT EXISTS vector;" >/dev/null

echo "[setup_test_db] database '${DB_NAME}' ready"
