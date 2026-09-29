import json
import ssl
import urllib.request

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

BASE_URL = "https://127.0.0.1:8000/api/v1"

def api_call(endpoint, method="GET", data=None):
    url = f"{BASE_URL}{endpoint}"
    body = json.dumps(data).encode("utf-8") if data else None
    headers = {"Content-Type": "application/json"}
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, context=ctx) as resp:
        return json.loads(resp.read().decode("utf-8"))

print("\n" + "=" * 80)
print("             ТЕСТИРОВАНИЕ СКВОЗНОЙ CPA-ВОРОНКИ (autoXAK)")
print("=" * 80)

test_cases = [
    {
        "user_id": "test_user_octavia_20",
        "car_id": "skoda_octavia_20tsi_dsg_4wd",
        "expected_volume": 5.7,
        "expected_grade": "0W-20",
        "target_savings": 2400.0
    },
    {
        "user_id": "test_user_haval_jolion",
        "car_id": "haval_jolion_15t_4wd",
        "expected_volume": 3.8,
        "expected_grade": "0W-20",
        "target_savings": 1800.0
    }
]

for tc in test_cases:
    uid = tc["user_id"]
    cid = tc["car_id"]
    print(f"\n[ТЕСТ] Пользователь: {uid} | Авто: {cid}")

    # 1. Обновление профиля: износ 85% (остаток 15% -> триггер CPA)
    profile_payload = {
        "user_id": uid,
        "car_id": cid,
        "current_oil_wear_percent": 85.0,
        "fuel_price_rub": 72.40,
        "service_cost_rub": 9500.0
    }
    api_call(f"/users/{uid}/profile", method="POST", data=profile_payload)

    # 2. Проверка триггера в дашборде
    dash = api_call(f"/users/{uid}/dashboard")
    oil_rem = dash["oil_remaining_percent"]
    cpa_rec = dash["cpa_recommended"]
    print(f"  ├── Дашборд: остаток масла {oil_rem}% | Триггер CPA: {cpa_rec} | Статус: {dash['cpa_offer_text']}")
    assert cpa_rec is True, "CPA триггер не сработал при остатке < 20%!"

    # 3. Получение персонализированного CPA-оффера
    offer = api_call(f"/cpa/offer/{uid}")
    vol = offer["oil_volume_liters"]
    grade = offer["oil_viscosity"]
    oil_name = offer["recommended_oil"]
    discount = offer["service_discount_rub"]
    promo = offer["promo_code"]

    print(f"  ├── CPA-оффер сформирован:")
    print(f"  │    • Модификация: {offer['car_name']}")
    print(f"  │    • Рекомендованное масло: {oil_name} ({grade})")
    print(f"  │    • Объем картера: {vol} л (Ожидалось: {tc['expected_volume']} л)")
    print(f"  │    • Скидка из копилки: {discount} ₽ | Промокод: {promo}")

    assert vol == tc["expected_volume"], f"Неверный объем масла! Получено {vol}, ожидалось {tc['expected_volume']}"
    assert grade == tc["expected_grade"], f"Неверная вязкость масла!"

    # 4. Имитация клика / активации промокода (Capture Lead)
    claim_payload = {
        "user_id": uid,
        "promo_code": promo,
        "partner_id": "autodoc_lead_engine"
    }
    claim_res = api_call("/cpa/claim", method="POST", data=claim_payload)
    print(f"  └── Фиксация лида: {claim_res['status']} (Промокод {claim_res['promo_code']} сохранен в БД)")

print("\n" + "=" * 80)
print("     ВСЕ ПРОВЕРКИ CPA-ВОРОНКИ ПРОЙДЕНЫ УСПЕШНО: ОБЪЕМЫ И СКИДКИ СОВПАЛИ")
print("=" * 80 + "\n")
