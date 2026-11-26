local clock = redis.call('TIME')
local now = tonumber(clock[1]) * 1000 + math.floor(tonumber(clock[2]) / 1000)
local units_per_token = 60000
local rate = tonumber(ARGV[1])
local capacity = tonumber(ARGV[2]) * units_per_token
local state = redis.call('HMGET', KEYS[1], 'units', 'updated_at')
local updated_at = math.max(now, tonumber(state[2]) or now)
local elapsed = math.max(0, now - (tonumber(state[2]) or now))
local units = math.min(capacity, (tonumber(state[1]) or capacity) + elapsed * rate)
local retry_ms = 0

if units >= units_per_token then
    units = units - units_per_token
else
    retry_ms = updated_at - now + math.ceil((units_per_token - units) / rate)
end

redis.call('HSET', KEYS[1], 'units', units, 'updated_at', updated_at)
redis.call('PEXPIRE', KEYS[1], 2 * math.ceil(capacity / rate))
return retry_ms
