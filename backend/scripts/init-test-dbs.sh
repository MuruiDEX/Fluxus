#!/bin/sh
# Creates the extra databases on the TEST postgres only.
# Dev database (muse) is untouched by test tooling.
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'EOSQL'
CREATE DATABASE muse_mig;
EOSQL
