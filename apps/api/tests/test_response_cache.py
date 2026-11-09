import asyncio
import fnmatch

import pytest

from app import response_cache


class MemoryPipeline:
    def __init__(self, redis):
        self.redis = redis
        self.operations = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    def incr(self, key):
        self.operations.append(("incr", key))
        return self

    async def execute(self):
        return [await getattr(self.redis, operation)(key) for operation, key in self.operations]


class MemoryRedis:
    def __init__(self):
        self.values = {}

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, *, nx=False, ex=None):
        if nx and key in self.values:
            return False
        self.values[key] = value.decode() if isinstance(value, bytes) else str(value)
        return True

    async def incr(self, key):
        value = int(self.values.get(key, 0)) + 1
        self.values[key] = str(value)
        return value

    async def eval(self, _script, _keys, key, token):
        if self.values.get(key) == token:
            del self.values[key]
            return 1
        return 0

    def pipeline(self, transaction=True):
        return MemoryPipeline(self)

    def matching(self, pattern):
        return [key for key in self.values if fnmatch.fnmatch(key, pattern)]


@pytest.fixture
def memory_redis(monkeypatch):
    redis = MemoryRedis()
    monkeypatch.setattr(response_cache, "redis_client", lambda: redis)
    response_cache._local_locks.clear()
    return redis


@pytest.mark.asyncio
async def test_miss_hit_and_parameter_scope(memory_redis):
    calls = 0

    async def build():
        nonlocal calls
        calls += 1
        return {"call": calls}

    first = await response_cache.get_or_create("leaderboard", (2026,), 60, build)
    second = await response_cache.get_or_create("leaderboard", (2026,), 60, build)
    other_scope = await response_cache.get_or_create("leaderboard", (2027,), 60, build)

    assert (first.status, second.status, other_scope.status) == ("miss", "hit", "miss")
    assert first.value == second.value == {"call": 1}
    assert other_scope.value == {"call": 2}


@pytest.mark.asyncio
async def test_invalidation_makes_old_generation_unreachable(memory_redis):
    calls = 0

    async def build():
        nonlocal calls
        calls += 1
        return {"call": calls}

    await response_cache.get_or_create("dashboard", ("user-a",), 60, build)
    await response_cache.invalidate(("dashboard",), reason="test mutation")
    refreshed = await response_cache.get_or_create("dashboard", ("user-a",), 60, build)

    assert refreshed.status == "miss"
    assert refreshed.value == {"call": 2}
    assert len(memory_redis.matching("*:data:dashboard:*")) == 2


@pytest.mark.asyncio
async def test_concurrent_misses_are_single_flight(memory_redis):
    calls = 0

    async def build():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return {"ok": True}

    results = await asyncio.gather(
        *(
            response_cache.get_or_create("yearly-definitions", ("same",), 60, build)
            for _ in range(8)
        )
    )

    assert calls == 1
    assert all(result.value == {"ok": True} for result in results)
    assert sorted(result.status for result in results) == ["hit"] * 7 + ["miss"]


@pytest.mark.asyncio
async def test_redis_failure_falls_back_without_caching(monkeypatch):
    class DownRedis:
        async def get(self, _key):
            raise ConnectionError("down")

    monkeypatch.setattr(response_cache, "redis_client", lambda: DownRedis())
    calls = 0

    async def build():
        nonlocal calls
        calls += 1
        return {"fresh": calls}

    first = await response_cache.get_or_create("dashboard", ("actor",), 60, build)
    second = await response_cache.get_or_create("dashboard", ("actor",), 60, build)

    assert first.status == second.status == "unavailable"
    assert (first.value, second.value) == ({"fresh": 1}, {"fresh": 2})


@pytest.mark.asyncio
async def test_bypass_for_unbounded_searches_does_not_touch_redis(memory_redis):
    result = await response_cache.get_or_create(
        "yearly-definitions", ("actor", "search"), 60, lambda: asyncio.sleep(0, result={"x": 1}),
        cacheable=False,
    )

    assert result.status == "bypass"
    assert result.value == {"x": 1}
    assert not memory_redis.values
