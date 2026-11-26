local clock = redis.call('TIME')
local now = tonumber(clock[1]) * 1000 + math.floor(tonumber(clock[2]) / 1000)
local ttl = tonumber(ARGV[2])
local max_entries = tonumber(ARGV[3])

redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', now)
redis.call('SET', KEYS[1], ARGV[1], 'PX', ttl)
redis.call('ZADD', KEYS[2], now + ttl, KEYS[1])

local excess = redis.call('ZCARD', KEYS[2]) - max_entries
if excess > 0 then
    local evicted = redis.call('ZPOPMIN', KEYS[2], excess)
    for i = 1, #evicted, 2 do
        redis.call('DEL', evicted[i])
    end
end

-- Keep the registry at least as long as the maximum configurable entry TTL.
redis.call('PEXPIRE', KEYS[2], 300000)
return 1
