import json
import ssl
import time
import uuid
import urllib.request
import numpy as np

# 60-секундный ездовой цикл (3000 точек, dt = 0.02 с)
total_points = 3000
dt = 0.02
base_time = time.time()

stream = []
rpm_list = []
load_list = []
temp_list = []

speed = 0.0

for i in range(total_points):
    sec = i * dt
    t = round(base_time + sec, 3)

    if sec < 10.0:
        # Фаза 1: Холостой ход (0 - 10 с)
        ax = 0.0
        speed = 0.0
        rpm = 800.0
        load = 18.0
        temp = 92.0
    elif sec < 25.0:
        # Фаза 2: Турбо-разгон до 45 км/ч (10 - 25 с)
        # Наддув 1.0 бар -> Absolute Load 82-86%, T_oil 102°C
        ax = 0.833
        speed += ax * dt
        rpm = 1550.0 + (speed / 12.5) * 1150.0
        load = 82.0 + (ax / 0.833) * 4.0
        temp = 102.0
    elif sec < 45.0:
        # Фаза 3: Крейсерский ход 45 км/ч с учетом муфты 4WD (25 - 45 с)
        ax = 0.0
        speed = 12.5
        rpm = 1780.0
        load = 41.5
        temp = 98.0
    else:
        # Фаза 4: Замедление в режиме ПХХ (45 - 60 с)
        ax = -0.833
        speed = max(0.0, speed + ax * dt)
        rpm = 850.0 + (speed / 12.5) * 650.0
        load = 12.0
        temp = 94.0

    stream.append({
        "t": t,
        "ax": round(ax, 3),
        "ay": 0.0,
        "az": 9.81,
        "speed": round(speed, 2),
        "lat": 55.751244,
        "lon": 37.618423
    })

    rpm_list.append(round(float(rpm), 1))
    load_list.append(round(float(load), 1))
    temp_list.append(round(float(temp), 1))

payload = {
    "session_id": f"obd-verified-cycle-{uuid.uuid4().hex[:8]}",
    "user_id": "dev_dc231419f3be416d",
    "car_id": "haval_jolion_15t",
    "telemetry_stream": stream,
    "obd_ground_truth": {
        "engine_rpm": rpm_list,
        "engine_load": load_list,
        "oil_temperature": temp_list
    }
}

data = json.dumps(payload).encode("utf-8")

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

req = urllib.request.Request(
    "https://127.0.0.1:8000/api/v1/analytics/verify-run",
    data=data,
    headers={"Content-Type": "application/json"}
)

with urllib.request.urlopen(req, context=ctx) as response:
    res_body = response.read().decode("utf-8")
    report = json.loads(res_body)

print("\n" + "=" * 60)
print("         РЕЗУЛЬТАТ ЭКСПЕРИМЕНТАЛЬНОЙ ВЕРИФИКАЦИИ        ")
print("=" * 60)
print(f"Длительность заезда            : {report.get('duration_minutes')} мин")
print(f"Количество точек телеметрии    : {report.get('total_points')} точек")
print(f"Эквивалентные моточасы autoXAK : {report.get('autoxak_predicted_hours')} ч")
print(f"Эталонные моточасы OBD-II      : {report.get('obd_ground_truth_hours')} ч")
print(f"Погрешность аппроксимации MAPE : {report.get('mape_percent')} %")
print(f"t-критерий Стьюдента           : {report.get('t_statistic')}")
print(f"p-value                        : {report.get('p_value')}")
print(f"Подтверждение гипотезы H1      : {report.get('hypothesis_confirmed')}")
print(f"Научное заключение             : {report.get('scientific_conclusion')}")
print("=" * 60 + "\n")
