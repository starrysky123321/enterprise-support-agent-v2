#!/bin/sh
set -eu

for attempt in $(seq 1 90); do
  if pg_isready -h postgres -U knowledge -d postgres -q; then
    psql -v ON_ERROR_STOP=1 -h postgres -U knowledge -d postgres <<'SQL'
SELECT 'CREATE DATABASE knowledge' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'knowledge') \gexec
SQL
    psql -v ON_ERROR_STOP=1 -h postgres -U knowledge -d knowledge <<'SQL'
\getenv search_password KNOWLEDGE_SEARCH_DATABASE_PASSWORD
BEGIN;
SELECT pg_advisory_xact_lock(846201539);
SELECT 'CREATE ROLE knowledge_index LOGIN'
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'knowledge_index') \gexec
ALTER ROLE knowledge_index PASSWORD :'search_password';
CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS knowledge_search AUTHORIZATION knowledge_index;
ALTER SCHEMA knowledge_search OWNER TO knowledge_index;
SELECT format('ALTER TABLE knowledge_search.%I OWNER TO knowledge_index', tablename)
FROM pg_tables WHERE schemaname = 'knowledge_search' AND tableowner <> 'knowledge_index' \gexec
COMMIT;
SQL
    exit 0
  fi
  sleep 2
done
exit 1
