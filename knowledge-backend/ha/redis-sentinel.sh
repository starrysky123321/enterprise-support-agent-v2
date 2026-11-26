#!/bin/sh
set -eu

config=/data/redis-sentinel.conf
if [ ! -s "$config" ]; then
  cat > "$config" <<CONF
port 26379
bind 0.0.0.0
protected-mode no
sentinel monitor knowledge-redis ${REDIS_BOOTSTRAP_ADDRESS} 6379 2
sentinel down-after-milliseconds knowledge-redis 3000
sentinel failover-timeout knowledge-redis 15000
sentinel parallel-syncs knowledge-redis 1
CONF
fi
exec redis-server "$config" --sentinel
