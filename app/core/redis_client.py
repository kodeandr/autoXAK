import os
import redis.asyncio as aioredis
from typing import Optional

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))

redis_client: Optional[aioredis.Redis] = None

def init_redis() -> aioredis.Redis:
    global redis_client
    if redis_client is None:
        redis_client = aioredis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            db=0,
            decode_responses=True
        )
    return redis_client

async def get_redis_client() -> aioredis.Redis:
    return init_redis()

class CacheService:
    @staticmethod
    def _dashboard_key(user_id: str) -> str:
        return f"dashboard:{user_id}"

    @classmethod
    async def get_dashboard(cls, redis: aioredis.Redis, user_id: str) -> Optional[str]:
        try:
            return await redis.get(cls._dashboard_key(user_id))
        except Exception:
            return None

    @classmethod
    async def set_dashboard(cls, redis: aioredis.Redis, user_id: str, data_json: str, ttl_sec: int = 60) -> None:
        try:
            await redis.set(cls._dashboard_key(user_id), data_json, ex=ttl_sec)
        except Exception:
            pass

    @classmethod
    async def invalidate_dashboard(cls, redis: aioredis.Redis, user_id: str) -> None:
        try:
            await redis.delete(cls._dashboard_key(user_id))
        except Exception:
            pass
