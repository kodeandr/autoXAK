import asyncio
import os
import httpx

API_KEY = os.getenv("DGIS_API_KEY", "1069d305-b706-4819-840a-b8a191fda6c8")

async def test_key():
    print(f"[*] Тестирование ключа 2GIS: {API_KEY[:8]}...{API_KEY[-4:]}")
    
    # Тест 1: Геокодер / Поиск объектов (базовый Catalog API v3)
    url_catalog = "https://catalog.api.2gis.com/3.0/items/geocode"
    params_catalog = {
        "key": API_KEY,
        "lat": 55.751244,
        "lon": 37.618423,
        "fields": "items.point"
    }

    async with httpx.AsyncClient(timeout=5.0) as client:
        res = await client.get(url_catalog, params=params_catalog)
        print(f"[1] Catalog API Status: {res.status_code}")
        if res.status_code == 200:
            print("    -> Catalog API успешно авторизован.")
        else:
            print(f"    -> Ошибка: {res.text}")

        # Тест 2: Навигационный/маршрутный сервис (уровень заторов и граф дорог)
        url_navi = f"https://routing.api.2gis.com/carrouting/6.0.0/global?key={API_KEY}"
        body_navi = {
            "points": [
                {"type": "stop", "lat": 55.751244, "lon": 37.618423},
                {"type": "stop", "lat": 55.755814, "lon": 37.617635}
            ],
            "type": "jam"
        }
        res_navi = await client.post(url_navi, json=body_navi)
        print(f"[2] Routing/Traffic API Status: {res_navi.status_code}")
        if res_navi.status_code == 200:
            print("    -> Routing/Traffic API успешно авторизован.")
        else:
            print(f"    -> Ошибка/Ограничение: {res_navi.status_code}")

if __name__ == "__main__":
    asyncio.run(test_key())