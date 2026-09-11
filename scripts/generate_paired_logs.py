import json
import time
import numpy as np
import pandas as pd

def generate_validation_dataset(duration_sec: int = 180):
    dt = 0.02
    t_axis = np.arange(0, duration_sec, dt)
    n = len(t_axis)
    
    now = time.time()
    phone_stream = []
    obd_records = []
    
    speed = 0.0
    
    for i, t in enumerate(t_axis):
        # Моделируем 3 фазы: простой на светофоре (0-60 с), разгон (60-120 с), городское движение (120-180 с)
        if t < 60.0:
            speed = 0.0
            ax = float(np.random.normal(0.0, 0.02))
            rpm = float(800.0 + np.random.normal(0, 15))
            load = 18.0
        elif t < 120.0:
            ax = float(np.random.normal(1.9, 0.2))  # Динамичный разгон
            speed = min(60.0, speed + ax * dt * 3.6)
            rpm = float(1800.0 + (speed / 60.0) * 2200.0 + np.random.normal(0, 50))
            load = 65.0
        else:
            ax = float(np.random.normal(-0.3, 0.15))
            speed = max(20.0, speed + ax * dt * 3.6)
            rpm = float(2000.0 + np.random.normal(0, 30))
            load = 35.0
            
        # Запись смартфона
        phone_stream.append({
            "t": round(now + t, 3),
            "ax": round(ax, 3),
            "ay": round(float(np.random.normal(0, 0.05)), 3),
            "az": round(float(9.81 + np.random.normal(0, 0.1)), 3),
            "speed": round(float(speed / 3.6), 2),  # м/с
            "lat": 55.751244,
            "lon": 37.618423
        })
        
        # Запись Car Scanner OBD (опрос с частотой 10 Гц)
        if i % 5 == 0:
            obd_records.append({
                "Time": round(t, 2),
                "Engine RPM": round(rpm, 1),
                "Vehicle Speed": round(speed, 1),
                "Engine Load": round(load, 1)
            })
            
    # Сохраняем JSON телефона
    phone_payload = {
        "session_id": "val_phone_session_001",
        "user_id": "test_user_01",
        "car_id": "test_car_vag_2.0tsi",
        "telemetry_stream": phone_stream
    }
    with open("data/mock_phone_trip.json", "w", encoding="utf-8") as f:
        json.dump(phone_payload, f, indent=2)
        
    # Сохраняем CSV Car Scanner
    df_obd = pd.DataFrame(obd_records)
    df_obd.to_csv("data/mock_car_scanner.csv", sep=";", index=False)
    
    print("[+] Сгенерирована согласованная пара данных:")
    print("    - data/mock_phone_trip.json")
    print("    - data/mock_car_scanner.csv")

if __name__ == "__main__":
    generate_validation_dataset(180)