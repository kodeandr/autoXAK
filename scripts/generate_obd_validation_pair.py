import json
import numpy as np

def generate_paired_dataset(output_path: str = "data/validation_trip_with_obd.json"):
    """
    Генерирует согласованную пару данных: 
    1. Мобильная телеметрия (акселерометр + GPS + 2GIS координаты)
    2. Аппаратный лог CAN-шины (RPM + Нагрузка на двигатель + Температура масла)
    """
    duration_sec = 600  # 10 минут городской поездки
    dt = 0.02           # 50 Гц
    n_points = int(duration_sec / dt)
    
    speed = 0.0
    telemetry = []
    obd_rpm = []
    obd_load = []
    obd_temp = []

    for i in range(n_points):
        # Циклы городского разгона и светофорных остановок
        phase = (i // 1000) % 3
        if phase == 0:  # Разгон
            acc = float(np.clip(np.random.normal(1.2, 0.2), 0.0, 2.0))
            rpm = float(1200 + acc * 800 + np.random.normal(0, 50))
            load = float(45.0 + acc * 20.0)
        elif phase == 1:  # Крейсерский ход
            acc = float(np.random.normal(0.0, 0.1))
            rpm = float(1600 + np.random.normal(0, 30))
            load = 28.0
        else:  # Остановка на светофоре / пробка
            acc = float(np.clip(np.random.normal(-1.2, 0.2), -2.5, 0.0)) if speed > 0.5 else 0.0
            rpm = 800.0 + float(np.random.normal(0, 15))
            load = 18.0

        speed = max(0.0, speed + acc * dt)

        telemetry.append({
            "t": round(i * dt, 3),
            "ax": round(acc, 3),
            "ay": round(float(np.random.normal(0, 0.05)), 3),
            "az": 9.81,
            "speed": round(speed, 2),
            "lat": 55.751244 + (i * 0.00001),
            "lon": 37.618423 + (i * 0.00001)
        })

        obd_rpm.append(round(rpm, 1))
        obd_load.append(round(load, 1))
        obd_temp.append(96.5)  # Рабочая температура масла

    payload = {
        "session_id": "val-session-mfti-001",
        "user_id": "researcher_mfti",
        "car_id": "tiguan_2.0tsi_stage1",
        "telemetry_stream": telemetry,
        "obd_ground_truth": {
            "engine_rpm": obd_rpm,
            "engine_load": obd_load,
            "oil_temperature": obd_temp
        }
    }

    with open(output_path, "w") as f:
        json.dump(payload, f)
    print(f"Сгенерирован валидационный датасет: {output_path} ({n_points} точек, 10 минут)")

if __name__ == "__main__":
    generate_paired_dataset()