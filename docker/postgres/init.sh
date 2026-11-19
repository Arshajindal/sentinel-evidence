#!/bin/sh
# Runs once, when the volume is first created (ADR-043).
# `sentinel` owns the application, test and MLflow databases but is not a
# superuser. pgvector isn't a trusted extension, so the superuser installs it
# in template1 first and every database below inherits it. Migrations still
# say CREATE EXTENSION IF NOT EXISTS and, like pg_trgm, never drop it.
set -eu

psql -v ON_ERROR_STOP=1 -U postgres -d template1 -c "CREATE EXTENSION IF NOT EXISTS vector"

psql -v ON_ERROR_STOP=1 -U postgres -d postgres \
     -v pw="$SENTINEL_DB_PASSWORD" <<'SQL'
CREATE ROLE sentinel LOGIN PASSWORD :'pw';
CREATE DATABASE sentinel OWNER sentinel;
CREATE DATABASE sentinel_test OWNER sentinel;
CREATE DATABASE mlflow OWNER sentinel;
SQL
