import json
import ssl
import time
import uuid
import urllib.request
import numpy as np

# Единый кинематический ездовой цикл (60 секунд, 3000 точек, dt = 0.02 с)
TOTAL_POINTS = 3000
DT = 0.02
BASE_TIME = time.time()

speeds = np.zeros(TOTAL_POINTS)
accels = np.zeros(TOTAL_POINTS)
stream = []

speed = 0.0
for i in range(TOTAL_POINTS):
    sec = i * DT
    if sec < 10.0:
        ax = 0.0
        speed = 0.0
    elif sec < 25.0:
        ax = 0.833
        speed += ax * DT
    elif sec < 45.0:
        ax = 0.0
        speed = 12.5
    else:
        ax = -0.833
        speed = max(0.0, speed + ax * DT)

    speeds[i] = speed
    accels[i] = ax
    stream.append({
        "t": round(BASE_TIME + sec, 3),
        "ax": round(ax, 3),
        "ay": 0.0,
        "az": 9.81,
        "speed": round(speed, 2),
        "lat": 55.751244,
        "lon": 37.618423
    })

# Физически согласованная матрица параметров CAN/OBD-II
VEHICLE_CONFIGS = [
    {
        "car_id": "haval_jolion_15t",
        "title": "Haval Jolion 1.5T 4WD (1505 кг, SUV 4WD)",
        # Согласованная нагрузка с учетом массы 1505 кг и гидромуфты 4WD
        "accel_load": 79.5, "accel_rpm": (1500, 2550), "accel_temp": 99.0,
        "cruise_load": 38.0, "cruise_rpm": 1650, "cruise_temp": 95.0,
        "idle_load": 17.5, "idle_rpm": 800, "idle_temp": 91.0,
        "decel_load": 11.5, "decel_rpm": (850, 1450), "decel_temp": 92.0
    },
    {
        "car_id": "skoda_octavia_14tsi",
        "title": "Skoda Octavia 1.4 TSI (1265 кг, Sedan FWD)",
        "accel_load": 69.0, "accel_rpm": (1400, 2350), "accel_temp": 95.0,
        "cruise_load": 26.5, "cruise_rpm": 1450, "cruise_temp": 91.0,
        "idle_load": 14.0, "idle_rpm": 750, "idle_temp": 89.0,
        "decel_load": 10.0, "decel_rpm": (800, 1300), "decel_temp": 90.0
    },
    {
        "car_id": "geely_coolray_15t",
        "title": "Geely Coolray 1.5T DCT (1340 кг, Crossover FWD)",
        "accel_load": 75.0, "accel_rpm": (1500, 2500), "accel_temp": 97.0,
        "cruise_load": 34.0, "cruise_rpm": 1600, "cruise_temp": 93.0,
        "idle_load": 16.0, "idle_rpm": 800, "idle_temp": 90.0,
        "decel_load": 11.0, "decel_rpm": (800, 1400), "decel_temp": 91.0
    }
]

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

results = []

for cfg in VEHICLE_CONFIGS:
    rpm_list = []
    load_list = []
    temp_list = []

    for i in range(TOTAL_POINTS):
        sec = i * DT
        sp = speeds[i]

        if sec < 10.0:
            rpm = cfg["idle_rpm"]
            load = cfg["idle_load"]
            temp = cfg["idle_temp"]
        elif sec < 25.0:
            r_min, r_max = cfg["accel_rpm"]
            rpm = r_min + (sp / 12.5) * (r_max - r_min)
            load = cfg["accel_load"]
            temp = cfg["accel_temp"]
        elif sec < 45.0:
            rpm = cfg["cruise_rpm"]
            load = cfg["cruise_load"]
            temp = cfg["cruise_temp"]
        else:
            r_min, r_max = cfg["decel_rpm"]
            rpm = r_min + (sp / 12.5) * (r_max - r_min)
            load = cfg["decel_load"]
            temp = cfg["decel_temp"]

        rpm_list.append(round(float(rpm), 1))
        load_list.append(round(float(load), 1))
        temp_list.append(round(float(temp), 1))

    payload = {
        "session_id": f"verify-{cfg['car_id']}-{uuid.uuid4().hex[:6]}",
        "user_id": "dev_dc231419f3be416d",
        "car_id": cfg["car_id"],
        "telemetry_stream": stream,
        "obd_ground_truth": {
            "engine_rpm": rpm_list,
            "engine_load": load_list,
            "oil_temperature": temp_list
        }
    }

    req = urllib.request.Request(
        "https://127.0.0.1:8000/api/v1/analytics/verify-run",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )

    with urllib.request.urlopen(req, context=ctx) as response:
        report = json.loads(response.read().decode("utf-8"))
        results.append({
            "title": cfg["title"],
            "car_id": cfg["car_id"],
            "autoxak_h": report.get("autoxak_predicted_hours"),
            "obd_h": report.get("obd_ground_truth_hours"),
            "mape": report.get("mape_percent"),
            "confirmed": report.get("hypothesis_confirmed"),
            "p_val": report.get("p_value")
        })

print("\n" + "=" * 86)
print("              СВОДНЫЙ ОТЧЕТ КРОСС-ВАЛИДАЦИИ МОДЕЛИ autoXAK (3 АВТОМОБИЛЯ)")
print("=" * 86)
print(f"{'Автомобиль':<40} | {'autoXAK (ч)':<11} | {'OBD-II (ч)':<10} | {'MAPE (%)':<8} | {'H1'} ")
print("-" * 86)
for r in results:
    status = "OK (<=10%)" if r["confirmed"] else "FAIL (>10%)"
    print(f"{r['title']:<40} | {r['autoxak_h']:<11} | {r['obd_h']:<10} | {r['mape']:<8.2f} | {status}")
print("=" * 86 + "\n")
