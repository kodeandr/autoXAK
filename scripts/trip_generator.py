import json
import time
import numpy as np

def generate_synthetic_trip(profile: str = "calm", duration_sec: int = 300) -> dict:
    dt = 0.02  # Частота 50 Гц (шаг 20 мс)
    timestamps = np.arange(0, duration_sec, dt)
    n_samples = len(timestamps)

    current_time = time.time()
    telemetry = []
    speed = 0.0

    for i, t in enumerate(timestamps):
        # Моделируем профиль ускорения
        if profile == "calm":
            base_acc = np.random.normal(0.2, 0.3) if speed < 16.0 else np.random.normal(-0.2, 0.3)
            base_acc = np.clip(base_acc, -1.5, 1.5)
        elif profile == "aggressive":
            # Резкие разгоны и резкие торможения
            base_acc = np.random.normal(1.2, 0.8) if speed < 22.0 else np.random.normal(-1.5, 0.8)
            base_acc = np.clip(base_acc, -3.5, 3.0)
        else:  # "traffic_jam" (пробка)
            base_acc = np.random.normal(0.0, 0.5) if (i // 500) % 2 == 0 else 0.0
            base_acc = np.clip(base_acc, -1.0, 1.0)

        speed = max(0.0, speed + base_acc * dt)

        # Накладываем высокочастотный шум подвески
        noise = np.random.normal(0, 0.15)

        telemetry.append({
            "t": round(current_time + t, 3),
            "ax": round(float(base_acc + noise), 4),
            "ay": round(float(np.random.normal(0, 0.1)), 4),
            "az": round(float(9.81 + noise), 4),
            "speed": round(float(speed), 2)
        })

    return {
        "session_id": "00000000-0000-0000-0000-000000000001",
        "user_id": "test_user_01",
        "car_id": "test_car_vag_2.0tsi",
        "telemetry_stream": telemetry
    }

if __name__ == "__main__":
    calm_data = generate_synthetic_trip("calm", 180)
    with open("data/sample_calm_trip.json", "w") as f:
        json.dump(calm_data, f, indent=2)
    print("Сгенерирован файл data/sample_calm_trip.json (180 сек, 50 Гц)")