import os
import json
import hashlib
import math
import asyncio
import httpx
import redis.asyncio as aioredis
from typing import List, Dict
from pydantic import BaseModel
from app.core.dsp.simplifier import PathSimplifier

class RoadContext(BaseModel):
    traffic_score: int          # Баллы затора (1-10)
    matched_distance_m: float   # Истинная дистанция по графу дорог (метры)
    avg_speed_limit_kmh: float  # Средний скоростной лимит участка
    avg_slope_percent: float    # Средний уклон рельефа (%)
    road_type: str              # Категория магистрали
    source: str                 # 'dgis_routing', 'redis_cache', 'internal_fallback'

class GISService:
    def __init__(self):
        self.api_key = os.getenv("DGIS_API_KEY", "")
        self.use_mock = os.getenv("DGIS_USE_MOCK", "false").lower() == "true"
        self.redis_host = os.getenv("REDIS_HOST", "redis")
        self.redis_port = int(os.getenv("REDIS_PORT", 6379))
        self.simplifier = PathSimplifier()
        self._pool = None

    def _get_redis_client(self) -> aioredis.Redis:
        if self._pool is None:
            self._pool = aioredis.ConnectionPool(
                host=self.redis_host, 
                port=self.redis_port, 
                decode_responses=True,
                max_connections=10,
                socket_connect_timeout=0.5, socket_timeout=0.5
            )
        return aioredis.Redis(connection_pool=self._pool)

    def _generate_route_hash(self, simplified_coords: List[Dict[str, float]]) -> str:
        if not simplified_coords:
            return "empty_route"
        raw_str = json.dumps(simplified_coords, sort_keys=True)
        return f"route_cache:{hashlib.md5(raw_str.encode()).hexdigest()}"

    async def get_route_context(self, coordinates: List[Dict[str, float]]) -> RoadContext:
        if not coordinates or self.use_mock or not self.api_key or self.api_key == "demo_key":
            return self._fallback_context(coordinates)

        # 1. RDP-сжатие геометрии
        simplified = await asyncio.to_thread(self.simplifier.downsample_gps_stream, coordinates, max_points_for_api=40)
        cache_key = self._generate_route_hash(simplified)
        redis_client = None

        # 2. Проверка LBS-кэша в Redis
        try:
            redis_client = self._get_redis_client()
            cached_data = await redis_client.get(cache_key)
            if cached_data:
                cached_json = json.loads(cached_data)
                cached_json["source"] = "redis_cache"
                return RoadContext(**cached_json)
        except Exception:
            pass

        if self.use_mock or not self.api_key or self.api_key == "demo_key":
            return self._fallback_context(coordinates)

        # 3. Боевой запрос к 2GIS Routing API
        try:
            async with httpx.AsyncClient(timeout=3.5) as http_client:
                url_routing = f"https://routing.api.2gis.com/carrouting/6.0.0/global?key={self.api_key}"
                payload = {
                    "points": [
                        {"type": "stop", "lat": simplified[0]["lat"], "lon": simplified[0]["lon"]},
                        {"type": "stop", "lat": simplified[-1]["lat"], "lon": simplified[-1]["lon"]}
                    ],
                    "type": "jam"
                }
                resp = await http_client.post(url_routing, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    route_info = data.get("result", [{}])[0]
                    distance_m = float(route_info["total_distance"])
                    duration_s = float(route_info["total_duration"])
                    if not math.isfinite(distance_m) or not math.isfinite(duration_s) or distance_m < 0 or duration_s <= 0:
                        raise ValueError("Invalid routing metrics")

                    avg_speed = (distance_m / duration_s) * 3.6 if duration_s > 0 else 30.0
                    traffic_score = 8 if avg_speed < 15.0 else (5 if avg_speed < 35.0 else 2)

                    result_context = RoadContext(
                        traffic_score=traffic_score,
                        matched_distance_m=distance_m,
                        avg_speed_limit_kmh=60.0,
                        avg_slope_percent=0.0,
                        road_type="urban_arterial",
                        source="dgis_routing"
                    )

                    # Запись в Redis (TTL 24 часа)
                    if redis_client:
                        try:
                            await redis_client.setex(cache_key, 300, result_context.model_dump_json())
                        except Exception:
                            pass

                    return result_context
        except Exception:
            pass

        return self._fallback_context(coordinates)

    def _fallback_context(self, coordinates: List[Dict[str, float]]) -> RoadContext:
        return RoadContext(traffic_score=1, matched_distance_m=0.0,
                           avg_speed_limit_kmh=0.0, avg_slope_percent=0.0,
                           road_type="unknown", source="internal_fallback")

    async def aclose(self):
        if self._pool is not None:
            await self._pool.disconnect()
            self._pool = None
