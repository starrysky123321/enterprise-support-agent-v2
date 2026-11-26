#!/bin/sh
set -eu

config=/data/redis.conf
if [ ! -s "$config" ]; then
  cat > "$config" <<'CONF'
bind 0.0.0.0
protected-mode no
save ""
appendonly no
CONF
  if [ -n "${REDIS_BOOTSTRAP_UPSTREAM:-}" ]; then
    printf 'replicaof %s 6379\n' "$REDIS_BOOTSTRAP_UPSTREAM" >> "$config"
  fi
fi
# Sentinel rewrites the configuration when it changes a node's role.
exec docker-entrypoint.sh redis-server "$config"
